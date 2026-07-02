"""Real, network-dependent tests of registry.diff against the actual
upstream repository (github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History).

NETWORK-DEPENDENT, same rationale as test_git_ops.py/test_resolver.py:
diff.py's whole job is producing real diffs of real historical AL source,
so a mock would just re-encode assumptions instead of catching them
(constitution Principle V). Marked `network` for the same
`pytest -m "not network"` skip path.

Fixture commits are the SAME real, fixed shas test_git_ops.py already
anchors on (`_FROM_SHA`/`_TO_SHA`/`_TOUCHED_PATH`), plus the two real
commits its own docstring flags as touching that path in between
(discovered live during development of this module): a real page trigger
(`page 680 "Report Inbox"`'s `OnOpenPage`) is removed at the first touching
commit and re-added, byte-identical to its original text, at the second --
giving real, live coverage of the added/removed-symbol edge case (spec
Edge Cases) without inventing synthetic fixtures. All tests share ONE
module-scoped mirror (`_mirror`), same rationale as test_resolver.py: these
four commits are small individual fetches (not a full-branch blobless
fetch), but re-fetching each of them once per test function instead of
once per module would still be needless repeated network traffic for a
test file this size.
"""
from __future__ import annotations

import pytest

from registry import diff

pytestmark = pytest.mark.network

_COUNTRY = "w1"

# Real commits on the real w1-28 branch (same as test_git_ops.py).
_FROM_SHA = "5d6549ea4a5c037b8032ab89b1cf673a18927a3a"  # w1-28.1.49838.51918
_FROM_VERSION = "w1-28.1.49838.51918"
_TO_SHA = "e94dbd8173ef42cfa4883983eb07c758b13c749f"  # w1-28.1.49838.51992
_TO_VERSION = "w1-28.1.49838.51992"
# Real commit touching the path between _FROM_SHA and _TO_SHA that REMOVES
# the "Report Inbox" page's OnOpenPage trigger.
_M1_SHA = "69e72f609e0ddd24a62aab5ea85046c7834f41ad"  # w1-28.1.49838.51938
_M1_VERSION = "w1-28.1.49838.51938"
# Real commit touching the path between _M1_SHA and _TO_SHA that RE-ADDS
# the trigger, byte-identical to its text at _FROM_SHA.
_M2_SHA = "50cb0696c3b3bfb12eab52913d0bbe9d9c79e91d"  # w1-28.1.49838.51979
_M2_VERSION = "w1-28.1.49838.51979"

_TOUCHED_PATH = "Base Application/eServices/EDocument/ReportInbox.Page.al"
_OBJECT_TYPE = "page"
_OBJECT_NAME = "Report Inbox"
_PROCEDURE_NAME = "OnOpenPage"


@pytest.fixture(scope="module")
def _mirror(tmp_path_factory):
    return tmp_path_factory.mktemp("diff-mirror")


def test_locate_symbol_file_finds_real_object(_mirror):
    path = diff.locate_symbol_file(_TO_SHA, _OBJECT_TYPE, _OBJECT_NAME, mirror_dir=_mirror)
    assert path == _TOUCHED_PATH


def test_locate_symbol_file_returns_none_for_nonexistent_object(_mirror):
    path = diff.locate_symbol_file(
        _TO_SHA, "codeunit", "This Codeunit Definitely Does Not Exist 12345", mirror_dir=_mirror
    )
    assert path is None


def test_diff_rejects_unscoped_request(_mirror):
    with pytest.raises(diff.DiffScopeError):
        diff.diff(
            _COUNTRY,
            _FROM_SHA,
            _FROM_VERSION,
            _TO_SHA,
            _TO_VERSION,
            mirror_dir=_mirror,
        )


def test_diff_rejects_both_path_and_symbol_scope(_mirror):
    with pytest.raises(diff.DiffScopeError):
        diff.diff(
            _COUNTRY,
            _FROM_SHA,
            _FROM_VERSION,
            _TO_SHA,
            _TO_VERSION,
            path=_TOUCHED_PATH,
            object_type=_OBJECT_TYPE,
            object_name=_OBJECT_NAME,
            mirror_dir=_mirror,
        )


def test_file_scope_diff_shows_only_that_file(_mirror):
    result = diff.diff(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _M1_SHA,
        _M1_VERSION,
        path=_TOUCHED_PATH,
        mirror_dir=_mirror,
    )
    assert result.scope == "file"
    assert result.path == _TOUCHED_PATH
    assert result.symbol is None
    assert result.from_found is True and result.to_found is True
    assert "OnOpenPage" in result.diff_text
    # A real file-scope diff -- never a whole-repo diff (FR-007's own
    # rejection is tested separately above; this confirms the *positive*
    # case stays scoped too).
    assert result.diff_text.count("diff --git") == 1


def test_symbol_scope_diff_removed_between_versions(_mirror):
    """The procedure exists at _FROM_SHA, is removed by _M1_SHA -- the
    real added/removed-symbol edge case (spec Edge Cases), reported via
    `from_found`/`to_found`, never as an error.
    """
    result = diff.diff(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _M1_SHA,
        _M1_VERSION,
        object_type=_OBJECT_TYPE,
        object_name=_OBJECT_NAME,
        procedure_name=_PROCEDURE_NAME,
        mirror_dir=_mirror,
    )
    assert result.scope == "symbol"
    assert result.symbol == diff.Symbol(_OBJECT_TYPE, _OBJECT_NAME, _PROCEDURE_NAME)
    assert result.from_found is True
    assert result.to_found is False
    assert "FilterGroup" in result.diff_text
    assert result.diff_text.strip() != ""


def test_symbol_scope_diff_added_between_versions(_mirror):
    """The mirror image of the above: absent at _M1_SHA, re-added
    (byte-identical to its original text) by _M2_SHA.
    """
    result = diff.diff(
        _COUNTRY,
        _M1_SHA,
        _M1_VERSION,
        _M2_SHA,
        _M2_VERSION,
        object_type=_OBJECT_TYPE,
        object_name=_OBJECT_NAME,
        procedure_name=_PROCEDURE_NAME,
        mirror_dir=_mirror,
    )
    assert result.from_found is False
    assert result.to_found is True
    assert "FilterGroup" in result.diff_text


def test_symbol_scope_diff_unchanged_end_to_end(_mirror):
    """_FROM_SHA and _TO_SHA are byte-identical for this symbol (the
    removal at _M1_SHA and re-addition at _M2_SHA cancel out) -- confirms
    a real net-zero range produces an empty diff with both sides found,
    not a false "no such symbol" or a spurious non-empty diff.
    """
    result = diff.diff(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _TO_SHA,
        _TO_VERSION,
        object_type=_OBJECT_TYPE,
        object_name=_OBJECT_NAME,
        procedure_name=_PROCEDURE_NAME,
        mirror_dir=_mirror,
    )
    assert result.from_found is True
    assert result.to_found is True
    assert result.diff_text == ""


def test_symbol_scope_diff_object_level_no_procedure(_mirror):
    """`procedure_name=None` diffs the whole object's text (the object
    itself is found in both -- only its body differs).
    """
    result = diff.diff(
        _COUNTRY,
        _FROM_SHA,
        _FROM_VERSION,
        _M1_SHA,
        _M1_VERSION,
        object_type=_OBJECT_TYPE,
        object_name=_OBJECT_NAME,
        mirror_dir=_mirror,
    )
    assert result.symbol.procedure_name is None
    assert result.from_found is True
    assert result.to_found is True
    assert "OnOpenPage" in result.diff_text
