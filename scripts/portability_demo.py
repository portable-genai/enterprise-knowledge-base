#!/usr/bin/env python3
"""Bounded executable portability proof for Hrz2.

This offline proof checks selector and port-map behavior, deterministic local execution,
SDK-free managed construction, identity replacement, fail-fast on-prem behavior, and a
hash-verified open-format audit export/reload. It does not prove a live managed service,
completed on-prem adapters, data migration from managed search, or Hrz5 delivery.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import kb_demo
from hex_service_kit import EXPORT_FORMAT, AuditChainError

from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.config import Container, LocalSettings, PiiSettings, Settings
from enterprise_kb.domain.identity import RequestContext
from enterprise_kb.domain.models import AuditEvent, Decision, KbQuery

EXPECTED_PORTS = {
    "access_control",
    "agent_runtime",
    "audit",
    "citation_store",
    "evaluation",
    "grounding",
    "guardrail",
    "identity",
    "ingestion",
    "ledger",
    "llm",
    "memory",
    "redaction",
    "registry",
    "retrieval",
    "session",
    "tool_catalog",
    "tracer",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"portability evidence mismatch: {message}")


def _settings(base: Settings, profile: str, root: Path) -> Settings:
    return replace(
        base,
        profile=profile,
        local=LocalSettings(
            db_path=str(root / f"{profile}-kb.db"),
            audit_path=str(root / f"{profile}-audit.db"),
            ledger_path=str(root / f"{profile}-ledger.db"),
        ),
    )


def _event(action: str) -> AuditEvent:
    return AuditEvent(
        action=action,
        actor="portable-reviewer@bank.test",
        decision=Decision.ALLOWED,
        redacted_prompt="[REDACTED] synthetic query",
        redacted_response="Synthetic cited answer",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def main() -> int:
    print("Hrz2 bounded portability proof")
    base = Settings.load()
    _require(base.profile == "local", "proof must run with KB_PROFILE=local")
    _require(set(base.adapters) == EXPECTED_PORTS, "port set")
    profiles = {"gcp", "platform", "local", "onprem"}
    _require(
        all(set(binding) == profiles for binding in base.adapters.values()),
        "every port must bind every declared profile exactly",
    )
    print("PASS port map: every port explicitly binds every declared profile")

    with tempfile.TemporaryDirectory(prefix="hrz2-portability-") as directory:
        root = Path(directory)
        first = kb_demo.run()
        second = kb_demo.run()
        _require(first == second, "isolated local rerun")
        print("PASS local replay: two isolated governed-RAG runs produce identical evidence")

        managed = Container(_settings(base, "gcp", root))
        _ = managed.retrieval
        # Says only what this step establishes. "Without eager SDK calls" is a claim about an
        # interpreter where the SDK cannot be imported, and this process is not one: with the
        # SDK installed, an eagerly imported adapter constructs here and prints PASS the same.
        print(
            "PASS managed seam: the GCP retrieval adapter constructs offline (that it does "
            "so with the SDK BLOCKED is proved by tests/contract/test_sdk_free_build.py)"
        )

        local_identity = Container(_settings(base, "local", root)).identity
        analyst = local_identity.resolve(RequestContext(headers={"x-dev-persona": "analyst"}))
        approver = local_identity.resolve(RequestContext(headers={"x-dev-persona": "approver"}))
        _require(analyst.subject != approver.subject, "identity subject swap")
        _require(
            set(analyst.principals) < set(approver.principals),
            "identity entitlement swap",
        )
        access = Container(_settings(base, "local", root)).access_control
        _require(
            access.resolve(analyst.principals, analyst.tenant)
            != access.resolve(approver.principals, approver.tenant),
            "identity effective ACL swap",
        )
        _require(
            approver.entitlement_principals(("group:kb-approver", "group:foreign-admin"))
            == ("group:kb-approver",),
            "foreign entitlement narrowing",
        )
        print("PASS identity seam: one header swaps server-owned persona and entitlements")

        source = Container(_settings(base, "local", root)).audit
        source.record(_event("search"))
        source.record(_event("answer"))
        reopened = Container(_settings(base, "local", root)).audit
        _require(reopened.read_all() == source.read_all(), "audit reopen")
        _require(reopened.verify_chain().ok, "reopened audit chain")
        export = root / "audit.jsonl"
        _require(source.export_jsonl(export) == 2, "audit export count")

        tampered_export = root / "audit-tampered.jsonl"
        tampered_export.write_text(
            export.read_text(encoding="utf-8").replace('"search"', '"altered"', 1),
            encoding="utf-8",
        )
        tampered_settings = replace(
            _settings(base, "local", root),
            local=LocalSettings(
                db_path=str(root / "tampered-kb.db"),
                audit_path=str(root / "tampered-audit.db"),
                ledger_path=str(root / "tampered-ledger.db"),
            ),
        )
        try:
            Container(tampered_settings).audit.import_jsonl(tampered_export)
        except AuditChainError:
            print("PASS audit transit: altered JSONL is rejected before restore")
        else:
            raise RuntimeError("tampered audit export was accepted")

        target_settings = replace(
            _settings(base, "local", root),
            local=LocalSettings(
                db_path=str(root / "reload-kb.db"),
                audit_path=str(root / "reload-audit.db"),
                ledger_path=str(root / "reload-ledger.db"),
            ),
        )
        target = Container(target_settings).audit
        _require(target.import_jsonl(export) == 2, "audit import count")
        _require(target.read_all() == source.read_all(), "audit record round-trip")
        report = target.verify_chain()
        _require(report.ok and report.chained == 2, "reloaded audit chain")
        # The export is an anchor header followed by the record lines: line 1 commits to the
        # chain head so a recipient can refuse a trail whose newest records were dropped in
        # transit (a shorter chain still links perfectly, so only the anchor exposes it).
        header, *records = export.read_text(encoding="utf-8").splitlines()
        _require(json.loads(header).get("format") == EXPORT_FORMAT, "export anchor header")
        _require(len(records) == 2, "export record count")
        for line in records:
            _require(
                {"seq", "prev_hash", "entry_hash", "event"} <= json.loads(line).keys(), "format"
            )
        _require(
            json.loads(header)["anchor"]["entry_hash"] == json.loads(records[-1])["entry_hash"],
            "export anchor commits to the head",
        )
        print("PASS audit seam: JSONL export reloads byte-equivalent records with chain proof")

        connection = target._log._conn  # noqa: SLF001 - deliberate tamper proof
        connection.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        connection.execute(
            "UPDATE audit_log SET event_json = replace(event_json, 'search', 'altered') "
            "WHERE seq = 1"
        )
        connection.commit()
        _require(not target.verify_chain().ok, "audit tamper detection")
        print("PASS audit integrity: an altered interior record breaks verification")

        onprem = Container(_settings(base, "onprem", root))
        try:
            onprem.retrieval.retrieve(KbQuery(text="synthetic portability query"))
        except NotImplementedError:
            print("PASS exit boundary: unfinished on-prem retrieval fails closed")
        else:
            raise RuntimeError("on-prem retrieval did not fail fast")
        try:
            onprem.identity.resolve(RequestContext(headers={}))
        except NotImplementedError:
            print("PASS identity exit: unfinished on-prem identity fails closed")
        else:
            raise RuntimeError("on-prem identity did not fail fast")

        try:
            _ = Container(_settings(base, "misspelled", root)).retrieval
        except KeyError:
            print("PASS selector: an unknown profile cannot fall through to managed services")
        else:
            raise RuntimeError("unknown profile did not fail closed")

        # PII pack portability: switching market is a settings change, and the pack
        # actually changes what is masked (an SG-only pack must NOT cover JP).
        sg_only = LocalRegexRedactionAdapter(replace(base, pii=PiiSettings(jurisdictions=("SG",))))
        sg_and_jp = LocalRegexRedactionAdapter(
            replace(base, pii=PiiSettings(jurisdictions=("SG", "JP")))
        )
        probe = "applicant S1234567D, my number 1234 5678 9018"
        if "1234 5678 9018" not in sg_only.redact(probe).text:
            raise RuntimeError("the SG-only pack silently covered a JP identifier")
        if "1234 5678 9018" in sg_and_jp.redact(probe).text:
            raise RuntimeError("adding JP did not change what is masked")
        if "S1234567D" in sg_only.redact(probe).text:
            raise RuntimeError("the SG pack did not mask the home identifier")
        print("PASS pii pack: one setting swaps the jurisdiction rows the redactor masks with")

    print(
        "LIMITS not proved here: live managed services/IAP, completed on-prem, managed WORM "
        "or corpus migration, cross-tenant/policy/regulator/UI portability, live DLP "
        "custom-info-type behavior, backup reconciliation, or Hrz5 audit delivery."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
