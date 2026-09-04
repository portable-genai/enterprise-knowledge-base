"""Contract tests: the ``onprem`` and ``local`` adapters are structural parity of the ports.

For every port the catalog declares, this iterates the adapter map and, for both the
``onprem`` and ``local`` profiles, imports + constructs the bound class (which must build
cleanly with **no Google Cloud SDK** installed), then asserts:

  1. the constructed instance satisfies its runtime_checkable Protocol (isinstance), and
  2. every method/property the Protocol declares actually exists on the instance.

It additionally proves the two profiles' distinct contracts:

* ``onprem`` is the fail-fast Google Distributed Cloud migration target: every method
  raises ``NotImplementedError`` (proven on a representative port), and
* ``local`` is a WORKING offline stack: the same ports construct and retrieve in-process.

This is the proof of the ports-and-adapters / no-lock-in promise (P-02): the on-prem
migration target and the offline local stack implement the exact same interface as the
managed GCP stack.
"""

from __future__ import annotations

from typing import Protocol, get_type_hints

import pytest

from enterprise_kb import config, ports
from enterprise_kb.config import Container, LocalSettings, Settings, instantiate

CONFIG_PATH = "config/settings.yaml"

# Every port name in settings.adapters mapped to its Protocol.
PORT_PROTOCOLS: dict[str, type] = {
    "retrieval": ports.RetrievalPort,
    "access_control": ports.AccessControlPort,
    "ingestion": ports.IngestionPort,
    "citation_store": ports.CitationStorePort,
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
    "identity": ports.IdentityPort,
}

# Profiles whose adapters must construct + satisfy the Protocols with no GCP SDK.
SDK_FREE_PROFILES = ("onprem", "local")


def _settings(profile: str) -> Settings:
    base = Settings.load(CONFIG_PATH)
    # Point the local stores at in-memory SQLite so the contract test stays ephemeral.
    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile=profile,
        kms_key=base.kms_key,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        storage=base.storage,
        alloydb=base.alloydb,
        model_armor=base.model_armor,
        logging=base.logging,
        agent_engine=base.agent_engine,
        corpus=base.corpus,
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
        adapters=base.adapters,
    )


def _protocol_members(protocol: type) -> set[str]:
    """The attribute names a Protocol declares (methods + properties), no dunders."""
    members = set(getattr(protocol, "__protocol_attrs__", set()))
    if not members:
        # Fallback for older typing internals: union of annotations + callables.
        members |= set(get_type_hints(protocol).keys())
        for name in dir(protocol):
            if name.startswith("_"):
                continue
            members.add(name)
    return {m for m in members if not m.startswith("_")}


def test_port_protocols_matches_settings_adapters():
    """The hand-maintained PORT_PROTOCOLS map must EQUAL the ports bound in settings.

    The other tests parametrise over ``PORT_PROTOCOLS``, so a port that is bound in
    ``config/settings.yaml`` but missing from that map silently gets ZERO parity /
    constructor / onprem-binding enforcement while CI stays green (invisible drift). This
    is the reverse of ``test_every_port_has_onprem_and_local_bindings``: that guards
    PORT_PROTOCOLS -> settings; this guards settings -> PORT_PROTOCOLS. Together they pin
    the two sets equal, so drift in EITHER direction fails loudly:

    * a new ``adapters:`` binding with no PORT_PROTOCOLS entry (untested), and
    * a PORT_PROTOCOLS entry with no ``adapters:`` binding (dangling Protocol).
    """
    settings = Settings.load(CONFIG_PATH)
    bound = set(settings.adapters)
    declared = set(PORT_PROTOCOLS)
    missing_from_map = bound - declared
    missing_from_settings = declared - bound
    assert not missing_from_map, (
        f"ports bound in settings.adapters but absent from PORT_PROTOCOLS "
        f"(so untested): {sorted(missing_from_map)}. Add them to the parity map."
    )
    assert not missing_from_settings, (
        f"ports in PORT_PROTOCOLS with no settings.adapters binding: "
        f"{sorted(missing_from_settings)}."
    )


def test_every_port_has_an_explicit_binding_for_every_profile():
    settings = Settings.load(CONFIG_PATH)
    for port_name in PORT_PROTOCOLS:
        binding = settings.adapters.get(port_name, {})
        missing = set(config.RUNTIME_PROFILES) - set(binding)
        assert not missing, f"port '{port_name}' has no explicit bindings for {sorted(missing)}"


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(KeyError, match="profile 'misspelled'"):
        _ = Container(_settings("misspelled")).retrieval


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_satisfies_protocol(profile: str, port_name: str):
    settings = _settings(profile)
    protocol = PORT_PROTOCOLS[port_name]
    dotted = settings.adapters[port_name][profile]

    # Import + construct with only Settings (the adapter convention), no GCP SDK.
    adapter = instantiate(dotted, settings)

    # 1. Structural conformance via runtime_checkable Protocol.
    assert isinstance(adapter, protocol), (
        f"{dotted} does not structurally satisfy {protocol.__name__}"
    )

    # 2. Every declared Protocol member exists. Check on the *class* (via the MRO), not
    #    the instance: a placeholder property getter may raise, so ``hasattr`` would
    #    wrongly report it missing. Looking the name up on the type tests for declaration
    #    without invoking the getter.
    members = _protocol_members(protocol)
    declared = set().union(*(vars(klass) for klass in type(adapter).__mro__))
    for member in members:
        assert member in declared, (
            f"{dotted} is missing port method/attr '{member}' of {protocol.__name__}"
        )


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_constructs_with_single_settings_arg(profile: str, port_name: str):
    """The build contract: every adapter is ``Adapter(settings: Settings)``."""
    settings = _settings(profile)
    dotted = settings.adapters[port_name][profile]
    module_path, _, class_name = dotted.partition(":")
    import importlib

    cls = getattr(importlib.import_module(module_path), class_name)
    # Must accept exactly one positional Settings argument and build cleanly.
    instance = cls(settings)
    assert instance is not None


def test_onprem_retrieval_fails_fast():
    """The on-prem stubs are fail-fast: a representative port raises NotImplementedError."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["retrieval"]["onprem"], settings)
    from enterprise_kb.domain.models import KbQuery

    with pytest.raises(NotImplementedError):
        adapter.retrieve(KbQuery(text="anything"))


def test_local_retrieval_returns_real_acl_tagged_passages():
    """The local stack is WORKING: retrieval returns real, page-cited, ACL-tagged passages."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["retrieval"]["local"], settings)
    from enterprise_kb.domain.models import KbQuery

    passages = adapter.retrieve(KbQuery(text="cloud onboarding due diligence", top_k=5))
    assert passages, "local FTS5 retrieval returned nothing for the seeded corpus"
    assert all(p.citation.page is not None for p in passages), "page-level citation required"
    assert all(p.acl_tags for p in passages), "local passages must carry ACL tags for P-09"


def test_local_access_control_resolves_seeded_principal():
    """The local access-control directory resolves a seeded principal to real ACL tags."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["access_control"]["local"], settings)
    tags = adapter.resolve(["user:jane@bank.test"], "demo-bank")
    assert tags, "seeded principal must resolve to at least one ACL tag"
    # An unknown principal resolves to no tags (access-denied, fail-closed).
    assert adapter.resolve(["user:nobody@nowhere.test"], "demo-bank") == set()


def test_tracer_port_and_token_usage_are_the_commons_objects_not_copies():
    """Object IDENTITY with ``hex-service-kit``, which structural checks cannot see.

    Every other test in this file asks whether an adapter SATISFIES the tracer Protocol, and a
    hand-copied Protocol answers yes: ``isinstance`` against a ``runtime_checkable`` Protocol
    is satisfied by a look-alike just as happily as by the real one, and ``TokenUsage`` was
    three int fields that any copy reproduces. That is how sixteen repositories each grew their
    own version of these and drifted apart without a single red test.

    ``is`` cannot be satisfied by a copy. If someone redeclares either name in this package,
    this fails immediately and names the file to delete.
    """
    from hex_service_kit import observability as commons

    from enterprise_kb.domain import kernel, models

    assert ports.ObservabilityTracerPort is commons.ObservabilityTracerPort
    assert ports.TokenUsage is commons.TokenUsage
    assert kernel.TokenUsage is commons.TokenUsage
    assert models.TokenUsage is commons.TokenUsage


def test_identity_values_are_the_commons_objects_not_copies():
    """enterprise-knowledge-base must consume the shared narrow-only principal contract verbatim."""
    from hex_service_kit import identity as commons

    from enterprise_kb.domain import identity

    assert identity.Principal is commons.Principal
    assert identity.RequestContext is commons.RequestContext
    assert identity.IdentityError is commons.IdentityError
    assert identity.ANONYMOUS is commons.ANONYMOUS


def test_tracer_adapters_report_the_commons_token_usage():
    """The re-export is load-bearing: the bound adapters accept the commons value type.

    A re-export that no adapter exercises proves only that an import statement parses. This
    hands each SDK-free tracer a ``TokenUsage`` built from the commons class and asserts the
    call is accepted, so the type on the wire between the LLM adapters and the tracer is the
    shared one rather than a same-shaped local twin.
    """
    from hex_service_kit.observability import TokenUsage as CommonsTokenUsage

    usage = CommonsTokenUsage(input_tokens=11, output_tokens=7, thinking_tokens=3)
    for profile in SDK_FREE_PROFILES:
        settings = _settings(profile)
        adapter = instantiate(settings.adapters["tracer"][profile], settings)
        with adapter.span("contract-test", action="parity"):
            adapter.record_token_usage(usage, "fictional-model-v1")

    local_tracer = instantiate(_settings("local").adapters["tracer"]["local"], _settings("local"))
    local_tracer.record_token_usage(usage, "fictional-model-v1")
    assert local_tracer.token_usage == [(usage, "fictional-model-v1")]
    assert type(local_tracer.token_usage[0][0]) is CommonsTokenUsage

    # The name each tracer module actually annotates with. Accepting the value at runtime is
    # not enough: an adapter typed against a local twin type-checks against the twin, so the
    # drift survives in mypy even when the values flow. These assert the modules bind the
    # shared class itself.
    from enterprise_kb.adapters.local import tracer as local_tracer_module
    from enterprise_kb.adapters.onprem import tracer as onprem_tracer_module

    assert local_tracer_module.TokenUsage is CommonsTokenUsage
    assert onprem_tracer_module.TokenUsage is CommonsTokenUsage


def test_all_protocols_are_runtime_checkable():
    for protocol in PORT_PROTOCOLS.values():
        assert issubclass(protocol, Protocol)  # type: ignore[arg-type]
        assert getattr(protocol, "_is_runtime_protocol", False), (
            f"{protocol.__name__} must be @runtime_checkable"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
