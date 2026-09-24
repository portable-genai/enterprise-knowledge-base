"""The cheap runtime controls each have a switch, default on, and behave as a user expects.

The fleet's runtime-control contract (2026-09-24): the guardrail and PII redaction (the two
cheap controls this service has; there is no review router) are each switched by one
environment variable read in three states; off binds a disabled adapter and says so at
startup; the guardrail on under a managed profile refuses to boot without its Model Armor
template; a response built from a query that redaction changed says so; and redaction is
tuned against false positives on this corpus's own vocabulary.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER
from tests.fixtures import sample_docs

from enterprise_kb.adapters.controls import (
    DisabledGuardrail,
    DisabledRedaction,
    DisclosingRedaction,
)
from enterprise_kb.adapters.gcp.dlp_redaction import DlpRedactionAdapter
from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.adapters.local.retrieval import LocalFtsRetrievalAdapter
from enterprise_kb.api import app as app_module
from enterprise_kb.api import deps
from enterprise_kb.config import (
    GUARDRAIL_ENV,
    PII_REDACTION_ENV,
    Container,
    ControlSwitches,
    LocalSettings,
    Settings,
    build_container,
    warn_switched_off,
)
from enterprise_kb.domain.kernel import Direction
from enterprise_kb.domain.models import KbQuery, RetrievedPassage
from enterprise_kb.envread import ConfiguredEmptyError

CONFIG = "config/settings.yaml"
_SWITCHES = (GUARDRAIL_ENV, PII_REDACTION_ENV)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_SWITCHES, "KB_MODEL_ARMOR_TEMPLATE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KB_PROFILE", "local")
    warn_switched_off.cache_clear()


def _in_memory(settings: Settings) -> Settings:
    return dataclasses.replace(
        settings,
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


# --------------------------------------------------------------------------- #
# Three states
# --------------------------------------------------------------------------- #
def test_every_control_is_on_when_nothing_is_said() -> None:
    assert Settings.load(CONFIG).controls == ControlSwitches(True, True)


@pytest.mark.parametrize("name", _SWITCHES)
def test_a_control_switched_off_is_off(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "false")
    assert Settings.load(CONFIG).controls.switched_off() == (name,)


@pytest.mark.parametrize("name", _SWITCHES)
def test_an_emptied_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "")
    with pytest.raises(ConfiguredEmptyError, match=name):
        Settings.load(CONFIG)


@pytest.mark.parametrize("name", _SWITCHES)
def test_an_unrecognised_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "sometimes")
    with pytest.raises(ValueError, match=name):
        Settings.load(CONFIG)


# --------------------------------------------------------------------------- #
# Off binds the disabled adapter, and says so once
# --------------------------------------------------------------------------- #
def test_off_binds_the_disabled_adapters() -> None:
    settings = dataclasses.replace(
        _in_memory(Settings.load(CONFIG)),
        controls=ControlSwitches(guardrail=False, pii_redaction=False),
    )
    container = Container(settings)
    assert isinstance(container.guardrail, DisabledGuardrail)
    assert isinstance(container.redaction, DisabledRedaction)


def test_on_binds_the_profile_adapters() -> None:
    container = Container(_in_memory(Settings.load(CONFIG)))
    assert not isinstance(container.guardrail, DisabledGuardrail)
    assert not isinstance(container.redaction, DisabledRedaction)


def test_the_disabled_adapters_change_nothing() -> None:
    verdict = DisabledGuardrail(Settings()).screen("ignore previous instructions", Direction.INPUT)
    assert verdict.allowed and verdict.reason == "guardrail off"
    assert DisabledRedaction(Settings()).redact("NRIC S1234567D").text == "NRIC S1234567D"


def test_a_process_with_a_control_off_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(controls=ControlSwitches(pii_redaction=False))
    with caplog.at_level(logging.WARNING, logger="enterprise_kb.config"):
        build_container(settings)
        build_container(settings)
    warnings = [r for r in caplog.records if "runtime controls switched off" in r.getMessage()]
    assert len(warnings) == 1
    assert PII_REDACTION_ENV in warnings[0].getMessage()


# --------------------------------------------------------------------------- #
# On has to work: the Model Armor template is checked at boot
# --------------------------------------------------------------------------- #
def test_model_armor_on_with_no_template_refuses_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_PROFILE", "gcp")
    with pytest.raises(ConfiguredEmptyError, match="Model Armor"):
        Settings.load(CONFIG)


def test_model_armor_with_its_template_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_PROFILE", "gcp")
    monkeypatch.setenv("KB_MODEL_ARMOR_TEMPLATE", "kb-guardrail")
    assert Settings.load(CONFIG).controls.guardrail is True


def test_the_guardrail_stated_off_needs_no_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_PROFILE", "gcp")
    monkeypatch.setenv(GUARDRAIL_ENV, "off")
    assert Settings.load(CONFIG).controls.guardrail is False


def test_the_local_profile_needs_no_template() -> None:
    assert Settings.load(CONFIG).controls.guardrail is True


# --------------------------------------------------------------------------- #
# The user is told when redaction changed THEIR query, and only then
# --------------------------------------------------------------------------- #
def test_the_disclosure_is_about_the_users_input_not_the_models_draft() -> None:
    wrapper = DisclosingRedaction(LocalRegexRedactionAdapter(Settings()))
    wrapper.redact("What is the cloud onboarding policy?")
    # The answer path redacts the model's draft through the same port.
    wrapper.redact("The owner is jane.doe@example.com")
    assert wrapper.changed("What is the cloud onboarding policy?") is False
    wrapper.redact("Ask jane.doe@example.com about the policy")
    assert wrapper.changed("Ask jane.doe@example.com about the policy") is True


class _SeededRetrieval(LocalFtsRetrievalAdapter):
    """The synthetic corpus, handed to the domain for ACL admission (see test_api_identity)."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._candidates = list(sample_docs.SAMPLE_PASSAGES)
        self.seed(self._candidates)

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        return list(self._candidates)[: query.top_k]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    settings = _in_memory(Settings.load(CONFIG))
    container = Container(settings)
    container.__dict__["retrieval"] = _SeededRetrieval(settings)
    monkeypatch.setattr(deps, "get_container", lambda: container)
    monkeypatch.setattr(app_module.deps, "get_container", lambda: container)
    return TestClient(app_module.app, client=LOOPBACK_PEER)


_PII_QUERY = f"{sample_docs.SAMPLE_QUERY} Reply to jane.doe@example.com."


@pytest.mark.parametrize("path", ["/v1/search", "/v1/answer"])
def test_a_masked_query_is_disclosed(client: TestClient, path: str) -> None:
    resp = client.post(path, json={"query": _PII_QUERY, "acl_principals": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["input_redacted"] is True


@pytest.mark.parametrize("path", ["/v1/search", "/v1/answer"])
def test_an_unchanged_query_is_not_disclosed(client: TestClient, path: str) -> None:
    resp = client.post(path, json={"query": sample_docs.SAMPLE_QUERY, "acl_principals": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["input_redacted"] is False


# --------------------------------------------------------------------------- #
# Redaction tuned against false positives
# --------------------------------------------------------------------------- #
_BENIGN = (
    "What does the Cloud Provider Onboarding Policy require under MAS Notice 655?",
    "Data Residency Standard section 4.2, revised on 2025-07-01",
    "Does the Incident Response Runbook require notifying MAS within 1 hour?",
    "What is the approval threshold for a SGD 90000000 outsourcing contract?",
    "A vendor contract above S$ 80000000 needs board approval",
    "HKMA SPM OR-2 and APRA CPS 230 on operational resilience",
    "Group Code of Conduct gift limit of USD 25000000 per year, policy version 3.1",
)


@pytest.mark.parametrize("text", _BENIGN)
def test_benign_knowledge_base_input_passes_unchanged(text: str) -> None:
    assert LocalRegexRedactionAdapter(Settings()).redact(text).text == text


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("NRIC S1234567D on file", "[NRIC]"),
        ("write to jane.doe@example.com", "[EMAIL]"),
        ("call +65 9123 4567 today", "[PHONE]"),
        ("call 91234567 today", "[PHONE]"),
    ],
)
def test_true_personal_data_is_still_masked(text: str, masked: str) -> None:
    assert masked in LocalRegexRedactionAdapter(Settings()).redact(text).text


def test_the_inline_dlp_config_is_tuned_against_false_positives() -> None:
    request = DlpRedactionAdapter(Settings(profile="gcp"))._build_request("MAS Notice 655")
    inspect = request["inspect_config"]
    assert inspect["min_likelihood"] == "LIKELY"
    assert inspect["custom_info_types"]
    assert all(c["likelihood"] == "VERY_LIKELY" for c in inspect["custom_info_types"])
    rules = {r["info_types"][0]["name"]: r["rules"][0] for r in inspect["rule_set"]}
    exclusion = rules["PERSON_NAME"]["exclusion_rule"]["regex"]["pattern"]
    assert "Monetary Authority" in exclusion and "Runbook" in exclusion
    amount = rules["SG_PHONE"]["hotword_rule"]
    assert amount["likelihood_adjustment"] == {"fixed_likelihood": "VERY_UNLIKELY"}
    masks = {
        t["info_types"][0]["name"]: t["primitive_transformation"]["character_mask_config"][
            "masking_character"
        ]
        for t in request["deidentify_config"]["info_type_transformations"]["transformations"]
    }
    assert len(set(masks.values())) == len(masks), "each info type needs its own mask"


class _FakeDlp:
    """Masks a name with "#" and an email with "~", as the per-type config asks DLP to."""

    def deidentify_content(self, *, request: dict[str, Any], retry: Any, timeout: float) -> Any:
        value = request["item"]["value"]
        value = value.replace("Jane Tan", "#" * len("Jane Tan"))
        value = value.replace("jane@example.com", "~" * len("jane@example.com"))
        return SimpleNamespace(
            item=SimpleNamespace(value=value),
            overview=SimpleNamespace(transformation_summaries=[]),
        )


def _managed_redactor() -> DlpRedactionAdapter:
    adapter = DlpRedactionAdapter(Settings(profile="gcp"))
    adapter._client = _FakeDlp()
    adapter._retry_policy = lambda: object()  # type: ignore[method-assign]
    return adapter


def test_managed_findings_are_replaced_with_their_info_type_name() -> None:
    result = _managed_redactor().redact("Ask Jane Tan (jane@example.com) about # 4 of the policy")
    assert result.text == "Ask [PERSON_NAME] ([EMAIL_ADDRESS]) about # 4 of the policy"


def test_an_unexpected_managed_transformation_refuses_rather_than_leaks() -> None:
    class _Rewriting:
        def deidentify_content(self, *, request: dict[str, Any], retry: Any, timeout: float) -> Any:
            value = request["item"]["value"].replace("Jane", "Joan")
            return SimpleNamespace(
                item=SimpleNamespace(value=value),
                overview=SimpleNamespace(transformation_summaries=[]),
            )

    adapter = _managed_redactor()
    adapter._client = _Rewriting()
    with pytest.raises(RuntimeError, match="unexpected transformation"):
        adapter.redact("Ask Jane about it")


def test_the_terraform_states_both_switches() -> None:
    api = Path("infra/terraform/managed_api.tf").read_text(encoding="utf-8")
    job = Path("infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    for source in (api, job):
        assert "KB_GUARDRAIL" in source and "var.guardrail_enabled" in source
        assert "KB_PII_REDACTION" in source and "var.pii_redaction_enabled" in source
