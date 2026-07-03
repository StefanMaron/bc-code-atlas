"""Bounded-concurrency build queue with in-flight request coalescing (T023;
FR-017, spec Edge Cases "mid-build requests").

Deliberately domain-agnostic: this module knows nothing about (country,
version), `ccc index`, or graphify-al -- it coalesces and bounds concurrency
for *any* keyed, awaitable unit of work. `build/build/mcp_server.py` supplies
the actual (country, commit_sha) key and an `incremental.build_version` +
`promote.promote` closure as the work function. Keeping this generic is what
makes it independently, realistically testable under real concurrent
`asyncio` tasks (T026) without needing a GPU, a git mirror, or cocoindex-code
in the loop.

Coalescing mechanism: a plain `dict[key, BuildRecord]` plus an `asyncio.Task`
per record. `asyncio.Task` is itself the "singleflight" primitive here --
it's awaitable multiple times (every awaiter gets the same result or the
same exception once it completes), so a second caller for a key that's
already queued/in-progress simply gets handed the existing record and can
`await record.task` if it wants to block for the result; `request_build`
itself never blocks on completion, since FR-011 requires an immediate
acknowledgment distinct from final results.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import Any

# GPU-bound, bursty work (README's "GPU vs CPU" section: full reindex ~3min
# GPU vs ~20hr CPU) -- default to a single build at a time unless configured
# otherwise. Configuration, not a hardcoded architectural limit (constitution
# Principle IV: bounded *residency*/concurrency by config, not by design).
DEFAULT_MAX_CONCURRENT_BUILDS = int(os.environ.get("BCATLAS_BUILD_MAX_CONCURRENT", "1"))

BuildState = str  # "queued" | "in_progress" | "ready" | "failed"


@dataclass
class BuildRecord:
    key: Hashable
    state: BuildState
    requested_at: float
    task: "asyncio.Task[Any] | None" = None
    error: str | None = None
    result: Any = None
    # How many callers have attached to this record via request_build,
    # including the one that started it -- purely observability, not used
    # for any control-flow decision.
    attach_count: int = field(default=1)
    # Set when the record leaves "queued" (started_at) and when it leaves
    # "in_progress" successfully (finished_at) -- feeds BuildQueue's ETA
    # estimation. Both None until then; finished_at stays None on failure
    # (a failed build's duration isn't a representative sample of real work).
    started_at: float | None = None
    finished_at: float | None = None


# How many of the most recent successfully-completed builds' durations to
# keep for ETA estimation -- a small rolling window (not a running average
# over the whole process lifetime) so the estimate tracks recent conditions
# (e.g. a run of cheap incremental builds vs. one cold one) rather than
# being dragged by history.
_DURATION_HISTORY_SIZE = 10


class BuildQueue:
    """Bounded-concurrency, coalescing build queue.

    `max_concurrent` builds actually run at once (an `asyncio.Semaphore`
    gate inside `_run`); everything else sits in `queued` state until a slot
    frees up. Requests for a key that's already `queued` or `in_progress`
    attach to the existing `BuildRecord` instead of starting a duplicate
    (FR-017) -- a *new* `BuildRecord` (and a real re-run of `build_fn`) is
    only ever created for a key that is unknown, or previously `ready`/
    `failed` (data-model.md: retries are a fresh Build, never a resume).
    """

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT_BUILDS) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._records: dict[Hashable, BuildRecord] = {}
        # Most recent successful build durations (seconds), newest last,
        # capped at _DURATION_HISTORY_SIZE -- see average_duration().
        self._recent_durations: list[float] = []
        # Guards read-modify-write of self._records so two concurrent
        # request_build calls for the same brand-new key can't both decide
        # "not present, I'll create it" and race each other into starting
        # two builds. There is no `await` between the dict read and the
        # dict write below, so this could in principle rely on asyncio's
        # single-threaded cooperative scheduling alone -- the explicit lock
        # is kept anyway so this stays correct even if that ever changes
        # (e.g. a future `await` is added to the decision logic).
        self._lock = asyncio.Lock()

    async def request_build(
        self,
        key: Hashable,
        build_fn: Callable[[], Awaitable[Any]],
    ) -> BuildRecord:
        """Start a build for `key`, or attach to one already queued/in
        in-flight for the same key. Returns immediately with the current
        `BuildRecord` -- never awaits build completion itself (FR-011).
        Callers that do want the result can `await record.task`.
        """
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing.state in ("queued", "in_progress"):
                existing.attach_count += 1
                return existing

            record = BuildRecord(key=key, state="queued", requested_at=time.time())
            self._records[key] = record
            record.task = asyncio.ensure_future(self._run(record, build_fn))
            return record

    async def _run(self, record: BuildRecord, build_fn: Callable[[], Awaitable[Any]]) -> Any:
        async with self._semaphore:
            record.state = "in_progress"
            record.started_at = time.time()
            try:
                result = await build_fn()
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
                record.state = "failed"
                record.error = str(exc)
                raise
            record.finished_at = time.time()
            record.state = "ready"
            record.result = result
            self._recent_durations.append(record.finished_at - record.started_at)
            del self._recent_durations[:-_DURATION_HISTORY_SIZE]
            return result

    def status(self, key: Hashable) -> BuildState | str:
        """`"unknown"` for a key never requested (contract:
        `bcatlas_version_status`), else the record's current state.
        """
        record = self._records.get(key)
        return record.state if record is not None else "unknown"

    def get(self, key: Hashable) -> BuildRecord | None:
        return self._records.get(key)

    def in_flight_keys(self) -> set[Hashable]:
        """Keys currently `queued` or `in_progress` -- used by eviction.py
        callers to protect an in-flight build's base sibling from reclaim.
        """
        return {k for k, r in self._records.items() if r.state in ("queued", "in_progress")}

    def average_duration(self) -> float | None:
        """Mean of the last `_DURATION_HISTORY_SIZE` successful build
        durations (seconds), or `None` if none have completed yet this
        process's lifetime -- callers must handle `None` (no ETA to give,
        not a zero-second one).
        """
        if not self._recent_durations:
            return None
        return sum(self._recent_durations) / len(self._recent_durations)

    @property
    def duration_sample_count(self) -> int:
        return len(self._recent_durations)

    def builds_ahead(self, key: Hashable) -> int | None:
        """How many other `queued`/`in_progress` records were requested
        before `key`'s -- `0` for the record currently running (nothing
        left to wait on), `None` if `key` is unknown or already terminal
        (`ready`/`failed`) -- position doesn't apply to those.
        """
        record = self._records.get(key)
        if record is None or record.state not in ("queued", "in_progress"):
            return None
        return sum(
            1
            for r in self._records.values()
            if r.state in ("queued", "in_progress") and r.requested_at < record.requested_at
        )

    def estimate_seconds_remaining(self, key: Hashable) -> float | None:
        """Rough ETA (seconds from now) for `key` to reach `ready`, via a
        simple list-scheduling simulation over `max_concurrent` slots using
        `average_duration()` as every build's expected length. `None` if
        `key` is unknown/terminal, or no historical duration exists yet to
        estimate from -- genuinely coarse (real builds vary cold-vs-
        incremental by roughly an order of magnitude, see IDEAS.md), meant
        as a rough "is this minutes or an hour" signal, not a precise ETA.
        """
        record = self._records.get(key)
        if record is None or record.state not in ("queued", "in_progress"):
            return None
        avg = self.average_duration()
        if avg is None:
            return None

        now = time.time()
        if record.state == "in_progress":
            return max(avg - (now - record.started_at), 0.0)

        in_progress = sorted(
            (r for r in self._records.values() if r.state == "in_progress"),
            key=lambda r: r.started_at,
        )
        slot_free_at = [max(avg - (now - r.started_at), 0.0) for r in in_progress]
        slot_free_at += [0.0] * max(0, self.max_concurrent - len(slot_free_at))

        queued = sorted(
            (r for r in self._records.values() if r.state == "queued"),
            key=lambda r: r.requested_at,
        )
        for r in queued:
            slot = min(range(len(slot_free_at)), key=lambda i: slot_free_at[i])
            slot_free_at[slot] += avg
            if r.key == key:
                return slot_free_at[slot]
        return None  # unreachable: `record` is in `queued` by the state check above
