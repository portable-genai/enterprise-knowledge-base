"""The model pill's server half: this service names its runtime and its model.

Every served UI in the fleet shows, at the top right of every page, the model that answers:
before any answer, the configured model from here with where it runs in the pill's title,
then the model that answered (``X-Answered-By``, pinned in ``test_answer_provenance.py``).
The console must never infer either value. A page that read its runtime from
``window.location`` would be right until the deployment served through a proxy, and wrong
silently after that; a page that hard-coded a model name would keep printing it after the
binding changed.

So the service answers, and the answer is DERIVED rather than kept as a second field
someone has to remember to update. That is what these tests pin.
"""

from __future__ import annotations

import dataclasses

import pytest
from hex_service_kit.localmodel import DEFAULT_LOCAL_MODEL

from enterprise_kb.config import Settings

CONFIG_PATH = "config/settings.yaml"


@pytest.fixture
def settings() -> Settings:
    return Settings.load(CONFIG_PATH)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", "local"),
        ("live", "local"),
        ("gcp", "gcp"),
        ("platform", "gcp"),
        ("onprem", "local"),
    ],
)
def test_the_runtime_says_where_the_process_runs_not_whose_model_it_calls(
    settings: Settings, profile: str, expected: str
) -> None:
    """``onprem`` reads ``local``, and there that is the whole selling point.

    The pill's title states WHERE the process runs, and its text states WHOSE model answers,
    precisely so the two facts cannot be collapsed into one misleading sentence.
    """
    assert dataclasses.replace(settings, profile=profile).runtime == expected


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", "deterministic-offline-stub"),
        ("live", DEFAULT_LOCAL_MODEL),
        ("gcp", "gemini-3.5-flash"),
        ("platform", "gemini-3.5-flash"),
        ("onprem", "onprem-not-implemented"),
    ],
)
def test_the_model_answers_what_the_profile_actually_binds(
    settings: Settings, profile: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCAL_MODEL", raising=False)
    assert dataclasses.replace(settings, profile=profile).generator_model == expected
