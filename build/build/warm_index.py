"""List of currently-warm (country, version) pairs -- lets a caller see
what's already built and instantly queryable before deciding whether to
wait for a fresh `bcatlas_request_version` build, or just use a nearby
warm version that's good enough.

Reuses `eviction.scan_warm_entries` (the same filesystem-derived source of
truth the LRU sweep uses -- no new state) and resolves each entry's
human-readable `version_string` via a real commit-message lookup
(`registry.git_ops.commit_message`), not the AL source tree's own
`version.txt` -- that file is upstream BC source content (the shipped app
manifest version), not our commit_sha, and isn't guaranteed to reflect the
build's actual target commit for an incremental (clone-then-patch) build
whose diff didn't happen to touch it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from registry import git_ops

from . import layout
from .eviction import scan_warm_entries


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def list_warm_versions(
    data_dir: Path = layout.DEFAULT_DATA_DIR,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
    country: str | None = None,
    *,
    resolve_version_string: Callable[[str, Path, str], str] = git_ops.commit_message,
) -> list[dict]:
    """Every warm entry (optionally filtered to one `country`), newest
    (`last_accessed_at`) first within each country. `resolve_version_string`
    is injectable so tests can avoid a real network call per entry.
    """
    entries = scan_warm_entries(data_dir)
    if country is not None:
        entries = [e for e in entries if e.country == country]
    entries = sorted(entries, key=lambda e: (e.country, -e.last_accessed_at))

    results = []
    for e in entries:
        try:
            version_string = resolve_version_string(e.version, mirror_dir, upstream_url)
        except git_ops.GitOpsError:
            version_string = None
        results.append(
            {
                "country": e.country,
                "commit_sha": e.version,
                "version_string": version_string,
                "size_bytes": e.size_bytes,
                "last_touched": _iso(e.last_accessed_at),
            }
        )
    return results
