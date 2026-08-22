"""An unset ``KB_PROFILE`` is refused, not read as consent to the ``local`` relaxations.

Before this fix ``Settings.load`` resolved the profile with
``os.environ.get("KB_PROFILE", raw.pop("profile", "local"))``, and the settings file's own
``${KB_PROFILE:-local}`` default said the same thing a second time. A process started with the
variable missing therefore served the whole ``local`` posture that nobody had chosen: the
localhost CORS fallback, no HSTS, the ``X-Dev-Persona`` seeded-persona identity seam, and the
commons S2S opening that leaves the governed data plane unauthenticated when ``KB_S2S_TOKEN``
is also unset.

Each case below is the fail-open it retires. The two directions are asserted separately
because a relaxation and a restriction must fail closed in OPPOSITE directions: the
unconsented run gets ``UNCONSENTED_PROFILE`` everywhere something is GRANTED to ``local``, and
``local`` everywhere something is WITHHELD from it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from hex_service_kit.netdefaults import cors_allowlist, resolve_bind_host

from enterprise_kb.adapters.local.identity import (
    LocalPersonaIdentityAdapter,
    LocalPersonaProfileError,
)
from enterprise_kb.api import deps, security
from enterprise_kb.config import UNCONSENTED_PROFILE, Settings

CONFIG_PATH = "config/settings.yaml"


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """``Settings`` as a process with no ``KB_PROFILE`` in its environment would load them."""
    monkeypatch.delenv("KB_PROFILE", raising=False)
    return Settings.load(CONFIG_PATH)


def test_the_shipped_settings_file_does_not_forge_a_choice(unconfigured: Settings) -> None:
    """``profile: ${KB_PROFILE}`` carries NO ``:-local`` default, so blank stays blank."""
    assert unconfigured.profile == "local", "the SDK-free adapters are still what gets bound"
    assert unconfigured.profile_explicit is False, "but nobody chose that posture"


def test_a_deliberate_choice_is_still_a_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_PROFILE", "local")
    settings = Settings.load(CONFIG_PATH)
    assert (settings.profile, settings.profile_explicit) == ("local", True)


def test_seeded_personas_refuse_to_serve_an_unconfigured_run(unconfigured: Settings) -> None:
    """The identity seam is an UNAUTHENTICATED grant, so it needs a deliberate local."""
    with pytest.raises(LocalPersonaProfileError) as excinfo:
        LocalPersonaIdentityAdapter(unconfigured)
    assert "KB_PROFILE" in str(excinfo.value)


def test_seeded_personas_still_serve_a_deliberate_local_run() -> None:
    adapter = LocalPersonaIdentityAdapter(Settings(profile="local"))
    assert adapter.personas(), "the offline demo posture must be unchanged"


def test_the_cors_dev_origin_fallback_is_withheld(unconfigured: Settings) -> None:
    """A relaxation: granted to a chosen ``local``, withheld from an unconfigured run."""
    exposure = unconfigured.choice.exposure_profile
    assert cors_allowlist(exposure, origins_env="KB_CORS_ORIGINS") == []
    assert cors_allowlist("local", origins_env="KB_CORS_ORIGINS") != []


def test_the_loopback_bind_is_still_applied(unconfigured: Settings, tmp_path: Any) -> None:
    """A restriction: ``local`` is the CONFINED case, so the unconsented run must look local."""
    host = resolve_bind_host(
        unconfigured.choice.bind_profile,
        host_env="KB_API_HOST_UNSET_FOR_TEST",
        insecure_demo_env="KB_ALLOW_INSECURE_DEMO_UNSET_FOR_TEST",
    )
    assert host == "127.0.0.1"


def test_the_two_directions_disagree_on_purpose(unconfigured: Settings) -> None:
    choice = unconfigured.choice
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.bind_profile == "local"


def _s2s_request() -> Any:
    class _Req:
        headers: dict[str, str] = {}

    return _Req()


def test_the_s2s_seam_refuses_before_it_picks_a_scheme(
    unconfigured: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no profile chosen, no scheme was chosen, so the answer is 401 not "open"."""
    monkeypatch.delenv("KB_S2S_TOKEN", raising=False)
    monkeypatch.setattr(deps, "get_settings", lambda: unconfigured)
    with pytest.raises(HTTPException) as excinfo:
        security.require_service_caller(_s2s_request())
    assert excinfo.value.status_code == 401
    assert "KB_PROFILE" in str(excinfo.value.detail)


def test_the_zero_secret_offline_posture_survives_a_deliberate_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline gate runs with no secrets, and this fix must not take that away."""
    monkeypatch.delenv("KB_S2S_TOKEN", raising=False)
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(profile="local"))
    security.require_service_caller(_s2s_request())
