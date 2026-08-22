"""The local audit store is hash-chained and tamper-evident (C9).

The store itself is the shared ``hex-service-kit`` ``HashChainedAuditLog`` (its own suite
proves the chain mechanics); these tests prove THIS repo's wiring of it: the adapter
records domain ``AuditEvent`` objects, reads them back, and surfaces tamper verification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hex_service_kit import AuditChainError

from enterprise_kb.adapters.local.audit import LocalAppendOnlyAuditAdapter
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.models import AuditEvent, Decision


def _event(action: str) -> AuditEvent:
    return AuditEvent(
        action=action,
        actor="eval-bot (FICTIONAL)",
        decision=Decision.ALLOWED,
        redacted_prompt="[REDACTED] example query",
        redacted_response="[REDACTED] example answer",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _adapter() -> LocalAppendOnlyAuditAdapter:
    settings = Settings(profile="local", local=LocalSettings(audit_path=":memory:"))
    return LocalAppendOnlyAuditAdapter(settings)


def test_events_round_trip_and_chain_verifies() -> None:
    adapter = _adapter()
    adapter.record(_event("search"))
    adapter.record(_event("answer"))
    events = adapter.read_all()
    assert [e["action"] for e in events] == ["search", "answer"]
    report = adapter.verify_chain()
    assert report.ok and report.chained == 2


def test_tampering_is_detected() -> None:
    adapter = _adapter()
    adapter.record(_event("search"))
    adapter.record(_event("answer"))
    # Reach under the adapter and edit a stored row in place (the tamper class the chain
    # must catch). The append-only triggers block UPDATE, so drop them first, exactly as
    # an attacker with file access could.
    conn = adapter._log._conn  # noqa: SLF001 - deliberate tamper simulation
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    conn.execute(
        "UPDATE audit_log SET event_json = replace(event_json, 'search', 'x') WHERE seq = 1"
    )
    conn.commit()
    assert not adapter.verify_chain().ok


def test_export_import_rejects_transit_tamper(tmp_path: Path) -> None:
    source = LocalAppendOnlyAuditAdapter(
        Settings(
            profile="local",
            local=LocalSettings(audit_path=str(tmp_path / "source.db")),
        )
    )
    source.record(_event("search"))
    export = tmp_path / "audit.jsonl"
    source.export_jsonl(export)
    export.write_text(
        export.read_text(encoding="utf-8").replace('"search"', '"altered"', 1),
        encoding="utf-8",
    )
    target = LocalAppendOnlyAuditAdapter(
        Settings(
            profile="local",
            local=LocalSettings(audit_path=str(tmp_path / "target.db")),
        )
    )

    with pytest.raises(AuditChainError, match="entry_hash mismatch"):
        target.import_jsonl(export)
    assert target.read_all() == []
