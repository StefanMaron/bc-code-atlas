"""Unit tests for `_load_presentation_settings` (specs/006-configurable-mcp-
instructions, GitHub issue #20): operator-configurable MCP instructions text
and search path-filter prefixes, defaulting to today's hardcoded behavior
when `.bcatlas/mcp_presentation.yml` is absent, and failing fast (FR-005) on
a present-but-invalid file.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http_server import _MCP_INSTRUCTIONS, _load_presentation_settings  # noqa: E402


@pytest.fixture()
def tmp_project() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="bcatlas_presentation_test_") as d:
        yield Path(d)


def _write_settings(project_root: Path, text: str) -> None:
    settings_dir = project_root / ".bcatlas"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "mcp_presentation.yml").write_text(text)


def test_no_file_returns_defaults(tmp_project: Path) -> None:
    instructions, prefixes = _load_presentation_settings(str(tmp_project))
    assert instructions == _MCP_INSTRUCTIONS
    assert prefixes is None


def test_instructions_only_keeps_prefix_default(tmp_project: Path) -> None:
    _write_settings(tmp_project, "instructions: |\n  Custom corpus instructions.\n")
    instructions, prefixes = _load_presentation_settings(str(tmp_project))
    assert instructions == "Custom corpus instructions.\n"
    assert prefixes is None


def test_prefixes_only_keeps_instructions_default(tmp_project: Path) -> None:
    _write_settings(tmp_project, "path_prefixes:\n  - src\n  - lib\n")
    instructions, prefixes = _load_presentation_settings(str(tmp_project))
    assert instructions == _MCP_INSTRUCTIONS
    assert prefixes == ("src", "lib")


def test_both_fields_configured(tmp_project: Path) -> None:
    _write_settings(
        tmp_project,
        "instructions: Custom text\npath_prefixes:\n  - src\n",
    )
    instructions, prefixes = _load_presentation_settings(str(tmp_project))
    assert instructions == "Custom text"
    assert prefixes == ("src",)


def test_empty_path_prefixes_means_no_prefixes(tmp_project: Path) -> None:
    _write_settings(tmp_project, "path_prefixes: []\n")
    _instructions, prefixes = _load_presentation_settings(str(tmp_project))
    assert prefixes == ()


def test_malformed_yaml_exits(tmp_project: Path) -> None:
    _write_settings(tmp_project, "instructions: [this is not closed\n")
    with pytest.raises(SystemExit) as exc_info:
        _load_presentation_settings(str(tmp_project))
    assert "mcp_presentation.yml" in str(exc_info.value)


def test_non_mapping_top_level_exits(tmp_project: Path) -> None:
    _write_settings(tmp_project, "- just\n- a\n- list\n")
    with pytest.raises(SystemExit) as exc_info:
        _load_presentation_settings(str(tmp_project))
    assert "mapping" in str(exc_info.value)


def test_instructions_wrong_type_exits(tmp_project: Path) -> None:
    _write_settings(tmp_project, "instructions:\n  - not\n  - a\n  - string\n")
    with pytest.raises(SystemExit) as exc_info:
        _load_presentation_settings(str(tmp_project))
    assert "instructions" in str(exc_info.value)


def test_path_prefixes_wrong_type_exits(tmp_project: Path) -> None:
    _write_settings(tmp_project, "path_prefixes: not-a-list\n")
    with pytest.raises(SystemExit) as exc_info:
        _load_presentation_settings(str(tmp_project))
    assert "path_prefixes" in str(exc_info.value)


def test_path_prefixes_non_string_items_exits(tmp_project: Path) -> None:
    _write_settings(tmp_project, "path_prefixes:\n  - src\n  - 42\n")
    with pytest.raises(SystemExit) as exc_info:
        _load_presentation_settings(str(tmp_project))
    assert "path_prefixes" in str(exc_info.value)
