"""LRU/TTL sweep over warm (country, version) artifacts under a disk budget
(T025; FR-015, FR-016, spec Acceptance Scenario 5-6).

No new persistent database is introduced for `WarmResidencyEntry` (per
data-model.md's header note and constitution Principle V's "measure, don't
assume" -- there's no need for one). `last_accessed_at` and `size_bytes` are
derived directly from the filesystem: `warm_root(country, version)`'s own
mtime as a last-access proxy (a real "touch on every read" is a serving-side
concern in chunker/tools/graphify-al, out of scope for this task per the
brief -- this sweep only *reads* whatever mtime is already there), and a
real recursive `du`-style byte sum for size.

Eviction is always safe (constitution Principle III: historical versions are
immutable, so a reclaimed (country, version) is always rebuildable from the
same immutable upstream commit) -- this module only ever deletes already-
promoted `warm_root` directories, never `staging_root` ones (that's
`promote.discard`'s job for a *failed* build, a different lifecycle event).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import layout

# Configuration, not an architectural constant (constitution Principle IV) --
# override via env var. Default picked as a reasonable single-host budget;
# real deployments should size this to actual disk.
DEFAULT_BUDGET_BYTES = int(os.environ.get("BCATLAS_WARM_DISK_BUDGET_BYTES", str(50 * 1024**3)))


@dataclass(frozen=True)
class WarmEntry:
    country: str
    version: str
    served_path: Path
    last_accessed_at: float  # POSIX mtime seconds
    size_bytes: int


def _dir_size(path: Path) -> int:
    """Real recursive byte sum of every regular file under `path` (du-style,
    not `st_blocks`-based -- apparent size, which is what actually matters
    for "how much of the configured budget does this consume").
    """
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat().st_size
            except OSError:
                # Deleted/broken symlink mid-walk -- don't let one bad entry
                # abort the whole size computation.
                continue
    return total


def scan_warm_entries(data_dir: Path = layout.DEFAULT_DATA_DIR) -> list[WarmEntry]:
    """Every currently-promoted (country, version) directory under
    `data_dir/warm/`, with real filesystem-derived size/last-access.
    """
    warm_base = data_dir / layout.WARM_SUBDIR
    entries: list[WarmEntry] = []
    if not warm_base.is_dir():
        return entries
    for country_dir in sorted(p for p in warm_base.iterdir() if p.is_dir()):
        for version_dir in sorted(p for p in country_dir.iterdir() if p.is_dir()):
            try:
                mtime = version_dir.stat().st_mtime
            except OSError:
                continue
            entries.append(
                WarmEntry(
                    country=country_dir.name,
                    version=version_dir.name,
                    served_path=version_dir,
                    last_accessed_at=mtime,
                    size_bytes=_dir_size(version_dir),
                )
            )
    return entries


def evict(
    data_dir: Path = layout.DEFAULT_DATA_DIR,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
    protected: frozenset[tuple[str, str]] = frozenset(),
    entries: list[WarmEntry] | None = None,
) -> list[WarmEntry]:
    """Reclaim warm directories beyond `budget_bytes`, oldest
    `last_accessed_at` first, skipping any `(country, version)` present in
    `protected` (an in-flight build's `base_sibling` -- see
    `queue.BuildQueue.in_flight_keys` / `incremental.BaseSelection`; must
    never be deleted mid-clone, spec Edge Cases).

    `entries` lets a caller pass an already-scanned list (e.g. to evict
    against a snapshot taken before/after some other check) -- defaults to a
    fresh `scan_warm_entries(data_dir)`.

    Returns the list of entries actually removed, oldest-first (the order
    they were deleted in).
    """
    if entries is None:
        entries = scan_warm_entries(data_dir)

    total = sum(e.size_bytes for e in entries)
    if total <= budget_bytes:
        return []

    candidates = sorted(
        (e for e in entries if (e.country, e.version) not in protected),
        key=lambda e: e.last_accessed_at,
    )

    removed: list[WarmEntry] = []
    for entry in candidates:
        if total <= budget_bytes:
            break
        _remove_served_path(entry.served_path)
        total -= entry.size_bytes
        removed.append(entry)
    return removed


def _remove_served_path(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
