"""Multi-step symbol change history across a version range -- FR-008, FR-009.

Composes two already-decided primitives (research.md "multi-step symbol
history via `git log` scoped to the containing file, filtered by per-commit
symbol re-extraction"):

1. `git_ops.log_for_path` -- every commit that touched the symbol's
   containing file within (from_sha, to_sha], oldest-first.
2. `diff.extract_symbol_text` -- the same per-version symbol extraction used
   by the two-point diff, re-run at each touching commit.

A touching commit is only kept as a step in the returned chain if the
symbol's own extracted `(found, text)` differs from the previous KEPT
step's -- not from the immediately-prior commit in the raw `git log` list.
This is exactly what makes FR-008 correct: a commit that touched the file
without changing the target symbol (a real, confirmed-live scenario --
see `registry/tests/test_git_ops.py`'s `ReportInbox.Page.al` fixture) never
appears in the output, and a symbol that changes then reverts within the
range still only appears at its genuine change points.

Deviation from data-model.md worth flagging explicitly: `SymbolHistoryStep`
there lists only `version`/`text`/`changed_from_previous`, with no
"found" field. Since a symbol can be genuinely absent at a given commit
(added/removed between versions, same edge case `DiffResult.from_found`/
`to_found` covers for the two-point case), this module adds a `found: bool`
field to `SymbolHistoryStep` so "the procedure didn't exist here" is never
silently collapsed into an indistinguishable empty string -- constitution
Principle VII ("never silently truncate/guess"). `text` is `""` when
`found` is `False`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import diff as diff_module
from . import git_ops

_VALID_GRANULARITIES = ("endpoints", "full")


class SymbolNotLocatedError(Exception):
    """Raised when the symbol's containing file can't be found in EITHER
    endpoint version -- there is no file to scope a `git log` walk to.
    Distinct from a per-step "not found" (`SymbolHistoryStep.found` being
    `False`), which is an expected, reportable mid-range state, not a
    request-level error.
    """


@dataclass(frozen=True)
class SymbolHistoryStep:
    """Mirrors data-model.md's SymbolHistoryStep, plus a `found` field --
    see module docstring for why."""

    commit_sha: str
    version_string: str
    text: str
    found: bool
    changed_from_previous: bool = True  # always True for included steps (FR-008; unchanged steps are filtered out before being returned, never included with this False)


@dataclass(frozen=True)
class SymbolHistoryResult:
    """Mirrors data-model.md's SymbolHistoryResult."""

    symbol: diff_module.Symbol
    country: str
    from_commit_sha: str
    from_version_string: str
    to_commit_sha: str
    to_version_string: str
    granularity: str
    steps: list[SymbolHistoryStep]


def build_history(
    country: str,
    from_sha: str,
    from_version_string: str,
    to_sha: str,
    to_version_string: str,
    object_type: str,
    object_name: str,
    procedure_name: str | None = None,
    granularity: str = "endpoints",
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> SymbolHistoryResult:
    """Build a `SymbolHistoryResult` for the named symbol across
    (from_sha, to_sha] -- FR-008, FR-009.

    `granularity`:
    - `"endpoints"` (default): exactly two steps, the symbol's state at
      `from_sha` and at `to_sha`, regardless of whether anything changed in
      between -- data-model.md: "still meaningful to confirm 'no change'."
    - `"full"`: the baseline step at `from_sha`, plus one step for every
      commit touching the symbol's containing file where the symbol's own
      `(found, text)` actually differs from the previous KEPT step -- never
      one step per raw touching commit.

    Raises `SymbolNotLocatedError` if the symbol's containing file can't be
    found in either endpoint version (nothing to scope a `git log -- path`
    walk to). Raises `ValueError` for an unrecognized `granularity`.
    """
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"Unknown granularity: {granularity!r} -- expected one of "
            f"{_VALID_GRANULARITIES}."
        )

    symbol = diff_module.Symbol(
        object_type=object_type.strip(),
        object_name=object_name.strip(),
        procedure_name=procedure_name.strip() if procedure_name and procedure_name.strip() else None,
    )

    # Locate the containing file once, from whichever endpoint has it --
    # `to_sha` is tried first since callers most often ask "history up to
    # the version I already know about," but either endpoint resolving is
    # enough to scope the `git log -- path` walk (FR-008 doesn't require
    # tracking the symbol across a file rename mid-range; that's a known,
    # documented limitation, not silently mishandled -- see class docstring
    # above for the explicit failure mode when NEITHER endpoint has it).
    path = diff_module.locate_symbol_file(
        to_sha, symbol.object_type, symbol.object_name, mirror_dir, upstream_url
    ) or diff_module.locate_symbol_file(
        from_sha, symbol.object_type, symbol.object_name, mirror_dir, upstream_url
    )
    if path is None:
        raise SymbolNotLocatedError(
            f"{symbol.object_type} {symbol.object_name!r} was not found in "
            f"either {from_version_string!r} or {to_version_string!r} -- "
            "cannot build a change history without knowing which file to "
            "watch."
        )

    def _extract(sha: str) -> tuple[str, bool]:
        return diff_module.extract_symbol_text(sha, path, symbol, mirror_dir, upstream_url)

    from_text, from_found = _extract(from_sha)
    baseline = SymbolHistoryStep(
        commit_sha=from_sha, version_string=from_version_string, text=from_text, found=from_found
    )

    if granularity == "endpoints":
        to_text, to_found = _extract(to_sha)
        end = SymbolHistoryStep(
            commit_sha=to_sha, version_string=to_version_string, text=to_text, found=to_found
        )
        return SymbolHistoryResult(
            symbol=symbol,
            country=country,
            from_commit_sha=from_sha,
            from_version_string=from_version_string,
            to_commit_sha=to_sha,
            to_version_string=to_version_string,
            granularity="endpoints",
            steps=[baseline, end],
        )

    # granularity == "full"
    touching_shas = git_ops.log_for_path(
        path, from_sha, to_sha, mirror_dir=mirror_dir, upstream_url=upstream_url
    )
    steps = [baseline]
    prev_key = (baseline.found, baseline.text)
    for sha in touching_shas:
        text, found = _extract(sha)
        key = (found, text)
        if key == prev_key:
            continue
        version_string = git_ops.commit_message(sha, mirror_dir=mirror_dir, upstream_url=upstream_url)
        steps.append(
            SymbolHistoryStep(commit_sha=sha, version_string=version_string, text=text, found=found)
        )
        prev_key = key

    return SymbolHistoryResult(
        symbol=symbol,
        country=country,
        from_commit_sha=from_sha,
        from_version_string=from_version_string,
        to_commit_sha=to_sha,
        to_version_string=to_version_string,
        granularity="full",
        steps=steps,
    )
