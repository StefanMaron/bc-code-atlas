"""Real, network-dependent tests of registry.history against the actual
upstream repository -- same fixture commits as test_diff.py (see that
module's docstring for how they were discovered and why they're real, not
synthetic).

Two real commits touch `_TOUCHED_PATH` between `_FROM_SHA` and `_TO_SHA`
(`_M1_SHA` then `_M2_SHA`). This single real range conveniently covers
BOTH cases T018 calls out explicitly:

- `_PROCEDURE_NAME` ("OnOpenPage"): both touching commits are REAL changes
  to this exact procedure (removed at _M1_SHA, re-added at _M2_SHA) --
  exercises real multi-step change capture, including the found/not-found
  transition (added/removed-symbol edge case).
- `_UNTOUCHED_PROCEDURE_NAME` ("OnAfterGetCurrRecord"): its text is
  byte-identical at all four commits (_FROM_SHA, _M1_SHA, _M2_SHA,
  _TO_SHA) -- both real touching commits changed the FILE (moved this
  trigger's line numbers, confirmed directly) without changing THIS
  symbol's own resolved text. `git log -- _TOUCHED_PATH` reports 2
  touching commits in this range either way; the full-granularity history
  for this symbol MUST collapse to just the baseline (1 step), proving the
  per-commit re-extraction filtering genuinely works rather than trusting
  "touched the file" as a proxy for "changed this symbol" (FR-008).
"""
from __future__ import annotations

import pytest

from registry import git_ops, history

pytestmark = pytest.mark.network

_COUNTRY = "w1"

_FROM_SHA = "5d6549ea4a5c037b8032ab89b1cf673a18927a3a"  # w1-28.1.49838.51918
_FROM_VERSION = "w1-28.1.49838.51918"
_TO_SHA = "e94dbd8173ef42cfa4883983eb07c758b13c749f"  # w1-28.1.49838.51992
_TO_VERSION = "w1-28.1.49838.51992"
_M1_SHA = "69e72f609e0ddd24a62aab5ea85046c7834f41ad"
_M2_SHA = "50cb0696c3b3bfb12eab52913d0bbe9d9c79e91d"

_TOUCHED_PATH = "Base Application/eServices/EDocument/ReportInbox.Page.al"
_OBJECT_TYPE = "page"
_OBJECT_NAME = "Report Inbox"
_PROCEDURE_NAME = "OnOpenPage"
_UNTOUCHED_PROCEDURE_NAME = "OnAfterGetCurrRecord"


@pytest.fixture(scope="module")
def _mirror(tmp_path_factory):
    return tmp_path_factory.mktemp("history-mirror")


def test_raw_git_log_reports_two_touching_commits(_mirror):
    """Sanity check establishing the baseline this module's filtering is
    measured against -- confirms the real range has exactly 2 raw touching
    commits before any symbol-level filtering is applied.
    """
    shas = git_ops.log_for_path(_TOUCHED_PATH, _FROM_SHA, _TO_SHA, mirror_dir=_mirror)
    assert shas == [_M1_SHA, _M2_SHA]


def test_endpoints_granularity_returns_exactly_two_steps(_mirror):
    result = history.build_history(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _TO_SHA,
        _TO_VERSION,
        _OBJECT_TYPE,
        _OBJECT_NAME,
        _PROCEDURE_NAME,
        granularity="endpoints",
        mirror_dir=_mirror,
    )
    assert result.granularity == "endpoints"
    assert len(result.steps) == 2
    assert result.steps[0].commit_sha == _FROM_SHA
    assert result.steps[-1].commit_sha == _TO_SHA
    # Both found and byte-identical (the removal/re-add cancel out over the
    # full range) -- still returned as two explicit steps, not collapsed,
    # per data-model.md ("still meaningful to confirm 'no change'").
    assert result.steps[0].found is True
    assert result.steps[-1].found is True
    assert result.steps[0].text == result.steps[-1].text


def test_full_granularity_captures_real_change_and_revert(_mirror):
    """The target procedure genuinely changes at both real touching
    commits (removed, then re-added) -- full granularity must surface both
    as real steps, including the found -> not-found -> found transition.
    """
    result = history.build_history(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _TO_SHA,
        _TO_VERSION,
        _OBJECT_TYPE,
        _OBJECT_NAME,
        _PROCEDURE_NAME,
        granularity="full",
        mirror_dir=_mirror,
    )
    assert result.granularity == "full"
    assert len(result.steps) == 3
    assert [s.commit_sha for s in result.steps] == [_FROM_SHA, _M1_SHA, _M2_SHA]

    baseline, removed, restored = result.steps
    assert baseline.found is True
    assert removed.found is False
    assert removed.text == ""
    assert restored.found is True
    # Re-added byte-identical to the original.
    assert restored.text == baseline.text
    # changed_from_previous is always True for an included step.
    assert all(step.changed_from_previous for step in result.steps)


def test_full_granularity_filters_out_touched_but_unchanged_symbol(_mirror):
    """The real, confirmed-live case T018 calls out explicitly: both real
    touching commits move this trigger's line numbers within the file
    (confirmed: `git log -- path` still reports 2 touching commits) but
    never change ITS OWN resolved text -- full granularity must collapse
    to just the baseline, strictly shorter than the raw git-log commit
    count for this same range/file (proving symbol-level filtering, not
    just structural plumbing).
    """
    result = history.build_history(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _TO_SHA,
        _TO_VERSION,
        _OBJECT_TYPE,
        _OBJECT_NAME,
        _UNTOUCHED_PROCEDURE_NAME,
        granularity="full",
        mirror_dir=_mirror,
    )
    assert result.granularity == "full"
    assert len(result.steps) == 1
    assert result.steps[0].commit_sha == _FROM_SHA
    assert result.steps[0].found is True

    raw_touching_count = len(
        git_ops.log_for_path(_TOUCHED_PATH, _FROM_SHA, _TO_SHA, mirror_dir=_mirror)
    )
    assert len(result.steps) < raw_touching_count + 1  # +1 for the baseline step


def test_symbol_not_located_in_either_endpoint_raises(_mirror):
    with pytest.raises(history.SymbolNotLocatedError):
        history.build_history(
            _COUNTRY,
            _FROM_SHA,
            _FROM_VERSION,
            _TO_SHA,
            _TO_VERSION,
            "codeunit",
            "This Codeunit Definitely Does Not Exist 12345",
            granularity="endpoints",
            mirror_dir=_mirror,
        )


def test_unknown_granularity_raises_value_error(_mirror):
    with pytest.raises(ValueError):
        history.build_history(
            _COUNTRY,
            _FROM_SHA,
            _FROM_VERSION,
            _TO_SHA,
            _TO_VERSION,
            _OBJECT_TYPE,
            _OBJECT_NAME,
            _PROCEDURE_NAME,
            granularity="bogus",
            mirror_dir=_mirror,
        )
