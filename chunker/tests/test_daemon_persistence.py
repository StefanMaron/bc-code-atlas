"""Regression guard for constitution Principle VIII (Deploys Must Not Reset
the Serving Index): the search daemon must resume from its on-disk state
after a process restart, not reprocess an already-indexed corpus from
scratch.

Uses the real `al_chunker` registration path and a real (small)
sentence-transformers model against real cocoindex-code daemon/CLI
machinery -- no mocking of the mechanism under test (constitution
Principle V). If a future change to cocoindex-code, its storage layout, or
`.cocoindex_code/` path resolution breaks resumption, this test fails and
blocks the merge -- exactly the guard Principle VIII requires.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cocoindex_code import client
from cocoindex_code.cli import app
from cocoindex_code.client import stop_daemon
from cocoindex_code.protocol import IndexingProgress
from cocoindex_code.settings import (
    ChunkerMapping,
    EmbeddingSettings,
    ProjectSettings,
    UserSettings,
    save_project_settings,
    save_user_settings,
)

runner = CliRunner()

# Same small, fast model cocoindex-code's own test suite uses -- a real
# download and a real embed, just cheap enough for CI (constitution
# Principle V: don't mock what can be measured for real).
TEST_EMBEDDING_MODEL = "sentence-transformers/paraphrase-MiniLM-L3-v2"

SAMPLE_CODEUNIT_AL = """\
codeunit 50100 "Test Codeunit"
{
    procedure DoSomething()
    begin
        Message('Hello');
    end;
}
"""

SAMPLE_TABLE_AL = """\
table 50101 "Test Table"
{
    fields
    {
        field(1; "No."; Code[20]) { }
    }
}
"""

SAMPLE_NEW_CODEUNIT_AL = """\
codeunit 50102 "Second Codeunit"
{
    procedure DoSomethingElse()
    begin
        Message('World');
    end;
}
"""


@pytest.fixture()
def al_project() -> Iterator[Path]:
    """Temp project indexed with the real al_chunker, same settings shape as
    the production `data/.cocoindex_code/settings.yml` (chunkers: ext al ->
    al_chunker:al_chunker).
    """
    base_dir = Path(tempfile.mkdtemp(prefix="bcatlas_persist_"))
    project_dir = base_dir / "project"
    project_dir.mkdir()
    (project_dir / "Codeunit1.al").write_text(SAMPLE_CODEUNIT_AL)
    (project_dir / "Table1.al").write_text(SAMPLE_TABLE_AL)
    (project_dir / ".git").mkdir()

    old_env = os.environ.get("COCOINDEX_CODE_DIR")
    os.environ["COCOINDEX_CODE_DIR"] = str(base_dir)
    old_cwd = os.getcwd()
    os.chdir(project_dir)

    save_user_settings(
        UserSettings(
            embedding=EmbeddingSettings(
                provider="sentence-transformers",
                model=TEST_EMBEDDING_MODEL,
            )
        )
    )
    save_project_settings(
        project_dir,
        ProjectSettings(
            include_patterns=["**/*.al"],
            chunkers=[ChunkerMapping(ext="al", module="al_chunker:al_chunker")],
        ),
    )

    try:
        yield project_dir
    finally:
        os.chdir(project_dir)
        runner.invoke(app, ["reset", "--all", "-f"])
        stop_daemon()
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("COCOINDEX_CODE_DIR", None)
        else:
            os.environ["COCOINDEX_CODE_DIR"] = old_env


def _index(project_root: str) -> IndexingProgress:
    """Run indexing and return the last progress snapshot seen."""
    last: list[IndexingProgress] = []
    client.index(project_root, on_progress=last.append)
    assert last, "daemon never reported any indexing progress"
    return last[-1]


def test_daemon_restart_resumes_instead_of_reprocessing(al_project: Path) -> None:
    """First index processes both real files. A daemon restart (simulating a
    routine deploy -- SIGTERM + respawn, `.cocoindex_code/` untouched on
    disk) followed by a genuinely new third file must show the two original
    files coming back as unchanged, not reprocessed.
    """
    project_root = str(al_project)

    initial = _index(project_root)
    assert initial.num_errors == 0
    assert initial.num_adds == 2
    assert initial.num_unchanged == 0

    # Simulate a routine deploy: kill and respawn the daemon process. This
    # must NOT touch `.cocoindex_code/` on disk -- exactly what a
    # `systemctl restart` does in production (see constitution Principle
    # VIII).
    result = runner.invoke(app, ["daemon", "restart"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    # A genuinely new file lands after the restart, same as a real deploy
    # shipping alongside unrelated code/corpus changes.
    (al_project / "Codeunit2.al").write_text(SAMPLE_NEW_CODEUNIT_AL)

    after_restart = _index(project_root)
    assert after_restart.num_errors == 0
    # The core Principle VIII guarantee: the two pre-restart files must be
    # recognized as unchanged, not reprocessed by the new daemon process.
    assert after_restart.num_unchanged == 2
    assert after_restart.num_adds == 1
    assert after_restart.num_reprocesses == 0

    status = client.project_status(project_root)
    assert status.total_files == 3
