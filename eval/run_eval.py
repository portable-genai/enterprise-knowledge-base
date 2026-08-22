#!/usr/bin/env python3
"""Evaluation for the A2 Enterprise Knowledge Base : A4 / P-08.

Two named layers (``--mode``, via the shared ``agent-eval-kit`` scaffold):

* **smoke** (default) : the offline pre-merge check CI runs on every change; the build
  fails if the knowledge base falls below the model-risk thresholds agreed for a governed
  RAG store (see ``eval/rubrics/*.yaml``). A deterministic, dependency-light heuristic in
  this file: **no GCP credentials and no Google Cloud SDK**, runs the real
  ``KnowledgeBaseService.search`` pipeline against in-memory fake adapters. It is a smoke
  check, NOT the promotion authority::

      retrieval_recall  >= 0.80
      acl_correctness   >= 0.99   (never returns a document outside the principal's tags)
      citation_accuracy >= 0.98   (ANCHOR level: the right block, not just the right page)
      pii_safety        >= 0.99   (no national identifier survives into a derived surface)

* **gate** : the promotion verdict from the production evaluator (the **Gen AI evaluation
  service**, wired into the hexagon as ``EvaluationGatePort`` ->
  ``enterprise_kb.adapters.gcp.genai_eval:GenAiEvalAdapter``; requires GCP credentials).
  Fails closed on the authority's scored report, so an offline smoke result is never
  relabelled a promotion pass. ``--use-gcp`` is kept as an alias for ``--mode gate``.

``citation_accuracy`` is the **anchor-level** gate (slice 4). It no longer asks the weak
question "did the pipeline attach a page?" : a pipeline that emits a page for everything
would score 1.0 on that forever. It runs the REAL local layout parser over the fixture
documents in ``eval/datasets/layout/``, resolves each golden example's ``claim`` to a
layout block with the REAL domain resolver, and compares the resolved anchor to the
``expected_anchors`` the dataset declares. That expectation is an INDEPENDENT oracle: it
is written by hand from the fixture, never read back from the pipeline's own output, so
a resolver that drifts to the wrong block turns the gate red instead of agreeing with
itself. :func:`self_check` proves that with a wrong-anchor red case.

The headline metric is **acl_correctness**: for a governed store, returning a single
document a principal is not entitled to see is a hard failure (threshold 0.99).

``pii_safety`` is the second hard gate (E2 / C4). It runs the **real runtime redactor**
(``LocalRegexRedactionAdapter``), so the gate and the redactor read the SAME
jurisdiction pattern rows from the shared ``pii-kit``, and it scores the DERIVED
surfaces (the query that reached retrieval, the prompt written to the audit sink), never
an echo of the raw input. Scoring is the package's two-part rule: a pack scan plus a
pack-INDEPENDENT literal scan for each example's planted identifier, so narrowing or
deleting a pattern row makes the gate go RED instead of silently un-masking. The harness
itself is gated by ``agent_eval_kit.assert_each_can_go_red`` (see :func:`self_check`), so
a refactor back to a tautological metric fails the build.

Usage::

    python eval/run_eval.py                      # offline smoke check (CI, default)
    python eval/run_eval.py --dataset path.jsonl # custom golden set
    python eval/run_eval.py --mode gate          # promotion verdict (production evaluator)

Exit code is ``0`` iff ``EvalReport.passed`` (and, in gate mode, the authority's verdict).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

# The --mode smoke|gate scaffold + the aligned report rendering come from the shared
# agent-eval-kit commons; this script keeps only its own offline evaluator
# and gate runner.
from agent_eval_kit import assert_each_can_go_red, eval_main
from pii_kit import score_pii_safety as score_pii_safety_raw

# Domain models are pure-stdlib (no GCP / framework imports), so importing them here keeps
# this script runnable in the on-prem/test profile with no Google Cloud SDK installed.
from enterprise_kb.domain.anchors import anchor_citations
from enterprise_kb.domain.layout import blocks_to_chunks
from enterprise_kb.domain.models import (
    AclTag,
    Citation,
    Direction,
    DocumentChunk,
    EvalMetricResult,
    EvalReport,
    GuardrailVerdict,
    KbQuery,
    RetrievedPassage,
    TokenUsage,
)
from enterprise_kb.pii_patterns import patterns_for

# --------------------------------------------------------------------------- #
# Thresholds : the promotion bar (SPEC A4 / P-08). Mirrors eval/rubrics/*.yaml.
# --------------------------------------------------------------------------- #
THRESHOLDS: dict[str, float] = {
    "retrieval_recall": 0.80,
    "acl_correctness": 0.99,
    # Raised from 0.90 with the metric itself: page-level presence was worth 0.90, an
    # exact block match against the golden oracle is worth 0.98.
    "citation_accuracy": 0.98,
    "pii_safety": 0.99,
}

METRIC_ORDER: tuple[str, ...] = (
    "retrieval_recall",
    "acl_correctness",
    "citation_accuracy",
    "pii_safety",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_kb.jsonl"

# A synthetic corpus: document_id -> (title, uri, acl_tags). Clearly fictional.
#
# Each document is scoped to exactly the department(s) entitled to it; "restricted"
# documents add a sensitive classification tag. Admission is all-of / subset (P-09): a
# caller must hold EVERY one of a document's tags, so a document a team does not fully
# hold (e.g. it lacks the restricted classification) is correctly excluded even if it
# shares the department tag.
_CORPUS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "policy-cloud-onboarding-v3": (
        "Cloud Provider Onboarding Policy",
        "https://kb.bank.test/policy/cloud-onboarding",
        ("dept:retail",),
    ),
    "policy-aml-onboarding-v4": (
        "AML Customer Onboarding Policy",
        "https://kb.bank.test/policy/aml-onboarding",
        ("dept:compliance",),
    ),
    "runbook-incident-response-v2": (
        "Incident Response Runbook",
        "https://kb.bank.test/runbook/incident-response",
        ("dept:risk",),
    ),
    "standard-data-residency-v1": (
        "Data Residency Standard",
        "https://kb.bank.test/standard/data-residency",
        ("dept:risk", "classification:restricted"),
    ),
    "standard-access-control-v2": (
        "Access Control Standard",
        "https://kb.bank.test/standard/access-control",
        ("dept:security",),
    ),
}

# principal -> the ACL tags it resolves to in the fake directory. Each team resolves to
# its own department tag; the risk team additionally holds the restricted classification.
# ``group:risk-readonly`` holds the department tag but NOT the restricted classification,
# so under subset matching it must be denied the restricted data-residency standard : the
# discriminating case that would leak under any-of overlap.
_PRINCIPAL_TAGS: dict[str, set[str]] = {
    "user:retail@bank.test": {"dept:retail"},
    "group:risk": {"dept:risk", "classification:restricted"},
    "group:risk-readonly": {"dept:risk"},
    "group:compliance": {"dept:compliance"},
    "group:security": {"dept:security"},
    "user:contractor@vendor.test": set(),  # no access
}


# --------------------------------------------------------------------------- #
# Golden dataset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GoldenExample:
    id: str
    query: str
    acl_principals: tuple[str, ...]
    expected_doc_ids: tuple[str, ...]
    must_not_see_ids: tuple[str, ...]
    #: Synthetic identifiers planted verbatim in ``query``. They are the PACK-INDEPENDENT
    #: oracle for pii_safety: if one survives into a derived surface, the metric goes red
    #: whatever the pattern rows happen to say. Empty for a case that plants no PII.
    planted_pii: tuple[str, ...] = ()
    #: The synthetic grounded claim whose supporting block the citation gate resolves.
    claim: str = ""
    #: document_id -> the anchor a human says supports ``claim`` in the layout fixture.
    #: This is the INDEPENDENT oracle for citation_accuracy: it is authored from the
    #: fixture text, never read back from the resolver, so a resolver that drifts cannot
    #: move the target with it.
    expected_anchors: dict[str, str] = field(default_factory=dict)


def load_golden(path: Path) -> list[GoldenExample]:
    examples: list[GoldenExample] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        examples.append(
            GoldenExample(
                id=str(obj.get("id", f"example-{lineno}")),
                query=str(obj["query"]),
                acl_principals=tuple(obj.get("acl_principals", []) or ()),
                expected_doc_ids=tuple(obj.get("expected_doc_ids", []) or ()),
                must_not_see_ids=tuple(obj.get("must_not_see_ids", []) or ()),
                planted_pii=tuple(obj.get("planted_pii", []) or ()),
                claim=str(obj.get("claim", "") or ""),
                expected_anchors={
                    str(k): str(v) for k, v in (obj.get("expected_anchors") or {}).items()
                },
            )
        )
    if not examples:
        raise SystemExit(f"{path}: golden dataset is empty")
    return examples


def load_thresholds_from_rubrics() -> dict[str, float]:
    """Read thresholds from ``eval/rubrics/*.yaml`` when PyYAML is available."""
    thresholds = dict(THRESHOLDS)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return thresholds
    rubric_dir = _REPO_ROOT / "eval" / "rubrics"
    for name in ("acl_correctness.yaml", "retrieval_recall.yaml", "citation_accuracy.yaml"):
        rubric_path = rubric_dir / name
        if not rubric_path.exists():
            continue
        doc = yaml.safe_load(rubric_path.read_text(encoding="utf-8")) or {}
        metric = doc.get("metric")
        if isinstance(metric, str) and "threshold" in doc:
            thresholds[metric] = float(doc["threshold"])
        for companion, spec in (doc.get("companion_metrics") or {}).items():
            if isinstance(spec, dict) and "threshold" in spec:
                thresholds[str(companion)] = float(spec["threshold"])
    return thresholds


# --------------------------------------------------------------------------- #
# Deterministic fake adapters (inlined : the gate must not import tests.conftest).
# --------------------------------------------------------------------------- #
def _passage_for(document_id: str, score: float) -> RetrievedPassage:
    title, uri, tags = _CORPUS[document_id]
    citation = Citation(
        document_id=document_id,
        title=title,
        uri=uri,
        version="v1",
        page=1,
        snippet=f"Relevant guidance from {title}.",
        score=score,
    )
    return RetrievedPassage(
        text=f"{title}: applicable internal guidance.",
        citation=citation,
        score=score,
        acl_tags=tuple(AclTag(label=t) for t in tags),
    )


#: The jurisdictions the gate scores, read from the same setting the runtime redactor
#: uses. Read once: the row list is the contract between the redactor and this gate.
def _pii_jurisdictions() -> tuple[str, ...]:
    try:
        from enterprise_kb.config import Settings

        return tuple(Settings.load().pii.jurisdictions)
    except Exception:  # pragma: no cover - defensive: config unreadable in a bare checkout
        return ("SG",)


PII_JURISDICTIONS: tuple[str, ...] = _pii_jurisdictions()
PII_PATTERNS = patterns_for(PII_JURISDICTIONS)


#: Layout fixtures for the synthetic corpus: one plain-text file per document, form feed
#: separated pages. They are parsed by the REAL local document parser, so the gate
#: exercises the shipped layout-aware parse path rather than a bespoke fixture format.
LAYOUT_DIR = _REPO_ROOT / "eval" / "datasets" / "layout"


def load_anchor_chunks() -> dict[str, list[DocumentChunk]]:
    """Parse every layout fixture into anchored chunks, keyed by document id.

    Uses ``LocalDocumentParser`` + ``blocks_to_chunks``: the same two steps the local
    ingestion adapter runs, so a regression in layout parsing shows up here as an anchor
    the golden oracle no longer matches.
    """
    from enterprise_kb.adapters.local.document import LocalDocumentParser
    from enterprise_kb.config import Settings

    parser = LocalDocumentParser(Settings.load())
    out: dict[str, list[DocumentChunk]] = {}
    for path in sorted(LAYOUT_DIR.glob("*.txt")):
        extract = parser.parse(path.read_bytes(), "text/plain")
        out[path.stem] = blocks_to_chunks(path.stem, extract.layout)
    return out


ANCHOR_CHUNKS: dict[str, list[DocumentChunk]] = load_anchor_chunks()


def build_citation_store() -> object:
    """The REAL local ``CitationStorePort`` adapter, seeded with the parsed fixtures.

    E2 again, for anchors: a gate that resolved claims against an ad-hoc in-memory dict
    would prove nothing about the store the product reads. This is the same
    ``LocalSqliteCitationStore`` the ``local`` profile serves with, on an ephemeral
    in-memory database.
    """
    from enterprise_kb.adapters.local.citation_store import LocalSqliteCitationStore
    from enterprise_kb.config import LocalSettings, Settings
    from enterprise_kb.domain.models import Document

    settings = Settings(profile="local", local=LocalSettings(db_path=":memory:"))
    store = LocalSqliteCitationStore(settings)
    for document_id, chunks in ANCHOR_CHUNKS.items():
        store.put(
            Document(
                id=document_id,
                title=f"Synthetic evaluation document {document_id}",
                uri=f"https://{document_id}.bank.test/policy",
            ),
            chunks,
        )
    return store


CITATION_STORE = build_citation_store()


#: The match floor the runtime resolver uses, read from the same bank-owned policy (B4),
#: so the gate cannot be passed by scoring with a laxer floor than production runs.
def _anchor_floor() -> float:
    try:
        from enterprise_kb.config import Settings

        return Settings.load().policy.citation_policy().anchor_match_floor
    except Exception:  # pragma: no cover - defensive: config unreadable in a bare checkout
        from enterprise_kb.domain.policy import DEFAULT_ANCHOR_MATCH_FLOOR

        return DEFAULT_ANCHOR_MATCH_FLOOR


ANCHOR_FLOOR: float = _anchor_floor()


def build_redaction_adapter() -> object:
    """The REAL runtime redactor (PIIRedactionPort), not a fake.

    E2: a gate whose detector is not the redactor proves nothing about the redactor.
    This is the same ``LocalRegexRedactionAdapter`` the ``local`` profile serves with,
    reading the same jurisdiction rows from the shared pack.
    """
    from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
    from enterprise_kb.config import Settings

    return LocalRegexRedactionAdapter(Settings.load())


class FakeGuardrailAdapter:
    """Always-allow guardrail with deterministic verdicts (GuardrailPort)."""

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        return GuardrailVerdict(
            allowed=True, direction=direction, findings=(), sanitized_text=text, reason="benign"
        )


class FakeAccessControlAdapter:
    """Resolve principals to ACL tags from the fixed directory (AccessControlPort)."""

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        tags: set[str] = set()
        for p in principals:
            tags |= _PRINCIPAL_TAGS.get(p, set())
        return tags


class FakeTracer:
    @contextmanager
    def span(self, name: str, **attributes: str):
        yield

    def record_token_usage(self, usage: TokenUsage, model: str) -> None:
        return None


class FakeAuditSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)

    def redacted_surfaces(self, start: int = 0) -> list[str]:
        """The text this sink persisted since ``start``: a derived surface to scan."""
        out: list[str] = []
        for event in self.events[start:]:
            out.append(str(getattr(event, "redacted_prompt", "")))
            out.append(str(getattr(event, "redacted_response", "")))
        return out


class FakeLLMAdapter:
    """Unused by the search path; present so the service constructs cleanly."""

    model = "gemini-3.5-flash"

    def generate(self, request):  # type: ignore[no-untyped-def] - not called by search
        from enterprise_kb.domain.models import LlmResponse

        return LlmResponse(text="{}", model=self.model)

    def classify(self, text: str, labels: list[str]) -> str:
        return labels[0] if labels else ""


class FakeRetrievalAdapter:
    """Return passages for the example's expected + must-not-see docs (RetrievalPort).

    The adapter returns *everything plausibly matching* (expected docs plus the
    forbidden ones) WITHOUT applying ACL filtering : that is the domain's job. This makes
    acl_correctness a genuine test of whether the service drops the forbidden documents.
    """

    def __init__(self, by_query: dict[str, GoldenExample]) -> None:
        self._by_query = by_query
        #: Every query text the retrieval backend actually received. This is a DERIVED
        #: surface: with redact-before-retrieval (P-04) a planted identifier must never
        #: appear here, so pii_safety scans it.
        self.seen_queries: list[str] = []

    def retrieve(self, query: KbQuery) -> list[RetrievedPassage]:
        self.seen_queries.append(query.text)
        example = self._by_query.get(query.text)
        if example is None:
            return []
        doc_ids = list(dict.fromkeys((*example.expected_doc_ids, *example.must_not_see_ids)))
        passages: list[RetrievedPassage] = []
        for rank, did in enumerate(doc_ids):
            if did in _CORPUS:
                passages.append(_passage_for(did, score=round(0.95 - rank * 0.1, 3)))
        return passages


# --------------------------------------------------------------------------- #
# Pipeline driver
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Adapters:
    retrieval: FakeRetrievalAdapter
    access_control: FakeAccessControlAdapter
    guardrail: FakeGuardrailAdapter
    redaction: object
    llm: FakeLLMAdapter
    tracer: FakeTracer
    audit: FakeAuditSink


def _build_adapters(examples: Sequence[GoldenExample]) -> _Adapters:
    redaction = build_redaction_adapter()
    # The service redacts BEFORE retrieval (P-04), so the retrieval fake is keyed on both
    # the raw and the redacted query text: a golden case may plant PII in its query and
    # still resolve to its expected documents.
    by_query: dict[str, GoldenExample] = {}
    for ex in examples:
        by_query[ex.query] = ex
        by_query[redaction.redact(ex.query).text] = ex  # type: ignore[attr-defined]
    return _Adapters(
        retrieval=FakeRetrievalAdapter(by_query),
        access_control=FakeAccessControlAdapter(),
        guardrail=FakeGuardrailAdapter(),
        redaction=redaction,
        llm=FakeLLMAdapter(),
        tracer=FakeTracer(),
        audit=FakeAuditSink(),
    )


def _make_service(adapters: _Adapters) -> object | None:
    try:
        from enterprise_kb.domain.kb_service import (  # type: ignore[import-not-found]
            KnowledgeBaseService,
        )
    except Exception:
        return None
    try:
        return KnowledgeBaseService(
            retrieval=adapters.retrieval,
            access_control=adapters.access_control,
            guardrail=adapters.guardrail,
            redaction=adapters.redaction,
            llm=adapters.llm,
            tracer=adapters.tracer,
            audit=adapters.audit,
        )
    except Exception:
        return None


def _search(
    service: object | None, adapters: _Adapters, example: GoldenExample
) -> list[RetrievedPassage]:
    if service is not None:
        result = service.search(  # type: ignore[attr-defined]
            example.query,
            actor="eval-bot",
            acl_principals=example.acl_principals,
            tenant="fictional-eval-bank",
        )
        if isinstance(result, list):
            return result
    return []


# --------------------------------------------------------------------------- #
# Heuristic scorers
# --------------------------------------------------------------------------- #
def score_retrieval_recall(passages: list[RetrievedPassage], example: GoldenExample) -> float:
    """Fraction of expected (and permitted) documents that were returned."""
    expected = set(example.expected_doc_ids)
    if not expected:
        return 1.0
    returned = {p.citation.document_id for p in passages}
    return round(len(expected & returned) / len(expected), 4)


def score_acl_correctness(passages: list[RetrievedPassage], example: GoldenExample) -> float:
    """1.0 unless any must-not-see document leaked into the result (then 0.0)."""
    returned = {p.citation.document_id for p in passages}
    leaked = returned & set(example.must_not_see_ids)
    return 0.0 if leaked else 1.0


def score_citation_accuracy(passages: list[RetrievedPassage], example: GoldenExample) -> float:
    """ANCHOR-level citation accuracy, scored against the dataset's golden anchors.

    The pipeline's page-level citations are run through the REAL claim-to-anchor
    resolution path : the real ``CitationStorePort`` adapter holding the real layout
    parse of the fixture documents, and the real domain resolver : and the anchor that
    comes out is compared to the anchor the GOLDEN FILE declares for that document.

    Three things must hold for a passage to score:

    1. the citation points at a real corpus document and already carries a page (the old
       page-level bar, kept as a floor beneath the raised one);
    2. resolution produced an anchor AND a bounding box (an anchor with no box cannot be
       highlighted, so it does not count as anchor-level provenance); and
    3. the resolved anchor equals the dataset's ``expected_anchors`` entry.

    Point 3 is what keeps the metric non-tautological: the expectation is authored by
    hand from the fixture text and is never read back from anything the pipeline emitted,
    so a resolver that drifts to a neighbouring block turns the gate red rather than
    agreeing with itself. An example that legitimately returns nothing scores a vacuous
    1.0, as before.
    """
    if not passages:
        return 1.0  # nothing returned -> nothing miscited
    citations = [p.citation for p in passages]
    chunks_by_document = {
        c.document_id: CITATION_STORE.get(c.document_id, "")  # type: ignore[attr-defined]
        for c in citations
    }
    anchored = anchor_citations(
        example.claim or example.query, citations, chunks_by_document, ANCHOR_FLOOR
    )
    scores: list[float] = []
    for original, cited in zip(citations, anchored, strict=True):
        expected = example.expected_anchors.get(cited.document_id)
        ok = (
            cited.document_id in _CORPUS
            and original.page is not None
            and cited.anchor is not None
            and cited.bbox is not None
            and expected is not None
            and cited.anchor == expected
        )
        scores.append(1.0 if ok else 0.0)
    return round(sum(scores) / len(scores), 4)


def score_pii_safety(surfaces: Sequence[str], example: GoldenExample) -> float:
    """1.0 unless unredacted PII survived into any derived surface; 0.0 if anything leaked.

    Two independent halves, both from the shared pack (E2, systemic finding 5):

    1. a PACK scan with the very rows the runtime redactor masks with, which catches PII
       the pipeline re-introduced after the redaction boundary; and
    2. a PACK-INDEPENDENT literal scan for this example's ``planted_pii``, which is what
       catches the pack itself being wrong: narrow, mis-escape or delete a row and the
       redactor silently stops masking AND the pack scan silently stops detecting, so
       only the literal check still fails.

    ``surfaces`` are DERIVED outputs (the query that reached retrieval, the text written
    to the audit sink), never an echo of the caller's raw input.
    """
    return score_pii_safety_raw(surfaces, PII_PATTERNS, planted_tokens=example.planted_pii)


def self_check(thresholds: dict[str, float]) -> None:
    """Prove each hard metric CAN go red before trusting a green run (finding 8).

    An eval metric that re-reads the product's own verdict, or that scores a leak with a
    detector that shares the defect, cannot fail. These pairs are scored by the very
    functions the run uses, so a refactor back to a tautological metric fails here rather
    than shipping a permanently green gate.
    """
    planted = "S1234567D"  # fictional SG NRIC, synthetic
    pii_case = GoldenExample(
        id="self-check",
        query=f"who is {planted}",
        acl_principals=(),
        expected_doc_ids=(),
        must_not_see_ids=(),
        planted_pii=(planted,),
    )
    assert_each_can_go_red(
        lambda surfaces: score_pii_safety(surfaces, pii_case),
        {
            # the pack half: an unredacted identifier the ROWS can see
            "pack": (["applicant [NRIC] is cleared"], ["applicant S1111111D is cleared"]),
            # the pack-independent half: the planted literal itself
            "planted": (["applicant [NRIC] is cleared"], [f"applicant {planted} is cleared"]),
        },
        threshold=thresholds.get("pii_safety", THRESHOLDS["pii_safety"]),
        metric="pii_safety",
    )

    # citation_accuracy, proven red twice, because there are two ways this metric could
    # rot into a permanently green one.
    #
    # (a) Tautology guard. Same passage, same claim, two oracles: the TRUE block of the
    # document, and a DIFFERENT REAL block of the SAME document. If the metric scored the
    # resolver against its own answer (or merely checked "an anchor is present"), both
    # would be green. The declared anchor must be what decides.
    cite_claim = "Restricted customer records must be stored only in the in-country region."
    cite_base = GoldenExample(
        id="self-check-citation",
        query="q",
        acl_principals=(),
        expected_doc_ids=(),
        must_not_see_ids=(),
        claim=cite_claim,
    )
    cite_passages = [_passage_for("standard-data-residency-v1", 0.9)]
    cite_threshold = thresholds.get("citation_accuracy", THRESHOLDS["citation_accuracy"])
    true_oracle = replace(
        cite_base, expected_anchors={"standard-data-residency-v1": "p2#b1"}
    )  # the storage-location paragraph, which is what the claim says
    wrong_oracle = replace(
        cite_base, expected_anchors={"standard-data-residency-v1": "p1#b1"}
    )  # a real block of the same document, but not the one that supports the claim
    green = score_citation_accuracy(cite_passages, true_oracle)
    red = score_citation_accuracy(cite_passages, wrong_oracle)
    if not (green >= cite_threshold > red):
        raise SystemExit(
            "citation_accuracy is not scoring against the declared anchor: the true "
            f"oracle scored {green} and a deliberately wrong block of the same document "
            f"scored {red} (threshold {cite_threshold}). The metric cannot go red."
        )

    # (b) The standard per-metric red pair: a passage citing a document the claim is not
    # grounded in resolves to no anchor at all and must score 0.0, never a page-level pass.
    assert_each_can_go_red(
        lambda passages: score_citation_accuracy(passages, true_oracle),
        {
            "unsupported-claim": (
                cite_passages,
                [_passage_for("policy-cloud-onboarding-v3", 0.9)],
            )
        },
        threshold=cite_threshold,
        metric="citation_accuracy",
    )

    acl_case = GoldenExample(
        id="self-check-acl",
        query="q",
        acl_principals=(),
        expected_doc_ids=("policy-cloud-onboarding-v3",),
        must_not_see_ids=("standard-data-residency-v1",),
    )
    assert_each_can_go_red(
        lambda passages: score_acl_correctness(passages, acl_case),
        {
            "leak": (
                [_passage_for("policy-cloud-onboarding-v3", 0.9)],
                [_passage_for("standard-data-residency-v1", 0.9)],
            )
        },
        threshold=thresholds.get("acl_correctness", THRESHOLDS["acl_correctness"]),
        metric="acl_correctness",
    )


# --------------------------------------------------------------------------- #
# Report assembly + presentation
# --------------------------------------------------------------------------- #
@dataclass
class _PerMetric:
    scores: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0


def run_offline(dataset: Path, thresholds: dict[str, float]) -> EvalReport:
    # Fail the run before scoring if a metric can no longer go red (finding 8).
    self_check(thresholds)
    examples = load_golden(dataset)
    adapters = _build_adapters(examples)
    service = _make_service(adapters)

    agg: dict[str, _PerMetric] = {metric: _PerMetric() for metric in THRESHOLDS}
    print(
        f"Running offline eval gate over {len(examples)} golden examples "
        f"(evaluator={'KnowledgeBaseService' if service else 'unavailable'}, "
        f"pii jurisdictions={','.join(PII_JURISDICTIONS)}).\n"
    )
    for example in examples:
        seen_before = len(adapters.retrieval.seen_queries)
        audited_before = len(adapters.audit.events)
        passages = _search(service, adapters, example)
        # Derived surfaces produced by THIS example: what retrieval received and what the
        # audit sink persisted. Never the raw query.
        surfaces = [
            *adapters.retrieval.seen_queries[seen_before:],
            *adapters.audit.redacted_surfaces(audited_before),
        ]
        agg["retrieval_recall"].scores.append(score_retrieval_recall(passages, example))
        agg["acl_correctness"].scores.append(score_acl_correctness(passages, example))
        agg["citation_accuracy"].scores.append(score_citation_accuracy(passages, example))
        agg["pii_safety"].scores.append(score_pii_safety(surfaces, example))

    results = tuple(
        EvalMetricResult(
            metric=metric,
            score=round(agg[metric].mean, 4),
            threshold=thresholds.get(metric, THRESHOLDS[metric]),
            passed=round(agg[metric].mean, 4) >= thresholds.get(metric, THRESHOLDS[metric]),
        )
        for metric in METRIC_ORDER
    )
    return EvalReport(dataset=str(dataset), results=results, n_examples=len(examples))


def run_gate(dataset: Path) -> tuple[EvalReport, bool]:
    """Promotion verdict from the production evaluator (EvaluationGatePort), fail-closed.

    The verdict is the authority's scored report (GenAiEvalAdapter / Gen AI evaluation
    service); requires GCP credentials. The offline smoke check never reaches this path.
    """
    from enterprise_kb.config import Settings, build_container

    settings = Settings.load()
    if settings.profile not in ("platform", "gcp"):
        raise SystemExit(
            "--mode gate is the promotion authority and requires KB_PROFILE=platform or gcp "
            f"(got {settings.profile!r}); run --mode smoke for the offline pre-merge check."
        )
    container = build_container(settings)
    gate = container.evaluation
    report = gate.evaluate(str(dataset))
    if not isinstance(report, EvalReport):  # pragma: no cover - defensive
        raise SystemExit("EvaluationGatePort.evaluate did not return an EvalReport")
    return report, report.passed


def main(argv: list[str] | None = None) -> int:
    """Dispatch --mode via the shared eval_main scaffold (fail-closed exit codes).

    ``--use-gcp`` (the pre-split flag for the production evaluator) is kept as an alias
    for ``--mode gate``.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if "--use-gcp" in args:
        args = [a for a in args if a != "--use-gcp"] + ["--mode", "gate"]
    return eval_main(
        smoke=lambda dataset: run_offline(dataset, load_thresholds_from_rubrics()),
        gate=run_gate,
        default_dataset=DEFAULT_DATASET,
        description="Offline / GCP evaluation gate for A2 (A4 / P-08).",
        smoke_label="offline heuristic (no GCP creds)",
        gate_label="production evaluator (GenAiEvalAdapter, promotion authority)",
        argv=args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
