"""Unit tests for `_validate_project_root` (specs/005-local-source-directory,
GitHub issue #18): a misconfigured AL source directory must fail fast with a
clear message (FR-004) or warn without failing (FR-005), never start up with
a silently empty index.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http_server import _validate_project_root  # noqa: E402


@pytest.fixture()
def tmp_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="bcatlas_source_dir_test_") as d:
        yield Path(d)


def test_missing_path_exits_with_clear_message(tmp_dir: Path) -> None:
    missing = tmp_dir / "does-not-exist"
    with pytest.raises(SystemExit) as exc_info:
        _validate_project_root(str(missing))
    assert str(missing) in str(exc_info.value)


def test_path_is_a_file_not_a_directory_exits(tmp_dir: Path) -> None:
    a_file = tmp_dir / "not-a-directory.al"
    a_file.write_text("codeunit 1 X { }")
    with pytest.raises(SystemExit) as exc_info:
        _validate_project_root(str(a_file))
    assert str(a_file) in str(exc_info.value)


def test_directory_with_al_files_passes_silently(tmp_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_dir / "Codeunit1.al").write_text("codeunit 1 X { }")
    _validate_project_root(str(tmp_dir))
    assert capsys.readouterr().out == ""


def test_empty_directory_warns_without_exiting(tmp_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _validate_project_root(str(tmp_dir))  # must not raise
    out = capsys.readouterr().out
    assert "warning" in out.lower()
    assert str(tmp_dir) in out


def test_nested_al_file_is_found(tmp_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nested = tmp_dir / "Base Application" / "Sales"
    nested.mkdir(parents=True)
    (nested / "SalesOrder.al").write_text("codeunit 1 X { }")
    _validate_project_root(str(tmp_dir))
    assert capsys.readouterr().out == ""
