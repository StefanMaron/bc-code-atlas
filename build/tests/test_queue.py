"""Real, concurrent `asyncio` tests for `build.queue.BuildQueue` (T026):
in-flight request coalescing (FR-017) and bounded concurrency.

No mocking of asyncio itself -- every test spins up real concurrent
`asyncio.create_task`/`asyncio.gather` calls against a real `BuildQueue`
instance and asserts on real observed behavior (call counts, overlap
windows), not on code structure.
"""
from __future__ import annotations

import asyncio

from build.queue import BuildQueue


def test_coalesces_concurrent_duplicate_requests() -> None:
    """Five concurrent request_build() calls for the SAME key must result
    in build_fn actually running exactly once, and every caller must
    observe the same BuildRecord (FR-017, spec Edge Cases "mid-build
    requests").
    """

    async def scenario() -> None:
        queue = BuildQueue(max_concurrent=2)
        call_count = 0
        started = asyncio.Event()

        async def build_fn() -> str:
            nonlocal call_count
            call_count += 1
            started.set()
            await asyncio.sleep(0.1)
            return "built"

        async def caller() -> object:
            return await queue.request_build(("w1", "abc123"), build_fn)

        records = await asyncio.gather(*(caller() for _ in range(5)))

        # All five callers attached to the exact same in-flight record.
        assert len({id(r) for r in records}) == 1
        assert records[0].task is not None
        result = await records[0].task
        assert result == "built"
        assert call_count == 1, "build_fn must run exactly once despite 5 concurrent requests"
        assert records[0].state == "ready"

    asyncio.run(scenario())


def test_new_build_after_ready_is_a_fresh_build() -> None:
    """A retry/re-request for a key that already finished (ready or
    failed) must start a genuinely new build, never resume/reuse the old
    one (data-model.md: "a retry is a new Build from queued").
    """

    async def scenario() -> None:
        queue = BuildQueue(max_concurrent=1)
        call_count = 0

        async def build_fn() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        key = ("w1", "deadbeef")
        first = await queue.request_build(key, build_fn)
        await first.task
        assert first.state == "ready"

        second = await queue.request_build(key, build_fn)
        await second.task

        assert first is not second
        assert call_count == 2

    asyncio.run(scenario())


def test_failed_build_is_reported_and_retryable() -> None:
    async def scenario() -> None:
        queue = BuildQueue(max_concurrent=1)
        attempt = 0

        async def flaky_build_fn() -> str:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("boom")
            return "ok"

        key = ("w1", "flaky")
        record = await queue.request_build(key, flaky_build_fn)
        try:
            await record.task
        except RuntimeError:
            pass
        assert record.state == "failed"
        assert record.error == "boom"
        assert queue.status(key) == "failed"

        retry = await queue.request_build(key, flaky_build_fn)
        result = await retry.task
        assert result == "ok"
        assert retry.state == "ready"

    asyncio.run(scenario())


def test_status_unknown_for_never_requested_key() -> None:
    queue = BuildQueue()
    assert queue.status(("zz", "neverseen")) == "unknown"


def test_bounded_concurrency_never_exceeds_max() -> None:
    """5 DIFFERENT keys against max_concurrent=2 must never have more than
    2 build_fns actually running (inside the semaphore) at the same time --
    the rest must sit queued.
    """

    async def scenario() -> None:
        max_concurrent = 2
        queue = BuildQueue(max_concurrent=max_concurrent)
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def build_fn(n: int) -> int:
            nonlocal current, peak
            async with lock:
                current += 1
                peak = max(peak, current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return n

        async def caller(n: int) -> object:
            return await queue.request_build(("w1", f"key-{n}"), lambda n=n: build_fn(n))

        records = await asyncio.gather(*(caller(n) for n in range(5)))
        await asyncio.gather(*(r.task for r in records))

        assert peak <= max_concurrent, f"observed {peak} concurrent builds, budget was {max_concurrent}"
        assert peak >= 1
        assert {r.result for r in records} == set(range(5))

    asyncio.run(scenario())


def test_in_flight_keys_reflects_queued_and_in_progress_only() -> None:
    async def scenario() -> None:
        queue = BuildQueue(max_concurrent=1)
        release = asyncio.Event()

        async def slow_build_fn() -> str:
            await release.wait()
            return "done"

        key = ("w1", "slow")
        record = await queue.request_build(key, slow_build_fn)
        await asyncio.sleep(0)  # let the task actually start
        assert key in queue.in_flight_keys()

        release.set()
        await record.task
        assert key not in queue.in_flight_keys()

    asyncio.run(scenario())
