"""Claim-to-anchor resolution (slice 4): pure, deterministic, and safely degrading.

The three properties that make an anchor auditable rather than decorative:

* it is PURE CODE : same claim and same blocks give the same anchor, and no model is
  consulted to produce it;
* it never invents : an anchor can only come from a stored block of the document the
  citation already names; and
* it degrades : below the bank-owned match floor the citation is returned unchanged at
  page level, so a claim the corpus does not support gets weaker provenance, not a
  confident wrong pointer.
"""

from __future__ import annotations

from enterprise_kb.domain.anchors import (
    anchor_citation,
    anchor_citations,
    resolve_anchor,
    split_claims,
)
from enterprise_kb.domain.kernel import BoundingBox
from enterprise_kb.domain.models import Citation, DocumentChunk

FLOOR = 0.34

CHUNKS = [
    DocumentChunk(
        document_id="policy",
        ordinal=0,
        text="Cloud Provider Onboarding Policy",
        page=1,
        anchor="p1#b0",
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=0.3, y1=0.02),
    ),
    DocumentChunk(
        document_id="policy",
        ordinal=1,
        text="The Retail Technology function owns this policy and reviews it annually.",
        page=1,
        anchor="p1#b1",
        bbox=BoundingBox(x0=0.0, y0=0.05, x1=0.6, y1=0.08),
    ),
    DocumentChunk(
        document_id="policy",
        ordinal=2,
        text=(
            "Before a cloud provider is onboarded the bank completes a security review "
            "and a data residency assessment."
        ),
        page=2,
        anchor="p2#b1",
        bbox=BoundingBox(x0=0.0, y0=0.1, x1=0.7, y1=0.15),
    ),
]

CITATION = Citation(document_id="policy", title="Policy", uri="u", version="v3", page=1)


def test_resolves_the_supporting_block() -> None:
    match = resolve_anchor(
        "Before onboarding a cloud provider the bank completes a security review.",
        CHUNKS,
        FLOOR,
    )
    assert match is not None
    assert match.anchor == "p2#b1"
    assert match.page == 2


def test_resolution_is_deterministic() -> None:
    claim = "The Retail Technology function owns this policy."
    first = resolve_anchor(claim, CHUNKS, FLOOR)
    second = resolve_anchor(claim, list(reversed(CHUNKS)), FLOOR)
    assert first is not None and second is not None
    assert first.anchor == second.anchor == "p1#b1"


def test_below_the_floor_resolves_to_nothing() -> None:
    assert resolve_anchor("quarterly dividend distribution schedule", CHUNKS, FLOOR) is None


def test_empty_claim_resolves_to_nothing() -> None:
    assert resolve_anchor("   ", CHUNKS, FLOOR) is None
    assert resolve_anchor("the and of a to", CHUNKS, FLOOR) is None, "stopwords are not evidence"


def test_raising_the_floor_withholds_a_weak_anchor() -> None:
    """The floor is a bank-owned dial (B4): raise it and a partial match is withheld."""
    claim = "The bank completes a security review of every regional subsidiary."
    assert resolve_anchor(claim, CHUNKS, 0.34) is not None
    assert resolve_anchor(claim, CHUNKS, 0.95) is None


def test_anchor_citation_refines_page_anchor_and_box() -> None:
    anchored = anchor_citation(
        "Before onboarding a cloud provider the bank completes a security review.",
        CITATION,
        CHUNKS,
        FLOOR,
    )
    assert anchored.anchor == "p2#b1"
    assert anchored.page == 2
    assert anchored.bbox == CHUNKS[2].bbox
    assert anchored.document_id == CITATION.document_id
    assert anchored.version == CITATION.version, "resolution refines the locator only"


def test_unresolvable_claim_returns_the_citation_unchanged() -> None:
    unchanged = anchor_citation("unrelated subject matter entirely", CITATION, CHUNKS, FLOOR)
    assert unchanged == CITATION
    assert unchanged.anchor is None, "page-level provenance stays valid, it is not an error"


def test_chunks_of_another_document_are_never_used() -> None:
    other = [
        DocumentChunk(
            document_id="other-doc",
            ordinal=0,
            text="Before a cloud provider is onboarded the bank completes a security review.",
            page=9,
            anchor="p9#b0",
            bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        )
    ]
    result = anchor_citation("the bank completes a security review", CITATION, other, FLOOR)
    assert result == CITATION, "an anchor may only come from the cited document"


def test_chunks_without_an_anchor_are_skipped() -> None:
    legacy = [
        DocumentChunk(
            document_id="policy",
            ordinal=0,
            text="Before a cloud provider is onboarded the bank completes a security review.",
            page=2,
        )
    ]
    assert anchor_citation("the bank completes a security review", CITATION, legacy, FLOOR) == (
        CITATION
    )


def test_multi_claim_answer_uses_the_strongest_sentence_block_pair() -> None:
    """Across an answer's sentences, the best (sentence, block) pair wins per citation."""
    answer = (
        "The Retail Technology function owns this policy and reviews it annually. "
        "Cloud providers are also assessed."
    )
    other_citation = Citation(document_id="runbook", title="Runbook", uri="u", page=1)
    chunks_by_document = {"policy": CHUNKS, "runbook": []}
    anchored = anchor_citations(answer, [CITATION, other_citation], chunks_by_document, FLOOR)
    assert anchored[0].anchor == "p1#b1"
    assert anchored[0].page == 1
    assert anchored[1] == other_citation, "a document with no stored chunks stays page-level"


def test_answer_anchors_to_the_block_its_strongest_claim_supports() -> None:
    answer = "Before onboarding a cloud provider the bank completes a data residency assessment."
    anchored = anchor_citations(answer, [CITATION], {"policy": CHUNKS}, FLOOR)
    assert anchored[0].anchor == "p2#b1"
    assert anchored[0].page == 2


def test_split_claims_splits_on_sentence_punctuation() -> None:
    assert split_claims("One thing. Two things! Three?") == [
        "One thing.",
        "Two things!",
        "Three?",
    ]
    assert split_claims("") == []
