"""Unit tests for serialization, Settings.load, and Container wiring.

* domain/serialization.to_jsonable round-trips enums (-> .value) and datetimes.
* Settings.load parses config/settings.yaml.
* Container under profile=onprem binds the on-prem placeholder adapters, and each bound
  adapter satisfies its runtime_checkable Protocol (structural parity).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tests.fixtures import sample_docs

from enterprise_kb import ports
from enterprise_kb.config import ConfiguredEmptyError, Container, Settings
from enterprise_kb.domain.models import (
    AuditEvent,
    Citation,
    Decision,
    GroundedAnswer,
    PrincipalKind,
)

CONFIG_PATH = "config/settings.yaml"

PORT_PROTOCOLS = {
    "retrieval": ports.RetrievalPort,
    "access_control": ports.AccessControlPort,
    "ingestion": ports.IngestionPort,
    "ledger": ports.FreshnessLedgerPort,
    "llm": ports.LLMPort,
    "grounding": ports.GroundingPort,
    "guardrail": ports.GuardrailPort,
    "redaction": ports.PIIRedactionPort,
    "agent_runtime": ports.AgentRuntimePort,
    "session": ports.SessionPort,
    "memory": ports.MemoryPort,
    "audit": ports.AuditSinkPort,
    "tracer": ports.ObservabilityTracerPort,
    "evaluation": ports.EvaluationGatePort,
    "registry": ports.AgentRegistryPort,
    "tool_catalog": ports.ToolCatalogPort,
}


# --------------------------------------------------------------------------- #
# to_jsonable
# --------------------------------------------------------------------------- #
def _to_jsonable():
    from enterprise_kb.domain.serialization import to_jsonable

    return to_jsonable


def test_to_jsonable_enum_becomes_value():
    to_jsonable = _to_jsonable()
    assert to_jsonable(PrincipalKind.GROUP) == "GROUP"
    assert to_jsonable(Decision.BLOCKED) == "blocked"


def test_to_jsonable_datetime_is_json_safe_string():
    to_jsonable = _to_jsonable()
    dt = datetime(2026, 6, 20, 8, 30, tzinfo=UTC)
    out = to_jsonable(dt)
    assert isinstance(out, str)
    assert json.loads(json.dumps(out)) == out
    assert "2026-06-20" in out


def test_to_jsonable_citation_roundtrips_through_json():
    to_jsonable = _to_jsonable()
    citation = sample_docs.PRIMARY_PASSAGE.citation
    out = to_jsonable(citation)
    assert isinstance(out, dict)
    assert out["document_id"] == citation.document_id
    assert out["page"] == citation.page
    text = json.dumps(out)
    assert json.loads(text)["document_id"] == citation.document_id


def test_to_jsonable_passage_includes_acl_tags():
    to_jsonable = _to_jsonable()
    out = to_jsonable(sample_docs.PRIMARY_PASSAGE)
    assert isinstance(out["acl_tags"], list)
    labels = {t["label"] for t in out["acl_tags"]}
    assert "dept:retail" in labels


def test_to_jsonable_grounded_answer_nested():
    to_jsonable = _to_jsonable()
    answer = GroundedAnswer(
        query="q",
        answer="a",
        citations=(Citation(document_id="d", title="t", uri="u", page=42),),
        confidence=0.9,
        requires_human_review=True,
    )
    out = to_jsonable(answer)
    reloaded = json.loads(json.dumps(out))
    assert reloaded["requires_human_review"] is True
    assert reloaded["citations"][0]["page"] == 42


def test_to_jsonable_audit_event_is_worm_serialisable():
    to_jsonable = _to_jsonable()
    event = AuditEvent(
        action="search",
        actor="analyst",
        decision=Decision.ALLOWED,
        redacted_prompt="[NRIC]",
        redacted_response="3 passage(s)",
        citations=(sample_docs.PRIMARY_PASSAGE.citation,),
    )
    reloaded = json.loads(json.dumps(to_jsonable(event)))
    assert reloaded["decision"] == "allowed"
    assert reloaded["action"] == "search"


# --------------------------------------------------------------------------- #
# Settings.load parses config/settings.yaml
# --------------------------------------------------------------------------- #
def test_settings_load_parses_yaml():
    settings = Settings.load(CONFIG_PATH)
    assert settings.region == "asia-southeast1"
    assert settings.corpus.ttl_days == 7
    assert settings.models.reasoning == "gemini-3.5-flash"
    assert settings.models.triage == "gemini-3.5-flash"
    assert set(PORT_PROTOCOLS) <= set(settings.adapters)


def test_settings_pins_models_to_allowed_ids():
    settings = Settings.load(CONFIG_PATH)
    assert settings.models.reasoning != "gemini-2.0-flash"
    assert settings.models.triage != "gemini-2.0-flash"
    assert settings.models.reasoning.startswith("gemini-3")


@pytest.mark.parametrize(("raw", "expected"), (("true", True), ("false", False)))
def test_grounding_flag_uses_strict_boolean_semantics(
    tmp_path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(
        "profile: local\nregion: ${KB_REGION:-asia-southeast1}\n"
        "grounding_enabled: ${KB_GROUNDING_ENABLED:-false}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KB_GROUNDING_ENABLED", raw)
    settings = Settings.load(config)
    assert settings.grounding_enabled is expected

    from enterprise_kb.adapters.gcp.gemini_grounding import (
        GeminiGoogleSearchGroundingAdapter,
    )

    assert GeminiGoogleSearchGroundingAdapter(settings).enabled is expected


@pytest.mark.parametrize("name", ("KB_GROUNDING_ENABLED", "KB_REGION"))
def test_secure_configured_empty_values_refuse(
    tmp_path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(
        "profile: local\nregion: ${KB_REGION:-asia-southeast1}\n"
        "grounding_enabled: ${KB_GROUNDING_ENABLED:-false}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(name, "")
    with pytest.raises(ConfiguredEmptyError, match=name):
        Settings.load(config)


def test_model_armor_rep_is_derived_from_selected_region() -> None:
    settings = replace(Settings(), region="europe-west4")
    assert settings.model_armor_host == "modelarmor.europe-west4.rep.googleapis.com"


# --------------------------------------------------------------------------- #
# Container binds on-prem adapters under profile=onprem, with structural parity.
# --------------------------------------------------------------------------- #
def _onprem_settings() -> Settings:
    return replace(Settings.load(CONFIG_PATH), profile="onprem")


def test_container_binds_onprem_adapters_with_protocol_parity():
    container = Container(_onprem_settings())
    for port_name, protocol in PORT_PROTOCOLS.items():
        adapter = getattr(container, port_name)
        assert isinstance(adapter, protocol), (
            f"on-prem adapter for '{port_name}' is not structurally a {protocol.__name__}"
        )


def test_container_falls_back_to_gcp_binding_when_profile_missing():
    # 'platform' profile is only defined for a few ports; the rest fall back to 'gcp'.
    settings = _onprem_settings()
    binding = settings.adapters["guardrail"]
    assert binding["onprem"].endswith("OnPremGuardrailAdapter")
    assert "gcp" in binding


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
