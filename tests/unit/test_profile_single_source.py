"""The profile has ONE source of truth, and it fails closed on an unset variable.

Mirrors Hrz7 (``human-review-console/tests/test_profile_single_source.py``) as the
standing gate for the absence-read-as-consent class. The remediation is only durable if no
other module re-derives the decision with its own permissive default: a single
``os.environ.get("KB_PROFILE", "local")`` anywhere in ``src`` reintroduces the whole class,
because it reads an UNSET variable as consent to the ``local`` relaxations (dev CORS origins,
no HSTS, seeded personas, the zero-secret S2S opening).
"""

from __future__ import annotations

import re
from pathlib import Path

from enterprise_kb.config import (
    RUNTIME_PROFILES,
    UNCONSENTED_PROFILE,
    ProfileError,
    Settings,
    resolve_profile,
)
from enterprise_kb.envread import ConfiguredEmptyError

_SRC = Path(__file__).resolve().parents[2] / "src" / "enterprise_kb"
_CONFIG = _SRC / "config.py"


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p != _CONFIG)


def test_only_the_resolver_reads_the_profile_variable_from_the_environment() -> None:
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(os\.environ|os\.getenv)[^\n]*PROFILE", line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "these modules re-derive the profile instead of calling config.resolve_profile, "
        "so an unset KB_PROFILE can again be read as consent:\n" + "\n".join(offenders)
    )


def test_the_resolver_treats_only_an_absent_variable_as_no_choice() -> None:
    choice = resolve_profile({})
    assert choice.explicit is False
    assert choice.service_auth_configured is False


def test_an_explicitly_empty_profile_refuses_instead_of_inheriting_absence() -> None:
    for environ in ({"KB_PROFILE": ""}, {"KB_PROFILE": "   "}):
        try:
            resolve_profile(environ)
        except ConfiguredEmptyError as exc:
            assert "KB_PROFILE" in str(exc)
        else:  # pragma: no cover - reached only if three-state resolution regresses
            raise AssertionError("an emptied KB_PROFILE was accepted as unset")


def test_an_unconsented_run_is_not_the_local_profile_for_any_relaxation() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.exposure_profile != "local"
    assert UNCONSENTED_PROFILE not in RUNTIME_PROFILES


def test_an_unconsented_run_still_binds_loopback() -> None:
    """The bind guard fails closed in the opposite direction: local is the restrictive case."""
    assert resolve_profile({}).bind_profile == "local"


def test_a_deliberate_profile_is_carried_through_unchanged() -> None:
    choice = resolve_profile({"KB_PROFILE": "gcp"})
    assert (choice.profile, choice.explicit) == ("gcp", True)
    assert choice.exposure_profile == "gcp"
    assert choice.bind_profile == "gcp"
    assert choice.service_auth_configured is True


def test_a_settings_file_profile_counts_as_a_deliberate_choice() -> None:
    """Someone wrote ``profile: gcp`` into settings.yaml; that IS a choice, unlike a blank."""
    chosen = resolve_profile({}, configured="gcp")
    assert (chosen.profile, chosen.explicit) == ("gcp", True)
    assert resolve_profile({}, configured="").explicit is False
    assert resolve_profile({}, configured="   ").explicit is False


def test_the_environment_variable_wins_over_the_settings_file() -> None:
    choice = resolve_profile({"KB_PROFILE": "onprem"}, configured="gcp")
    assert choice.profile == "onprem"


def test_an_unknown_or_miscapitalised_profile_is_refused_at_resolution() -> None:
    """Exact, case-sensitive: ``Local`` selects no relaxation but also no restriction."""
    for bogus in ("bogus", "Local", "GCP", "LOCAL"):
        try:
            resolve_profile({"KB_PROFILE": bogus})
        except ProfileError as exc:
            assert "KB_PROFILE" in str(exc)
        else:  # pragma: no cover - only reached if the guard regresses
            raise AssertionError(f"{bogus!r} was accepted as a profile")


def test_directly_constructed_settings_are_a_deliberate_choice() -> None:
    """A caller who named the profile in code consented to it; only ``load`` can be unsure."""
    assert Settings(profile="local").choice.explicit is True
    assert Settings(profile="local").choice.exposure_profile == "local"
    assert Settings(profile="local", profile_explicit=False).choice.exposure_profile != "local"
