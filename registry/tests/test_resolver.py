"""Real, network-dependent tests of registry.resolver against the actual
upstream repository (github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History).

NETWORK-DEPENDENT, same rationale as test_git_ops.py: this module's whole
job is resolving real version specs against real upstream data, so a mock
would just re-encode assumptions instead of catching them (constitution
Principle V). Marked `network` for the same `pytest -m "not network"` skip
path.

All tests in this module share ONE module-scoped mirror directory
(`_mirror`) rather than a fresh `tmp_path` per test: `git_ops.list_commits`
does a blobless full-branch fetch (~25s/~130MB for `w1-28` the first time,
confirmed live), and re-running that per test would make this suite
minutes slower for no additional coverage -- a second fetch against an
already-populated mirror is a fast no-op (also confirmed live).
"""
from __future__ import annotations

import pytest

from registry import resolver

pytestmark = pytest.mark.network

_COUNTRY = "w1"


@pytest.fixture(scope="module")
def _mirror(tmp_path_factory):
    return tmp_path_factory.mktemp("resolver-mirror")


def test_resolve_exact_version_string(_mirror):
    # Real, fixed build on the real w1-28 branch (same commit test_git_ops.py
    # anchors on) -- confirmed live via `git log` against the real upstream
    # mirror during development of this module.
    #
    # UPDATE 2026-07-31: the expected commit_sha below changed from the
    # original e94dbd8173ef42cfa4883983eb07c758b13c749f. Confirmed live via
    # the GitHub API that BOTH shas exist with the identical commit message
    # and author timestamp -- the upstream mirror's w1-28 branch history was
    # rewritten at some point, and the old sha is no longer reachable from
    # the current tip (still directly fetchable by sha, which is exactly
    # what test_resolve_exact_commit_sha below still verifies -- it's the
    # walked-branch-log path in resolve_version that now finds the new one).
    # resolve_version's own ambiguous-match guard (see resolver.py's "Not
    # observed live, but never silently pick one" comment) correctly did
    # NOT fire here, because only one of the two duplicates is actually in
    # the branch's current walked history -- this is real upstream mirror
    # drift, not a resolver bug.
    #
    # UPDATE 2026-08-02: changed again, from 312dd91685771271372d53a8350ca
    # 168633c3889 to 88503b3fb425952d8b67a467b70bc926af4d8f45. Confirmed
    # live (this session) via `git log --all --format='%H %s'` against the
    # real upstream mirror: the branch was rewritten again since 2026-07-31,
    # and a real, live CI run picked up the new value independently of any
    # local state -- same expected-drift situation as before, not a
    # resolver bug.
    #
    # UPDATE 2026-08-03: changed again, from 88503b3fb425952d8b67a467b70
    # bc926af4d8f45 to 1ff2c27ddc1358f24f54d34a8f7f9e6429f5c2ad. Confirmed
    # live via a fresh `git clone --bare` of the real upstream mirror --
    # same expected-drift situation, caught by a real CI run on PR #15.
    #
    # UPDATE 2026-08-05: changed again, from 1ff2c27ddc1358f24f54d34a8f7f
    # 9e6429f5c2ad to 5a1047abc2924b31e03fb78ab1e8ec1bfe3eb638. Confirmed
    # live via a fresh `git clone --bare` of the real upstream mirror --
    # same expected-drift situation, caught by a real CI run on PR #16.
    #
    # UPDATE 2026-08-12: changed again, from 5a1047abc2924b31e03fb78ab1e8
    # ec1bfe3eb638 to 2a482cf89989c0219d75937232ef828b78a314eb. Confirmed
    # live via a fresh `git clone --bare` of the real upstream mirror --
    # same expected-drift situation, caught by a real CI run on PR #22.
    #
    # UPDATE 2026-08-13: changed again, from 2a482cf89989c0219d75937232e
    # f828b78a314eb to 136774729359fdb059e05db27847e5411d8ae1b8. Same
    # expected-drift situation, caught by a real CI run on PR #34.
    #
    # UPDATE 2026-08-19: changed again, from 136774729359fdb059e05db27847
    # e5411d8ae1b8 to 88c5353d392ec7d2d682a6fd2a73f74d9028289a. Same
    # expected-drift situation, caught by a real CI run on PR #54.
    spec = "w1-28.1.49838.51992"

    result = resolver.resolve_version(_COUNTRY, spec, mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolvedVersion)
    assert result.resolved is True
    assert result.country == _COUNTRY
    assert result.version_string == spec
    assert result.commit_sha == "88c5353d392ec7d2d682a6fd2a73f74d9028289a"


def test_resolve_exact_commit_sha(_mirror):
    sha = "e94dbd8173ef42cfa4883983eb07c758b13c749f"

    result = resolver.resolve_version(_COUNTRY, sha, mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolvedVersion)
    assert result.resolved is True
    assert result.commit_sha == sha
    assert result.version_string == "w1-28.1.49838.51992"


def test_resolve_loose_major_minor_picks_single_highest_build(_mirror):
    result = resolver.resolve_version(_COUNTRY, "28.1", mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolvedVersion)
    assert result.resolved is True
    assert result.country == _COUNTRY
    # Real highest w1-28.1.* build, confirmed live via
    # `git log --format='%s' | grep '^w1-28\\.1\\.' | sort` against the real
    # upstream mirror. UPDATE 2026-07-31, then again 2026-08-02, then again
    # 2026-08-03, then again 2026-08-05, then again 2026-08-12, then again
    # 2026-08-13, then again 2026-08-19: new builds keep landing upstream --
    # expected drift (module docstring), not a code bug.
    assert result.version_string == "w1-28.1.49838.53731"
    assert result.version_string.startswith("w1-28.1.")


def test_resolve_not_found_for_unknown_version_string(_mirror):
    result = resolver.resolve_version(_COUNTRY, "w1-28.1.00000.00000", mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolutionFailure)
    assert result.resolved is False
    assert result.reason == "not_found"


def test_resolve_not_found_for_unknown_country(_mirror):
    result = resolver.resolve_version("not-a-real-country", "28.1", mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolutionFailure)
    assert result.resolved is False
    assert result.reason == "not_found"


def test_resolve_not_found_for_unrecognized_spec_shape(_mirror):
    result = resolver.resolve_version(_COUNTRY, "not-a-real-version", mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolutionFailure)
    assert result.resolved is False
    assert result.reason == "not_found"


def test_resolve_major_only_spec_is_ambiguous(_mirror):
    # "28" alone matches multiple real minor versions (28.1, 28.2, ...) on
    # the real w1-28 branch -- the canonical "too loose to resolve to
    # exactly one build" edge case (spec.md Edge Cases) -- MUST fail
    # explicitly, never silently pick one.
    result = resolver.resolve_version(_COUNTRY, "28", mirror_dir=_mirror)

    assert isinstance(result, resolver.ResolutionFailure)
    assert result.resolved is False
    assert result.reason == "ambiguous"
    # Real ancestor-sharing bug caught live during T009 verification: the
    # w1-28 branch's git history transitively contains every earlier
    # major's own build commits too (w1-28 was cut from a point in w1-27's
    # history, which was cut from w1-26's, and so on -- confirmed live,
    # ~4075 commits reachable from the w1-28 ref, not just its own ~100-ish
    # major-28 builds). A naive implementation reports ALL of those earlier
    # majors as "matching minor versions" for a major-only spec of "28" --
    # this MUST only ever mention major 28's own minors.
    assert "28.1" in result.detail
    assert "28.2" in result.detail
    assert "24.0" not in result.detail
    assert "23.5" not in result.detail


def test_list_countries_includes_real_countries(_mirror):
    countries = resolver.list_countries(mirror_dir=_mirror)

    assert "w1" in countries
    assert "us" in countries
    # Never the raw branch names themselves.
    assert "w1-28" not in countries
    assert len(countries) == len(set(countries))


def test_list_major_versions_summarizes_by_major_minor(_mirror):
    versions = resolver.list_major_versions(_COUNTRY, mirror_dir=_mirror)

    assert versions is not None
    major_minors = [v["major_minor"] for v in versions]
    assert "28.1" in major_minors
    assert "28.2" in major_minors
    entry = next(v for v in versions if v["major_minor"] == "28.1")
    # UPDATE 2026-07-31, then again 2026-08-02, then again 2026-08-03, then
    # again 2026-08-05, then again 2026-08-12, then again 2026-08-13, then
    # again 2026-08-19: expected drift, see
    # test_resolve_loose_major_minor_picks_single_highest_build above.
    assert entry["latest_build"] == "w1-28.1.49838.53731"


def test_list_major_versions_returns_none_for_unknown_country(_mirror):
    assert resolver.list_major_versions("not-a-real-country", mirror_dir=_mirror) is None
