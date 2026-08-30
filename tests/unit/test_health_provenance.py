"""The banner's server half: this service names its runtime and its model.

Every served UI in the fleet states, at the top of every page, where it is running and
which model answers (org decision, 2026-08-30). The console must never infer either. A
page that read its runtime from ``window.location`` would be right until the deployment
served through a proxy, and wrong silently after that; a page that hard-coded a model name
would keep printing it after the binding changed.

So the service answers, and the answer is DERIVED rather than kept as a second field
someone has to remember to update. That is what these tests pin.
"""

from __future__ import annotations

import dataclasses

import pytest

from enterprise_kb.config import Settings

CONFIG_PATH = "config/settings.yaml"


@pytest.fixture
def settings() -> Settings:
    return Settings.load(CONFIG_PATH)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("local", "local"), ("gcp", "gcp"), ("platform", "gcp"), ("onprem", "local")],
)
def test_the_runtime_says_where_the_process_runs_not_whose_model_it_calls(
    settings: Settings, profile: str, expected: str
) -> None:
    """``onprem`` reads ``local``, and there that is the whole selling point.

    The banner states WHERE the process runs, and the model half states WHOSE model
    answers, precisely so the two facts cannot be collapsed into one misleading sentence.
    """
    assert dataclasses.replace(settings, profile=profile).runtime == expected


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", "deterministic-offline-stub"),
        ("gcp", "gemini-3.5-flash"),
        ("platform", "gemini-3.5-flash"),
        ("onprem", "onprem-not-implemented"),
    ],
)
def test_the_model_answers_what_the_profile_actually_binds(
    settings: Settings, profile: str, expected: str
) -> None:
    assert dataclasses.replace(settings, profile=profile).generator_model == expected
