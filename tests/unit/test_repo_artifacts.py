"""Lock regeneration and immutable shared-package pins are executable contracts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCKFILES = tuple(sorted(path.name for path in REPO_ROOT.glob("requirements-*.lock")))
_LOCK_PIN = re.compile(r"^([a-z0-9-]+) @ git\+(\S+?)@(\S+)\s*$", re.M)
_DECLARED_PIN = re.compile(r"([a-z0-9-]+)(?:\[[^\]]*\])?\s*@\s*git\+\S+?@(v[0-9][^\s\"']*)")
_TAG_COMMIT_LINE = re.compile(
    r"^#\s+(?P<package>[a-z0-9-]+)\s+(?P<tag>v[0-9][^\s]*)\s*=\s*"
    r"(?P<commit>[0-9a-f]{40})\s*$"
)


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _locked_pins(text: str) -> dict[str, str]:
    return {name: ref for name, _url, ref in _LOCK_PIN.findall(text)}


def _declared_tags() -> dict[str, str]:
    return dict(_DECLARED_PIN.findall(_read("pyproject.toml")))


def _recorded_tags(text: str) -> dict[str, tuple[str, str]]:
    return {
        match["package"]: (match["tag"], match["commit"])
        for match in (_TAG_COMMIT_LINE.match(line) for line in text.splitlines())
        if match
    }


def test_the_expected_lockfiles_are_discovered() -> None:
    assert LOCKFILES == ("requirements-dev.lock", "requirements-gcp.lock")


def test_make_lock_reaches_every_shipped_lockfile_through_the_header_safe_script() -> None:
    makefile = _read("Makefile")
    recipe = re.search(r"^lock:.*?\n((?:\t.*\n)+)", makefile, re.M | re.S)
    assert recipe, "Makefile has no lock target"
    assert "scripts/lock.py" in recipe.group(1)
    script = _read("scripts/lock.py")
    assert all(f'"{name}"' in script for name in LOCKFILES)


@pytest.mark.parametrize("name", LOCKFILES)
def test_lockfile_commons_are_immutable_commits_with_truthful_tag_maps(name: str) -> None:
    text = _read(name)
    locked = _locked_pins(text)
    recorded = _recorded_tags(text)
    declared = _declared_tags()

    assert locked
    assert set(recorded) == set(locked)
    for package, ref in locked.items():
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{name}: {package} is pinned at {ref}"
        assert package in declared
        assert recorded[package] == (declared[package], ref)


def test_shared_package_commits_agree_between_lockfiles() -> None:
    by_file = {name: _locked_pins(_read(name)) for name in LOCKFILES}
    common = set.intersection(*(set(pins) for pins in by_file.values()))
    assert common
    for package in common:
        assert len({pins[package] for pins in by_file.values()}) == 1


def test_mutation_probe_sees_a_downgraded_commit() -> None:
    current = _read("requirements-dev.lock")
    pins = _locked_pins(current)
    original = pins["hex-service-kit"]
    mutated = current.replace(original, "4" * 40)
    assert _locked_pins(mutated)["hex-service-kit"] == "4" * 40
    assert _recorded_tags(mutated)["hex-service-kit"][1] == "4" * 40


def test_live_managed_smoke_walks_the_deployed_iap_journey_not_disabled_runtime() -> None:
    smoke = _read("tests/integration/test_gcp_smoke.py")
    assert "agent_runtime" not in smoke
    assert "GOOGLE_CLOUD_PROJECT" not in smoke
    for env_name in (
        "KB_MANAGED_BASE_URL",
        "KB_MANAGED_IAP_ID_TOKEN",
        "KB_MANAGED_EXPECTED_DOCUMENT_ID",
    ):
        assert env_name in smoke
    for path in ("/healthz", "/v1/corpus/status", "/v1/search", "/v1/answer"):
        assert path in smoke
