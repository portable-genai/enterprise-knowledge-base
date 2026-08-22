"""Three-state behavior for application settings that have documented defaults."""

from pathlib import Path

import pytest

from enterprise_kb.config import Settings, _interpolate, resolve_profile
from enterprise_kb.envread import ConfiguredEmptyError, setting_or_default


def test_setting_default_applies_only_when_variable_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KB_TEST_UPSTREAM_URL", raising=False)
    assert setting_or_default("KB_TEST_UPSTREAM_URL", "http://localhost:9999") == (
        "http://localhost:9999"
    )

    monkeypatch.setenv("KB_TEST_UPSTREAM_URL", " https://upstream.fictional.example ")
    assert setting_or_default("KB_TEST_UPSTREAM_URL", "http://localhost:9999") == (
        "https://upstream.fictional.example"
    )


def test_configured_empty_value_cannot_inherit_a_nonempty_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KB_TEST_UPSTREAM_URL", "   ")

    with pytest.raises(ConfiguredEmptyError, match="KB_TEST_UPSTREAM_URL"):
        setting_or_default("KB_TEST_UPSTREAM_URL", "http://localhost:9999")
    with pytest.raises(ConfiguredEmptyError, match="KB_TEST_UPSTREAM_URL"):
        _interpolate("${KB_TEST_UPSTREAM_URL:-http://localhost:9999}")


def test_configured_empty_optional_value_refuses_instead_of_becoming_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KB_TEST_OPTIONAL", "")
    with pytest.raises(ConfiguredEmptyError, match="KB_TEST_OPTIONAL"):
        _interpolate("${KB_TEST_OPTIONAL:-}")

    monkeypatch.delenv("KB_TEST_OPTIONAL")
    assert _interpolate("${KB_TEST_OPTIONAL:-}") == ""


def test_configured_empty_profile_and_settings_path_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfiguredEmptyError, match="KB_PROFILE"):
        resolve_profile({"KB_PROFILE": ""})

    monkeypatch.setenv("KB_SETTINGS", "")
    with pytest.raises(ConfiguredEmptyError, match="KB_SETTINGS"):
        Settings.load()


def test_explicit_settings_path_does_not_read_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("profile: local\n", encoding="utf-8")
    monkeypatch.setenv("KB_SETTINGS", "")

    assert Settings.load(path).profile == "local"
