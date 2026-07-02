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
            try:
                result = await build_fn()
            except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
                record.state = "failed"
                record.error = str(exc)
                raise
            record.state = "ready"
            record.result = result
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
