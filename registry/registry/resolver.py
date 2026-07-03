"""Version-spec parsing and resolution against the real upstream repository.

Implements data-model.md's VersionSpec resolution rule (FR-003, FR-004,
FR-005): an exact `version_string`/`commit_sha` resolves directly; a loose
`major.minor` spec (e.g. `"28.1"`) resolves to the single highest-build
`Version` matching that prefix; anything else -- including a spec that is
genuinely too loose to pick exactly one build (spec.md Edge Cases: "a spec
too loose to resolve to exactly one build") -- fails explicitly. Resolution
NEVER silently picks one of several candidates.

Branch-name and version-string shapes below are not assumed -- both were
verified directly against the real upstream repo this session (constitution
Principle V): 546 real branches all match `<country>-<major>[-vNext]`
(the two `claude/*` branches and `main` are not country/version branches and
are ignored everywhere in this module); commit messages match
`<country>-<major>.<minor>.<buildA>.<buildB>[-vNext]` (vNext branches keep
the `-vNext` suffix on every commit message too, confirmed against
`us-28-vNext`/`at-25-vNext`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import git_ops

# `<country>-<major>` or `<country>-<major>-vNext`. Country codes observed
# live are short lowercase alphanumeric tokens (`w1`, `us`, `de`, ...) with
# no internal hyphen -- verified against all 546 real branches this session
# (only `main` and two `claude/*` branches don't match this shape at all,
# and those are correctly not countries).
_BRANCH_RE = re.compile(r"^(?P<country>[a-z0-9]+)-(?P<major>\d+)(?P<vnext>-vNext)?$")

# `<country>-<major>.<minor>.<buildA>.<buildB>` with an optional trailing
# `-vNext` (vNext branches' commit messages keep the suffix, e.g.
# `us-28.4.52211.0-vNext` -- confirmed live).
_VERSION_STRING_RE = re.compile(
    r"^(?P<country>[a-z0-9]+)-(?P<major>\d+)\.(?P<minor>\d+)\.(?P<build_a>\d+)\.(?P<build_b>\d+)(?P<vnext>-vNext)?$"
)

# A caller-supplied "loose" spec: exactly `major.minor`, e.g. "28.1" -- per
# data-model.md's VersionSpec, loose specs are major.minor only, nothing
# coarser or finer.
_LOOSE_SPEC_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)$")

# A caller-supplied spec of just a major version, e.g. "28" -- not a valid
# loose spec on its own (data-model.md requires major.minor), but a common
# enough mistake that it deserves the "ambiguous" edge case explicitly
# called out in spec.md ("a spec too loose to resolve to exactly one
# build") rather than a bare "not found".
_MAJOR_ONLY_RE = re.compile(r"^\d+$")

# Full git commit sha (hex-40) -- the only sha shape treated as an exact
# spec; anything shorter is ambiguous by construction (git itself would
# need a real disambiguation check) and out of scope for this resolver.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class UpstreamUnavailableError(Exception):
    """Wraps a real `git_ops.GitOpsError` (network/auth/protocol failure
    against the real upstream repo) so callers -- especially
    `mcp_server.py` -- can produce the contract's shared
    `{"error": "upstream_unavailable", ...}` shape (contracts/registry-tools.md)
    without every resolver caller needing to know about `git_ops`'s own
    exception type.
    """


@dataclass(frozen=True)
class ParsedVersion:
    """One real build, parsed from a branch commit's `(sha, message)`."""

    country: str
    major: int
    minor: int
    build_a: int
    build_b: int
    is_vnext: bool
    commit_sha: str
    version_string: str

    @property
    def major_minor(self) -> str:
        return f"{self.major}.{self.minor}"

    @property
    def build_key(self) -> tuple[int, int]:
        """Sort key for "highest build" -- data-model.md's `build_number`
        field is documented as the trailing numeric component (`build_b`),
        but `build_a` is included first here as a robustness margin in case
        it ever varies within one major.minor (not observed live, but
        cheap to guard against without contradicting the documented field).
        """
        return (self.build_a, self.build_b)


@dataclass(frozen=True)
class ResolvedVersion:
    """Success shape -- mirrors contracts/registry-tools.md's
    `bcatlas_resolve_version` success output field-for-field.
    """

    country: str
    commit_sha: str
    version_string: str
    resolved: bool = field(default=True, init=False)


@dataclass(frozen=True)
class ResolutionFailure:
    """Failure shape -- mirrors contracts/registry-tools.md's
    `bcatlas_resolve_version` failure output field-for-field. `reason` is
    always `"not_found"` or `"ambiguous"`, never a bare exception (FR-005).
    """

    reason: str
    detail: str
    resolved: bool = field(default=False, init=False)


def parse_branch_name(branch: str) -> tuple[str, str, bool] | None:
    """`(country, major, is_vnext)`, or `None` if `branch` isn't a real
    country/version branch (e.g. `main`, `claude/*`).
    """
    m = _BRANCH_RE.match(branch)
    if m is None:
        return None
    return m.group("country"), m.group("major"), m.group("vnext") is not None


def parse_version_string(version_string: str) -> ParsedVersion | None:
    """Parse a commit message into its components, or `None` if it doesn't
    match the known `<country>-<major>.<minor>.<buildA>.<buildB>[-vNext]`
    shape (defensive -- every real commit message observed live matches,
    but a resolver must not crash on an unexpected upstream commit).
    """
    m = _VERSION_STRING_RE.match(version_string)
    if m is None:
        return None
    return ParsedVersion(
        country=m.group("country"),
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        build_a=int(m.group("build_a")),
        build_b=int(m.group("build_b")),
        is_vnext=m.group("vnext") is not None,
        commit_sha="",  # filled in by the caller, which has the sha
        version_string=version_string,
    )


def list_countries(
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> list[str]:
    """Deduplicated, sorted list of real country codes derived from
    upstream branch-name prefixes (FR-001) -- never the raw ~546 branch
    names themselves.
    """
    try:
        branches = git_ops.list_branches(upstream_url=upstream_url)
    except git_ops.GitOpsError as e:
        raise UpstreamUnavailableError(str(e)) from e
    countries: set[str] = set()
    for branch in branches:
        parsed = parse_branch_name(branch)
        if parsed is not None:
            countries.add(parsed[0])
    return sorted(countries)


def _branches_for_country_major(
    country: str, major: str, branches: list[str]
) -> list[str]:
    """Every real branch for this exact (country, major) -- normally just
    `<country>-<major>`, plus `<country>-<major>-vNext` when it exists too.
    """
    result = []
    for branch in branches:
        parsed = parse_branch_name(branch)
        if parsed is not None and parsed[0] == country and parsed[1] == major:
            result.append(branch)
    return result


def _country_exists(country: str, branches: list[str]) -> bool:
    return any(
        parsed is not None and parsed[0] == country
        for parsed in (parse_branch_name(b) for b in branches)
    )


def _builds_for_branches(
    country: str,
    branches: list[str],
    mirror_dir: Path,
    upstream_url: str,
) -> list[ParsedVersion]:
    """Every real build (as `ParsedVersion`) across `branches`, scoped to
    `country` (defensive -- a branch's commits should all belong to that
    branch's own country, but this never trusts that without checking the
    parsed commit message itself).

    `is_vnext` is OR'd from two independent sources -- the commit message's
    own `-vNext` suffix AND the branch it was actually read from
    (`parse_branch_name`) -- rather than trusting the message alone.
    Confirmed live (real upstream data, not theorized): commit
    `90abe0f13b0e7e24ec90ffea8ac5a1b9aea1d434` on `w1`'s ONLY branch for
    major 29 (`w1-29-vNext` -- there is no plain `w1-29` at all yet) has the
    message `w1-29.0.46763.0`, with no `-vNext` suffix on the message text
    itself. Trusting the message alone left `is_vnext=False`, and since it
    was the ONLY build for major_minor "29.0", `_pick_best`'s
    stable-preferred selection had nothing else to prefer over it --
    `list_major_versions('w1')` and every resolution path routed through
    this function (exact version string, loose major.minor, major-only)
    surfaced it as if it were a real stable release. A branch named
    `<country>-<major>-vNext` can ONLY ever contain preview builds by
    definition, regardless of what any individual commit's own message
    text happens to say -- so the branch-level flag must win whenever it
    says vNext, even if the message-derived flag disagrees.
    """
    builds: list[ParsedVersion] = []
    for branch in branches:
        branch_parsed = parse_branch_name(branch)
        branch_is_vnext = branch_parsed is not None and branch_parsed[2]
        try:
            commits = git_ops.list_commits(branch, mirror_dir=mirror_dir, upstream_url=upstream_url)
        except git_ops.GitOpsError as e:
            raise UpstreamUnavailableError(str(e)) from e
        for sha, message in commits:
            parsed = parse_version_string(message)
            if parsed is not None and parsed.country == country:
                builds.append(
                    ParsedVersion(
                        country=parsed.country,
                        major=parsed.major,
                        minor=parsed.minor,
                        build_a=parsed.build_a,
                        build_b=parsed.build_b,
                        is_vnext=parsed.is_vnext or branch_is_vnext,
                        commit_sha=sha,
                        version_string=message,
                    )
                )
    return builds


def _pick_best(builds: list[ParsedVersion]) -> ParsedVersion:
    """Highest-build entry within `builds`, preferring non-vNext
    (stable-track) builds over `-vNext` preview builds when both exist.

    Confirmed live against the real upstream repo: a country's `-vNext`
    branch can carry commits sharing a `major.minor` label with its stable
    branch AND a higher raw build number (e.g. real `w1-28-vNext` commit
    `w1-28.1.50254.0-vNext` alongside real stable `w1-28` commits also
    labelled `28.1` but with lower build numbers) -- naive "highest build
    wins" would silently resolve a plain `"28.1"` spec (no mention of
    vNext) to an unreleased preview build. A loose spec with no `-vNext`
    marker should resolve to the stable release whenever one exists at all;
    only a major.minor that exists ONLY on a `-vNext` branch (e.g. the next
    major version before its stable branch is cut) falls back to vNext.
    """
    stable = [b for b in builds if not b.is_vnext]
    pool = stable if stable else builds
    return max(pool, key=lambda b: b.build_key)


def list_major_versions(
    country: str,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> list[dict] | None:
    """Summarized `major_minor` -> latest-build view for `country`, per
    `bcatlas_list_versions`'s contract (FR-002) -- one entry per
    `major_minor`, not one per raw build.

    Returns `None` (distinct from `[]`) if `country` isn't a real country at
    all, so a caller (`mcp_server.py`) can tell "real country, zero
    versions" apart from "not a real country" per the contract's explicit
    "Error (not empty list) if country doesn't exist."
    """
    try:
        branches = git_ops.list_branches(upstream_url=upstream_url)
    except git_ops.GitOpsError as e:
        raise UpstreamUnavailableError(str(e)) from e

    if not _country_exists(country, branches):
        return None

    country_branches = [
        b for b in branches if (parsed := parse_branch_name(b)) is not None and parsed[0] == country
    ]
    builds = _builds_for_branches(country, country_branches, mirror_dir, upstream_url)

    by_major_minor: dict[str, list[ParsedVersion]] = {}
    for build in builds:
        by_major_minor.setdefault(build.major_minor, []).append(build)
    best = {mm: _pick_best(group) for mm, group in by_major_minor.items()}

    def _sort_key(major_minor: str) -> tuple[int, int]:
        major_str, minor_str = major_minor.split(".", 1)
        return (int(major_str), int(minor_str))

    return [
        {"major_minor": mm, "latest_build": best[mm].version_string}
        for mm in sorted(best, key=_sort_key)
    ]


def resolve_version(
    country: str,
    spec: str,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    upstream_url: str = git_ops.UPSTREAM_URL,
) -> ResolvedVersion | ResolutionFailure:
    """Resolve `spec` (exact `version_string`, exact `commit_sha`, or a
    loose `"major.minor"` spec) to a single, unambiguous `Version` within
    `country` -- FR-003, FR-004, FR-005. Never guesses: zero or more than
    one interpretation always returns a `ResolutionFailure`, never a
    `ResolvedVersion` picked arbitrarily.
    """
    country = country.strip()
    spec = spec.strip()

    if not country:
        return ResolutionFailure(reason="not_found", detail="country is required.")
    if not spec:
        return ResolutionFailure(reason="not_found", detail="spec is required.")

    try:
        branches = git_ops.list_branches(upstream_url=upstream_url)
    except git_ops.GitOpsError as e:
        raise UpstreamUnavailableError(str(e)) from e

    if not _country_exists(country, branches):
        return ResolutionFailure(
            reason="not_found", detail=f"Unknown country: {country!r}."
        )

    # 1. Exact commit sha.
    if _SHA_RE.match(spec):
        try:
            message = git_ops.commit_message(spec, mirror_dir=mirror_dir, upstream_url=upstream_url)
        except git_ops.GitOpsError:
            return ResolutionFailure(
                reason="not_found",
                detail=f"{spec!r} is not a real commit in the upstream repository.",
            )
        parsed = parse_version_string(message)
        if parsed is None or parsed.country != country:
            return ResolutionFailure(
                reason="not_found",
                detail=f"{spec!r} does not belong to country {country!r} (found: {message!r}).",
            )
        return ResolvedVersion(country=country, commit_sha=spec, version_string=message)

    # 2. Exact version string.
    exact = parse_version_string(spec)
    if exact is not None:
        if exact.country != country:
            return ResolutionFailure(
                reason="not_found",
                detail=f"{spec!r} does not belong to country {country!r}.",
            )
        target_branches = _branches_for_country_major(country, str(exact.major), branches)
        if not target_branches:
            return ResolutionFailure(
                reason="not_found",
                detail=f"No branch for country {country!r} major {exact.major}.",
            )
        builds = _builds_for_branches(country, target_branches, mirror_dir, upstream_url)
        matches = [b for b in builds if b.version_string == spec]
        if not matches:
            return ResolutionFailure(
                reason="not_found",
                detail=f"{spec!r} is not a real build of {country!r}.",
            )
        if len(matches) > 1:
            # Not observed live, but never silently pick one if it happens
            # (e.g. an upstream rebuild re-issuing an identical message).
            return ResolutionFailure(
                reason="ambiguous",
                detail=f"{spec!r} matches {len(matches)} distinct commits for {country!r}.",
            )
        match = matches[0]
        return ResolvedVersion(country=country, commit_sha=match.commit_sha, version_string=match.version_string)

    # 3. Loose major.minor spec.
    loose = _LOOSE_SPEC_RE.match(spec)
    if loose is not None:
        major, minor = loose.group("major"), loose.group("minor")
        target_branches = _branches_for_country_major(country, major, branches)
        if not target_branches:
            return ResolutionFailure(
                reason="not_found",
                detail=f"No branch for country {country!r} major {major}.",
            )
        builds = _builds_for_branches(country, target_branches, mirror_dir, upstream_url)
        matches = [b for b in builds if b.major == int(major) and b.minor == int(minor)]
        if not matches:
            return ResolutionFailure(
                reason="not_found",
                detail=f"No build matching {country}-{major}.{minor} found.",
            )
        best = _pick_best(matches)
        return ResolvedVersion(country=country, commit_sha=best.commit_sha, version_string=best.version_string)

    # 4. Major-only spec -- too loose to resolve to one build by design
    # (data-model.md requires major.minor for a loose spec); surfaced as
    # the explicit "ambiguous" edge case (spec.md Edge Cases) rather than
    # "not found", since it's a real, common near-miss, not junk input.
    if _MAJOR_ONLY_RE.match(spec):
        target_branches = _branches_for_country_major(country, spec, branches)
        if not target_branches:
            return ResolutionFailure(
                reason="not_found",
                detail=f"No branch for country {country!r} major {spec}.",
            )
        builds = _builds_for_branches(country, target_branches, mirror_dir, upstream_url)
        # A country's higher-major branch's git history transitively
        # includes older majors' own build commits as ancestors (confirmed
        # live: `w1-28`'s ref reaches ~4075 commits, not just its own
        # ~100-ish major-28 builds, because w1-28 was cut from a point in
        # w1-27's history, which was cut from w1-26's, and so on) --
        # `_builds_for_branches` deliberately doesn't scope by major itself
        # (loose major.minor resolution above needs exactly this
        # ancestor-inclusive behavior to be a no-op there since it already
        # filters by major itself), so this path MUST filter explicitly or
        # every major-only spec would spuriously look ambiguous across the
        # country's ENTIRE history, not just the requested major.
        builds = [b for b in builds if b.major == int(spec)]
        distinct_minors = sorted({b.major_minor for b in builds}, key=lambda mm: int(mm.split(".", 1)[1]))
        if not distinct_minors:
            return ResolutionFailure(
                reason="not_found",
                detail=f"No build matching major version {spec} found for {country!r}.",
            )
        if len(distinct_minors) == 1:
            matches = [b for b in builds if b.major_minor == distinct_minors[0]]
            best = _pick_best(matches)
            return ResolvedVersion(country=country, commit_sha=best.commit_sha, version_string=best.version_string)
        return ResolutionFailure(
            reason="ambiguous",
            detail=(
                f"{spec!r} is a major version only and matches "
                f"{len(distinct_minors)} minor versions for {country!r}: "
                f"{', '.join(distinct_minors)}. Specify major.minor (e.g. "
                f"{distinct_minors[-1]!r})."
            ),
        )

    return ResolutionFailure(
        reason="not_found",
        detail=(
            f"{spec!r} is not a recognized version spec -- expected an exact "
            "build's version string, a full commit sha, or a loose "
            "\"major.minor\" spec (e.g. \"28.1\")."
        ),
    )
