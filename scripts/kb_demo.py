"""Runnable demo of the A2 governed-RAG flow (synthetic, fictional data, offline).

Drives the *real* :class:`KnowledgeBaseService` over the built-in ``local`` stack
(SQLite FTS5 + deterministic LLM, SDK-free) and produces the audit-view JSON the static
renderer and the live demo server consume. It walks three personas against the same
seeded corpus so the headline control : ACL-aware, fail-closed access (P-09) : is visible
at a glance:

    step 0  query posed         the retail RM asks a question; PII redacted at the boundary
    step 1  retrieve + filter   ACL-tagged passages retrieved, then filtered IN THE DOMAIN
    step 2  grounded answer      a cited answer, never beyond the retrieved set, + confidence
    step 3  ACL contrast        the same corpus seen by an approver holding the restricted
                                classification (escalates to enhanced review) and by an
                                unknown principal, who sees nothing and is REFUSED rather
                                than given an uncited answer (B2)

Run it::

    KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo.py [out.json]

It prints a per-step summary and writes the full audit-view JSON. No cloud, no API key,
no emulator: retrieval is local FTS5, the LLM is the deterministic offline adapter, and
the ACL/redaction/audit math is reproducible by a reviewer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("KB_PROFILE", "local")

from enterprise_kb.api.deps import build_ingestion_service, build_kb_service  # noqa: E402
from enterprise_kb.config import LocalSettings, Settings, build_container  # noqa: E402
from enterprise_kb.domain.errors import RetrievalEmptyError  # noqa: E402
from enterprise_kb.domain.models import AclTag, Document, KbQuery  # noqa: E402
from enterprise_kb.domain.serialization import to_jsonable  # noqa: E402

# --------------------------------------------------------------------------- #
# Synthetic scenario : fictional bank-corpus questions and the personas asking.
# All data is invented; the principals/tags mirror the built-in seed directory so the
# offline run admits the matching seeded passages out of the box.
# --------------------------------------------------------------------------- #
ACTOR = "rm.tan@bank.test"

# (id, label, principals, query, want_answer)
PERSONAS = {
    "retail": {
        "label": "Retail RM (user:jane@bank.test) : dept:retail + classification:internal",
        "principals": ("user:jane@bank.test",),
        "query": "What due diligence is required before onboarding a cloud provider?",
    },
    "risk": {
        "label": "Risk approver (group:kb-approver) : holds dept:risk + classification:restricted",
        "principals": ("group:kb-approver",),
        "query": "Where must records classified restricted be stored?",
    },
    "unknown": {
        "label": "Unentitled principal (user:nobody@bank.test) : resolves to no ACL tags",
        "principals": ("user:nobody@bank.test",),
        "query": "What due diligence is required before onboarding a cloud provider?",
    },
}

# A document the RM ingests live, to show redact-before-index (P-04). The body carries
# obvious synthetic PII (an email and a fictional SG NRIC) that must never reach the index.
INGEST_DOC = Document(
    id="standard-vendor-offboarding-v1",
    title="Vendor Offboarding Standard (FICTIONAL)",
    uri="https://kb.bank.test/standard/vendor-offboarding",
    acl_tags=(AclTag(label="dept:retail"), AclTag(label="classification:internal")),
    version="v1",
)
INGEST_BODY = (
    "Vendor Offboarding Standard. On contract termination, revoke all vendor access "
    "within 24 hours and confirm data return or destruction. Escalations to "
    "jane.doe@bank.test or NRIC S1234567A are routed to the vendor-risk desk."
)


def _passage_view(p: object) -> dict:
    """Project a RetrievedPassage to the audit view (citation + admitting ACL tags)."""
    cit = p.citation  # type: ignore[attr-defined]
    return {
        "text": p.text,  # type: ignore[attr-defined]
        "document_id": cit.document_id,
        "title": cit.title,
        "page": cit.page,
        # Anchor-level locator: the layout block this passage is, and the box a reviewer
        # highlights. None when the corpus predates layout-aware parsing.
        "anchor": cit.anchor,
        "bbox": None if cit.bbox is None else to_jsonable(cit.bbox),
        "version": cit.version,
        "score": round(float(p.score), 3),  # type: ignore[attr-defined]
        "acl_tags": [t.label for t in p.acl_tags],  # type: ignore[attr-defined]
    }


def _run(settings: Settings) -> dict:
    """Execute the whole flow over one isolated local stack."""
    container = build_container(settings)
    kb = build_kb_service(container)
    ingestion = build_ingestion_service(container)

    # --- Ingest one document live, to surface redact-before-index ----------- #
    ingest_result = ingestion.ingest(
        INGEST_DOC, INGEST_BODY.encode("utf-8"), "text/plain", actor=ACTOR
    )
    indexed = container.retrieval.retrieve(
        KbQuery(text="vendor offboarding contract termination revoke access", top_k=10)
    )
    indexed_ingest = next(
        passage for passage in indexed if passage.citation.document_id == INGEST_DOC.id
    )

    data: dict = {
        "profile": settings.profile,
        "region": settings.region,
        "actor": ACTOR,
        "ingest": {
            "document_id": ingest_result.document_id,
            "title": INGEST_DOC.title,
            "chunks": ingest_result.chunks,
            "ok": ingest_result.ok,
            "redacted": [
                {"info_type": f.info_type, "count": f.count}
                for f in ingest_result.redaction_findings
            ],
            "acl_tags": [t.label for t in INGEST_DOC.acl_tags],
            "indexed_text": indexed_ingest.text,
        },
        "personas": [],
    }

    for key, spec in PERSONAS.items():
        principals = spec["principals"]
        query = spec["query"]
        passages = kb.search(query, actor=ACTOR, acl_principals=principals)
        # B2: an unentitled caller grounds nothing, so the service REFUSES rather than
        # returning an uncited answer. The demo shows the refusal as the governed
        # outcome it is; the ESCALATED audit record is already written by the domain.
        try:
            answer_view = to_jsonable(kb.answer(query, actor=ACTOR, acl_principals=principals))
            answer_view["refused"] = False
        except RetrievalEmptyError as exc:
            answer_view = {
                "query": query,
                "answer": f"Refused: {exc}",
                "citations": [],
                "web_citations": [],
                "confidence": 0.0,
                "requires_human_review": True,
                "review_level": "enhanced",
                "review_reasons": ["no_grounding_passages"],
                "caveats": [str(exc)],
                "refused": True,
            }
        data["personas"].append(
            {
                "key": key,
                "label": spec["label"],
                "principals": list(principals),
                "query": query,
                "passages": [_passage_view(p) for p in passages],
                "answer": answer_view,
            }
        )

    audit_events = container.audit.read_all()
    chain = container.audit.verify_chain()
    serialized_audit = json.dumps(audit_events, sort_keys=True)
    data["audit"] = {
        "entries": len(audit_events),
        "actions": [event["action"] for event in audit_events],
        "chain_ok": chain.ok,
        "chained": chain.chained,
        "raw_pii_absent": all(
            raw not in serialized_audit for raw in ("jane.doe@bank.test", "S1234567A")
        ),
    }
    return data


def run() -> dict:
    """Execute the whole demo in an isolated, deterministic local stack."""
    base = Settings.load()
    if base.profile != "local":
        raise RuntimeError("the offline demo requires KB_PROFILE=local")
    with tempfile.TemporaryDirectory(prefix="hrz2-kb-demo-") as directory:
        root = Path(directory)
        settings = replace(
            base,
            local=LocalSettings(
                db_path=str(root / "kb.db"),
                audit_path=str(root / "audit.db"),
                ledger_path=str(root / "ledger.db"),
            ),
        )
        return _run(settings)


def _print_summary(data: dict) -> None:
    ing = data["ingest"]
    masked = ", ".join(f"{r['info_type']}x{r['count']}" for r in ing["redacted"]) or "none"
    print("A2 Enterprise Knowledge Base : governed-RAG demo (local, offline)")
    print(f"  profile={data['profile']}  region={data['region']}  actor={data['actor']}")
    print(f"  ingest [{ing['document_id']}] chunks={ing['chunks']} redacted-before-index: {masked}")
    for p in data["personas"]:
        ans = p["answer"]
        if ans.get("refused"):
            review = "REFUSED (ungrounded)"
        else:
            review = f"HUMAN-REVIEW/{ans.get('review_level', 'standard')}"
        print(f"\n  -- {p['label']}")
        print(f"     query   : {p['query']}")
        print(
            f"     passages: {len(p['passages'])} admitted "
            f"({', '.join(pp['document_id'] for pp in p['passages']) or 'none : fail-closed'})"
        )
        cites = (
            ", ".join(
                f"{c['document_id']} p.{c['page']}"
                + (f" block {c['anchor']}" if c.get("anchor") else "")
                for c in ans["citations"]
            )
            or "none"
        )
        print(f"     answer  : confidence={ans['confidence']:.2f}  gate={review}  cites: {cites}")


def main(out_path: str | None) -> None:
    data = run()
    _print_summary(data)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
