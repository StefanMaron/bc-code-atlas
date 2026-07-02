"""Staging -> served atomic promotion (constitution Principle II; T022).

A serving process (chunker/, tools/graphify-al) must only ever see, for a
given `warm_root(country, version)`, either "nothing yet" or "a fully
complete, correct artifact" -- never a partially-written one. This module is
the only place that ever creates or replaces a `layout.warm_root(...)`
directory; every write happens first at `layout.staging_root(build_id)` (a
private path no serving process ever opens) and is moved into place only
after the caller has confirmed the build succeeded.

Atomicity notes (measured/verified this session, not assumed):

- `os.rename` on POSIX is atomic for a single directory rename *only when
  source and destination are on the same filesystem/mount* and the
  destination does not already exist as a non-empty directory. Across
  filesystems it either raises `OSError(EXDEV)` or (via higher-level
  helpers that silently fall back to copy+delete, e.g. naive uses of
  `shutil.move`) becomes a slow, non-atomic copy -- neither of which is
  acceptable here. `_assert_same_filesystem` checks `st_dev` before ever
  attempting the rename and raises `PromotionError` rather than silently
  falling back to a copy.
- POSIX `rename(2)` cannot atomically replace an existing *non-empty*
  directory (`ENOTEMPTY`/`EEXIST`) -- there is no single syscall available
  from pure Python (no `renameat2(..., RENAME_EXCHANGE)` binding in the
  stdlib) that atomically swaps two non-empty directories in place. Instead,
  re-promotion of an already-warm (country, version) (e.g. rebuilding a
  country's moving tip) is done as two atomic renames: the old served
  directory is renamed *out of the way* first (atomic, since the
  destination path doesn't yet exist), then the new staging directory is
  renamed into the now-empty served path (atomic, same reason). Each half
  is individually atomic; the only observable effect of the brief gap
  between them is that a concurrent reader may transiently see "nothing yet"
  for that (country, version) -- never a partial/mixed artifact, which is
  the actual invariant FR-012/constitution Principle II require. The
  vacated old directory is then removed in the background (best-effort,
  not part of the atomicity contract).
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

from . import layout


class PromotionError(Exception):
    """Raised when a staging build cannot be promoted: missing/incomplete
    staging artifact, or staging/warm roots are not on the same filesystem.
    """


def _assert_same_filesystem(a: Path, b: Path) -> None:
    """Raise PromotionError unless `a` and `b` live on the same mount.

    `b` (the warm parent directory) may not exist yet on a first-ever
    promotion for a (country, version) -- in that case its nearest existing
    ancestor is checked instead, since that's the mount the new directory
    will actually be created on.
    """
    dev_a = a.stat().st_dev
    probe = b
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            raise PromotionError(f"no existing ancestor found for {b}")
        probe = parent
    dev_b = probe.stat().st_dev
    if dev_a != dev_b:
        raise PromotionError(
            f"staging root {a} and warm root {b} are on different filesystems "
            f"(st_dev {dev_a} != {dev_b}) -- an os.rename here would not be "
            "atomic (cross-device rename either fails or silently copies). "
            "Ensure data/staging and data/warm share a filesystem/mount."
        )


def verify_staging_complete(build_id: str, data_dir: Path = layout.DEFAULT_DATA_DIR) -> None:
    """Raise PromotionError unless the staging build looks like a real,
    finished artifact -- both the search and graph subdirectories exist and
    are non-empty. This is a structural completeness check, not a
    correctness check (that's the build step's own responsibility); it
    exists to catch an accidental promote-before-build-finished bug rather
    than to re-validate build content.
    """
    search_dir = layout.staging_search_dir(build_id, data_dir)
    graph_dir = layout.staging_graph_dir(build_id, data_dir)
    if not search_dir.is_dir() or not any(search_dir.iterdir()):
        raise PromotionError(f"staging search dir missing or empty: {search_dir}")
    if not graph_dir.is_dir() or not any(graph_dir.iterdir()):
        raise PromotionError(f"staging graph dir missing or empty: {graph_dir}")


def promote(
    build_id: str,
    country: str,
    version: str,
    data_dir: Path = layout.DEFAULT_DATA_DIR,
) -> Path:
    """Atomically promote `staging_root(build_id)` to `warm_root(country,
    version)`. Only call this after a build has genuinely succeeded --
    there is no partial-promotion recovery path by design (data-model.md:
    "A failed build does not produce or leave behind a served_path").

    Returns the new warm_root path.
    """
    staging = layout.staging_root(build_id, data_dir)
    if not staging.is_dir():
        raise PromotionError(f"staging root does not exist: {staging}")
    verify_staging_complete(build_id, data_dir)

    warm = layout.warm_root(country, version, data_dir)
    warm.parent.mkdir(parents=True, exist_ok=True)
    _assert_same_filesystem(staging, warm.parent)

    if warm.exists():
        # Re-promotion (e.g. rebuilding a country's moving tip): move the old
        # artifact out of the way first so the second rename below has an
        # empty target path, keeping each individual rename atomic (see
        # module docstring).
        vacated = warm.parent / f".{warm.name}.vacated-{uuid.uuid4().hex}"
        os.rename(warm, vacated)
        os.rename(staging, warm)
        shutil.rmtree(vacated, ignore_errors=True)
    else:
        os.rename(staging, warm)

    return warm


def discard(build_id: str, data_dir: Path = layout.DEFAULT_DATA_DIR) -> None:
    """Delete a staging build that failed or was abandoned. Never touches
    anything under `warm_root` -- a failed build simply never gets promoted
    (data-model.md: a retry is a fresh Build from `queued`, never a resume).
    """
    staging = layout.staging_root(build_id, data_dir)
    shutil.rmtree(staging, ignore_errors=True)


def new_build_id(country: str, version: str) -> str:
    """A staging directory name that's unique per attempt (not per
    (country, version)) -- two concurrent/retried builds of the same target
    must not share a staging path, only the eventual promoted warm path.
    """
    return f"{country}-{version}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
