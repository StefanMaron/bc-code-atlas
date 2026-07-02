"""Pure path-computation for the build/ project's warm and staging directory
conventions (plan.md Project Structure, data-model.md's `Build` and
`WarmResidencyEntry` entities).

No I/O side effects -- these functions only build `Path` objects, never
create/rename/delete anything. Callers (`promote.py`, `eviction.py`,
`incremental.py`) own all filesystem mutation; keeping the naming
convention here, and only here, means it has exactly one place to change
and is trivially unit-testable without touching a filesystem.
"""
from __future__ import annotations

from pathlib import Path

# build/build/layout.py -> parents[2] is the repo root (build/build -> build
# -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _REPO_ROOT / "data"

WARM_SUBDIR = "warm"
STAGING_SUBDIR = "staging"

# The two artifact kinds every (country, version) directory holds -- see
# plan.md Project Structure and data-model.md's WarmResidencyEntry:
# cocoindex-code's own project directory ("search") and graphify-al's
# extracted graph.json + supporting state ("graph").
SEARCH_SUBDIR = "search"
GRAPH_SUBDIR = "graph"


def warm_root(country: str, version: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """`data/warm/<country>/<version>` -- the served-path root for one
    (country, version) pair. This is the only path a serving process ever
    opens for that pair (constitution Principle II).
    """
    return data_dir / WARM_SUBDIR / country / version


def warm_search_dir(country: str, version: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """`data/warm/<country>/<version>/search` -- cocoindex-code's project_root."""
    return warm_root(country, version, data_dir) / SEARCH_SUBDIR


def warm_graph_dir(country: str, version: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """`data/warm/<country>/<version>/graph` -- graphify-al's graph_path parent."""
    return warm_root(country, version, data_dir) / GRAPH_SUBDIR


def staging_root(build_id: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """`data/staging/<build-id>` -- a single build's private write target,
    never opened by a serving process. Promoted to `warm_root(...)` via
    atomic rename only after the build succeeds (constitution Principle
    II; build/promote.py).
    """
    return data_dir / STAGING_SUBDIR / build_id


def staging_search_dir(build_id: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return staging_root(build_id, data_dir) / SEARCH_SUBDIR


def staging_graph_dir(build_id: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return staging_root(build_id, data_dir) / GRAPH_SUBDIR
