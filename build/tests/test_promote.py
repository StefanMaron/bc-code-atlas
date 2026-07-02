"""Real-filesystem tests for `build.promote` (T022 support): staging is
never promoted until it looks complete, promotion is a real atomic
directory rename, and re-promotion of an already-warm (country, version)
(e.g. rebuilding a country's moving tip) replaces it cleanly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from build import layout, promote


def _make_staging(data_dir: Path, build_id: str, *, complete: bool = True) -> None:
    search_dir = layout.staging_search_dir(build_id, data_dir)
    search_dir.mkdir(parents=True)
    (search_dir / "Some Object.al").write_text("codeunit 1 X { }")
    if complete:
        graph_dir = layout.staging_graph_dir(build_id, data_dir)
        graph_dir.mkdir(parents=True)
        (graph_dir / "graph.json").write_text("{}")


def test_promote_moves_staging_to_warm(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_staging(data_dir, "build-1")

    warm = promote.promote("build-1", "w1", "28.1", data_dir=data_dir)

    assert warm == layout.warm_root("w1", "28.1", data_dir)
    assert (warm / "search" / "Some Object.al").is_file()
    assert (warm / "graph" / "graph.json").is_file()
    assert not layout.staging_root("build-1", data_dir).exists()


def test_promote_refuses_incomplete_staging(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_staging(data_dir, "build-2", complete=False)  # no graph dir

    with pytest.raises(promote.PromotionError):
        promote.promote("build-2", "w1", "28.1", data_dir=data_dir)

    # Never left a partial artifact at the warm path.
    assert not layout.warm_root("w1", "28.1", data_dir).exists()


def test_promote_refuses_missing_staging(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with pytest.raises(promote.PromotionError):
        promote.promote("no-such-build", "w1", "28.1", data_dir=data_dir)


def test_reader_never_sees_partial_artifact_during_reject(tmp_path: Path) -> None:
    """If verify_staging_complete rejects, warm_root must not exist at all
    afterward -- "nothing yet", never a half-written directory."""
    data_dir = tmp_path / "data"
    _make_staging(data_dir, "build-3", complete=False)
    try:
        promote.promote("build-3", "w1", "28.1", data_dir=data_dir)
    except promote.PromotionError:
        pass
    assert not layout.warm_root("w1", "28.1", data_dir).exists()


def test_re_promotion_replaces_existing_warm_directory(tmp_path: Path) -> None:
    """Rebuilding a country's moving tip: promoting a second staging build
    for the SAME (country, version) must atomically replace the old one,
    never leave both or neither."""
    data_dir = tmp_path / "data"
    _make_staging(data_dir, "build-4a")
    promote.promote("build-4a", "w1", "tip", data_dir=data_dir)
    warm = layout.warm_root("w1", "tip", data_dir)
    assert (warm / "search" / "Some Object.al").read_text() == "codeunit 1 X { }"

    _make_staging(data_dir, "build-4b")
    (layout.staging_search_dir("build-4b", data_dir) / "Some Object.al").write_text("codeunit 1 X { NEW }")
    promote.promote("build-4b", "w1", "tip", data_dir=data_dir)

    assert (warm / "search" / "Some Object.al").read_text() == "codeunit 1 X { NEW }"
    # No leftover vacated/staging directories.
    assert list(data_dir.glob(".*vacated*")) == []
    assert not layout.staging_root("build-4a", data_dir).exists()
    assert not layout.staging_root("build-4b", data_dir).exists()


def test_discard_removes_staging_and_never_touches_warm(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_staging(data_dir, "build-5")
    promote.discard("build-5", data_dir=data_dir)
    assert not layout.staging_root("build-5", data_dir).exists()
    assert not layout.warm_root("w1", "28.1", data_dir).exists()


def test_new_build_id_is_unique_per_call() -> None:
    ids = {promote.new_build_id("w1", "28.1") for _ in range(20)}
    assert len(ids) == 20
