"""File- and symbol-scoped diffing between two resolved commits of the same
country's AL source -- FR-006, FR-007.

Two independent scopes (spec Acceptance Scenarios 1-3):

- File scope: a real `git diff <from_sha> <to_sha> -- <path>` via
  `git_ops.diff_paths` -- ordinary git line-diff is the *right* tool here
  because the caller explicitly wants "what changed in this file."
- Symbol scope: NEVER a git line-diff (research.md "symbol-scoped diff by
  independent per-version extraction, not a git line-diff" -- line numbers
  shift between versions, so a line-anchored diff would misattribute
  unrelated nearby changes to the target symbol, or miss the symbol's own
  change entirely). Instead: locate the symbol's containing file
  independently in each version's tree, fetch each version's blob, extract
  the symbol's text via `symbols.find_symbol_span` in each, and diff the
  two extracted texts with `difflib.unified_diff`.

A request supplying neither `path` nor a full symbol triple (nor both) is
rejected via `DiffScopeError` -- an unscoped diff is never produced
(FR-007, spec Acceptance Scenario 3).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, symbols


class DiffScopeError(Exception):
    """Raised when the request doesn't supply exactly one of `path` (file
    scope) or a full `object_type`/`object_name` pair (symbol scope) --
    both absent or both present are equally rejected. `mcp_server.py`
    turns this into the contract's explicit rejection response; it MUST
    NEVER be swallowed into producing a whole-repository diff (FR-007).
    """


@dataclass(frozen=True)
class Symbol:
    """Mirrors data-model.md's Symbol entity."""

    object_type: str
    object_name: str
    procedure_name: str | None = None


@dataclass(frozen=True)
class DiffResult:
    """Mirrors data-model.md's DiffResult entity field-for-field (plus
    `from_commit_sha`/`to_commit_sha` alongside the two version strings,
    since callers need the exact commit for e.g. a follow-up
    `bcatlas_symbol_history` range).
    """

    scope: str  # "file" | "symbol"
    country: str
    from_commit_sha: str
    from_version_string: str
    to_commit_sha: str
    to_version_string: str
    path: str | None
    symbol: Symbol | None
    diff_text: str
    from_found: bool
    to_found: bool


# Filename-suffix tokens (case-insensitive) accepted for each real
# `object_type` (the full-word forms used throughout this feature's data
# model and by graphify's `_AL_CONFIG.class_types`, e.g. "pageextension" --
# never AL's on-disk abbreviations). Real on-disk filenames for extension
# objects use inconsistent case/abbreviation for the type segment
# (confirmed empirically against the real w1-28 corpus this session, e.g.
# "TestDataSearchExtension.PageExt.al", "ExtensionSubscribers.PageExt.al"
# alongside other real files using "Pageext"/"pageext") -- base (non-
# extension) object types are consistently the full word
# ("Assert.Codeunit.al", "ReportInbox.Page.al"). This mapping is why
# `locate_symbol_file` searches the real committed tree by filename
# pattern instead of constructing "<ObjectName>.<ObjectType>.al" directly:
# a direct construction would silently miss roughly half of all real
# extension-object files due to this casing inconsistency.
_FILENAME_TYPE_TOKENS: dict[str, tuple[str, ...]] = {
    "codeunit": ("codeunit",),
    "table": ("table",),
    "page": ("page",),
    "report": ("report",),
    "query": ("query",),
    "xmlport": ("xmlport",),
    "enum": ("enum",),
    "interface": ("interface",),
    "controladdin": ("controladdin",),
    "permissionset": ("permissionset",),
    "permissionsetextension": ("permissionsetextension", "permissionsetext"),
    "profile": ("profile",),
    "profileextension": ("profileextension", "profileext"),
    "pageextension": ("pageextension", "pageext"),
    "tableextension": ("tableextension", "tableext"),
    "reportextension": ("reportextension", "reportext"),
    "enumextension": ("enumextension", "enumext"),
    "entitlement": ("entitlement",),
    "dotnet": ("dotnet",),
}


def _normalize_object_name(name: str) -> str:
    """Real on-disk filenames strip spaces from the object name (confirmed
    live: page "Report Inbox" -> file "ReportInbox.Page.al") and vary in
    case -- normalize both sides of the comparison the same way.
    """
    return name.replace(" ", "").strip().lower()


def locate_symbol_file(
    sha: str,
    object_type: str,
    object_name: str,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> str | None:
    """Find the real path of the `.al` file containing `object_name`
    (`object_type`) within the tree at `sha`, or `None` if no matching file
    exists in this version -- an expected, common case (an object added or
    removed between two versions, per spec Edge Cases), not an error.

    Strategy: `git_ops.list_tree(sha)` (real committed tree, <20ms even for
    the ~19k-file w1-28 corpus) filtered by filename pattern
    "<Name>.<Type>.al" or "<Name>.<Type>.<numeric-id>.al" (both real shapes
    confirmed live, e.g. "ReportInbox.Page.al" and the one real outlier
    "ContactCoverSheet.Report.5085.al" -- `segments[1]` is always the type
    token regardless of a trailing numeric id segment). Chosen over directly
    constructing the expected path from `object_type`/`object_name` because
    the type segment's real on-disk casing/abbreviation is not reproducible
    for extension objects (see `_FILENAME_TYPE_TOKENS`'s docstring above) --
    this was verified against real files in `data/w1-28-src` before picking
    this approach, per constitution Principle V.

    If more than one file matches (not observed live for a real base-app
    object/type pair, but not provable impossible across the whole
    ~51-country corpus), the first match in `git ls-tree`'s path-sorted
    order is used -- deterministic, and a real ambiguity here would be a
    genuine upstream naming collision worth surfacing separately rather
    than silently disambiguating further.
    """
    tokens = _FILENAME_TYPE_TOKENS.get(object_type.strip().lower())
    if not tokens:
        return None
    target = _normalize_object_name(object_name)

    for path in git_ops.list_tree(sha, mirror_dir=mirror_dir, upstream_url=upstream_url):
        if not path.lower().endswith(".al"):
            continue
        filename = path.rsplit("/", 1)[-1]
        segments = filename.split(".")
        # "<Name>.<Type>.al" or "<Name>.<Type>.<id>.al" -- need at least
        # name + type + "al".
        if len(segments) < 3:
            continue
        name_token, type_token = segments[0], segments[1]
        if type_token.lower() not in tokens:
            continue
        if _normalize_object_name(name_token) == target:
            return path
    return None


def extract_symbol_text(
    sha: str,
    path: str | None,
    symbol: Symbol,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> tuple[str, bool]:
    """`(text, found)` for `symbol` at `sha`, given the file `path` already
    located by `locate_symbol_file` (or `None` if no file was found there
    at all). Shared by `diff()` (two-point diff) and `history.py`
    (multi-step walk) so both use identical extraction semantics.
    """
    if path is None:
        return "", False
    source = git_ops.read_blob(sha, path, mirror_dir=mirror_dir, upstream_url=upstream_url)
    span = symbols.find_symbol_span(
        source, symbol.object_type, symbol.object_name, symbol.procedure_name
    )
    if span is None:
        return "", False
    return span.text, True


def _symbol_label(version_string: str, symbol: Symbol) -> str:
    label = f"{version_string}: {symbol.object_type} {symbol.object_name!r}"
    if symbol.procedure_name:
        label += f".{symbol.procedure_name}"
    return label


def diff(
    country: str,
    from_sha: str,
    from_version_string: str,
    to_sha: str,
    to_version_string: str,
    path: str | None = None,
    object_type: str | None = None,
    object_name: str | None = None,
    procedure_name: str | None = None,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> DiffResult:
    """Produce a `DiffResult` scoped to exactly one of `path` (file scope)
    or the `object_type`/`object_name`/`procedure_name` triple (symbol
    scope) -- FR-006. Raises `DiffScopeError` if neither or both are
    supplied -- an unscoped, whole-repository diff is never produced
    (FR-007).
    """
    has_path = bool(path and path.strip())
    has_symbol = bool(object_type and object_type.strip()) and bool(object_name and object_name.strip())

    if has_path and has_symbol:
        raise DiffScopeError(
            "Supply exactly one of `path` (file scope) or `object_type`+"
            "`object_name` (symbol scope), not both."
        )
    if not has_path and not has_symbol:
        raise DiffScopeError(
            "A diff request must be scoped to either `path` (file scope) or "
            "`object_type`+`object_name` (symbol scope) -- an unscoped, "
            "whole-repository diff is never produced."
        )

    if has_path:
        diff_text = git_ops.diff_paths(
            from_sha, to_sha, path.strip(), mirror_dir=mirror_dir, upstream_url=upstream_url
        )
        return DiffResult(
            scope="file",
            country=country,
            from_commit_sha=from_sha,
            from_version_string=from_version_string,
            to_commit_sha=to_sha,
            to_version_string=to_version_string,
            path=path.strip(),
            symbol=None,
            diff_text=diff_text,
            from_found=True,
            to_found=True,
        )

    symbol = Symbol(
        object_type=object_type.strip(),
        object_name=object_name.strip(),
        procedure_name=procedure_name.strip() if procedure_name and procedure_name.strip() else None,
    )

    from_path = locate_symbol_file(from_sha, symbol.object_type, symbol.object_name, mirror_dir, upstream_url)
    to_path = locate_symbol_file(to_sha, symbol.object_type, symbol.object_name, mirror_dir, upstream_url)

    from_text, from_found = extract_symbol_text(from_sha, from_path, symbol, mirror_dir, upstream_url)
    to_text, to_found = extract_symbol_text(to_sha, to_path, symbol, mirror_dir, upstream_url)

    diff_text = "".join(
        difflib.unified_diff(
            from_text.splitlines(keepends=True),
            to_text.splitlines(keepends=True),
            fromfile=_symbol_label(from_version_string, symbol),
            tofile=_symbol_label(to_version_string, symbol),
        )
    )

    return DiffResult(
        scope="symbol",
        country=country,
        from_commit_sha=from_sha,
        from_version_string=from_version_string,
        to_commit_sha=to_sha,
        to_version_string=to_version_string,
        path=None,
        symbol=symbol,
        diff_text=diff_text,
        from_found=from_found,
        to_found=to_found,
    )
