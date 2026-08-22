"""Built-in synthetic, ACL-tagged corpus for the ``local`` profile.

A tiny, clearly-fictional set of bank-corpus passages (policies, runbooks, standards)
with page-level citations and ACL tags, so the local retrieval adapter has something to
ground answers on out of the box and the end-to-end CLI smoke run returns a real cited,
ACL-filtered artifact with no external corpus. The text is invented; the document ids /
titles / names are plausible but fictional and must not be treated as real instruments
or people.

This mirrors ``tests/fixtures/sample_docs`` so the local adapters and the unit-test
fixtures share one deterministic corpus, but it lives under ``src`` (not ``tests``) so
the shipped package can seed itself without importing the test tree. The local
access-control directory (:data:`PRINCIPAL_TAGS`) resolves the same principals to the
same tags, so a CLI run with ``--principals user:jane@bank.test`` admits the matching
passages.
"""

from __future__ import annotations

from ...domain.kernel import BlockKind, BoundingBox
from ...domain.layout import anchor_id
from ...domain.models import (
    AclTag,
    Citation,
    Document,
    DocumentChunk,
    RetrievedPassage,
    SourceSystem,
)

# --------------------------------------------------------------------------- #
# ACL tags used across the seed corpus (mirrors tests/fixtures/sample_docs).
# Admission is all-of / subset: a caller must hold EVERY one of a passage's tags.
# --------------------------------------------------------------------------- #
TAG_RETAIL = AclTag(label="dept:retail")
TAG_RISK = AclTag(label="dept:risk")
TAG_INTERNAL = AclTag(label="classification:internal")
TAG_RESTRICTED = AclTag(label="classification:restricted")  # sensitive -> forces review

# Tenant partitions. Most corpus content belongs to the primary demo tenant; a passage with
# no tenant ("") is shared/global and visible to every tenant.
TENANT_DEMO = "demo-bank"

# --------------------------------------------------------------------------- #
# Documents.
# --------------------------------------------------------------------------- #
SEED_DOCUMENTS: tuple[Document, ...] = (
    Document(
        id="policy-cloud-onboarding-v3",
        title="Cloud Provider Onboarding Policy",
        uri="https://kb.bank.test/policy/cloud-onboarding",
        source_system=SourceSystem.POLICY_PORTAL,
        acl_tags=(TAG_RETAIL, TAG_INTERNAL),
        tenant=TENANT_DEMO,
        version="v3",
    ),
    Document(
        id="runbook-incident-response-v2",
        title="Incident Response Runbook",
        uri="https://kb.bank.test/runbook/incident-response",
        source_system=SourceSystem.CONFLUENCE,
        acl_tags=(TAG_RISK, TAG_INTERNAL),
        tenant=TENANT_DEMO,
        version="v2",
    ),
    Document(
        id="standard-data-residency-v1",
        title="Data Residency Standard",
        uri="https://kb.bank.test/standard/data-residency",
        source_system=SourceSystem.SHAREPOINT,
        acl_tags=(TAG_RISK, TAG_RESTRICTED),
        tenant=TENANT_DEMO,
        version="v1",
    ),
    Document(
        id="notice-code-of-conduct-v1",
        title="Group Code of Conduct",
        uri="https://kb.bank.test/notice/code-of-conduct",
        source_system=SourceSystem.POLICY_PORTAL,
        acl_tags=(TAG_INTERNAL,),
        tenant="",  # shared/global notice: every tenant may read it
        version="v1",
    ),
)

DOCS_BY_ID: dict[str, Document] = {d.id: d for d in SEED_DOCUMENTS}


def _citation(
    document: Document, page: int, snippet: str, score: float, block: int = 1
) -> Citation:
    """A seed citation, anchored to a synthetic layout block of its page.

    The seed corpus is canned rather than parsed, so its anchors are declared here: block
    ``block`` of the given page, with a box in the upper half of the page. That keeps the
    out-of-the-box offline corpus anchor-capable, so a demo shows the block-level locator
    without an ingest step.
    """
    return Citation(
        document_id=document.id,
        title=document.title,
        uri=document.uri,
        version=document.version,
        page=page,
        snippet=snippet,
        score=score,
        anchor=anchor_id(page, block),
        bbox=BoundingBox(x0=0.08, y0=0.18 + 0.06 * block, x1=0.92, y1=0.26 + 0.06 * block),
    )


# --------------------------------------------------------------------------- #
# Canned passages : each carries a page number and its ACL tags.
# --------------------------------------------------------------------------- #
SEED_PASSAGES: tuple[RetrievedPassage, ...] = (
    RetrievedPassage(
        text=(
            "Before onboarding a cloud provider, conduct provider due diligence covering "
            "data residency, exit strategy and concentration risk, and retain audit rights."
        ),
        citation=_citation(
            DOCS_BY_ID["policy-cloud-onboarding-v3"],
            page=4,
            snippet="conduct provider due diligence",
            score=0.93,
        ),
        score=0.93,
        acl_tags=(TAG_RETAIL, TAG_INTERNAL),
        tenant=TENANT_DEMO,
    ),
    RetrievedPassage(
        text=(
            "On a major incident, page the on-call lead, open a bridge, and notify the "
            "risk function within thirty minutes of declaration."
        ),
        citation=_citation(
            DOCS_BY_ID["runbook-incident-response-v2"],
            page=2,
            snippet="notify the risk function within thirty minutes",
            score=0.88,
        ),
        score=0.88,
        acl_tags=(TAG_RISK, TAG_INTERNAL),
        tenant=TENANT_DEMO,
    ),
    RetrievedPassage(
        text=(
            "All customer records classified restricted must remain resident in the "
            "primary in-country region and may not be replicated cross-border."
        ),
        citation=_citation(
            DOCS_BY_ID["standard-data-residency-v1"],
            page=1,
            snippet="must remain resident in the primary in-country region",
            score=0.82,
        ),
        score=0.82,
        acl_tags=(TAG_RISK, TAG_RESTRICTED),
        tenant=TENANT_DEMO,
    ),
    RetrievedPassage(
        text=(
            "All staff must complete the annual code-of-conduct attestation and report "
            "suspected misconduct through the confidential whistleblowing channel."
        ),
        citation=_citation(
            DOCS_BY_ID["notice-code-of-conduct-v1"],
            page=1,
            snippet="complete the annual code-of-conduct attestation",
            score=0.70,
        ),
        score=0.70,
        acl_tags=(TAG_INTERNAL,),
        tenant="",  # shared/global: visible across tenants
    ),
)


def _seed_chunks() -> tuple[DocumentChunk, ...]:
    """Project the seed passages into anchored chunks for the local citation store.

    One chunk per seed passage, ordinal-ordered within its document, so claim-to-anchor
    resolution has something to resolve against out of the box : the same relationship
    the ingest path creates between indexed passages and stored anchors.
    """
    per_document: dict[str, int] = {}
    chunks: list[DocumentChunk] = []
    for passage in SEED_PASSAGES:
        citation = passage.citation
        ordinal = per_document.get(citation.document_id, 0)
        per_document[citation.document_id] = ordinal + 1
        chunks.append(
            DocumentChunk(
                document_id=citation.document_id,
                ordinal=ordinal,
                text=passage.text,
                page=citation.page,
                anchor=citation.anchor,
                bbox=citation.bbox,
                kind=BlockKind.PARAGRAPH,
            )
        )
    return tuple(chunks)


#: Anchored chunks the local citation store self-seeds with, keyed by (document, tenant).
SEED_CHUNKS: tuple[DocumentChunk, ...] = _seed_chunks()

#: The tenant each seed passage belongs to, so the store seeds with the same partition
#: the retrieval index uses and a cross-tenant read of the seed corpus still returns
#: nothing.
SEED_CHUNK_TENANTS: dict[str, str] = {p.citation.document_id: p.tenant for p in SEED_PASSAGES}


# --------------------------------------------------------------------------- #
# Local access-control directory : principal id -> the ACL tag labels it may see.
# A principal that is not present resolves to no tags (access-denied, fail-closed).
# --------------------------------------------------------------------------- #
RETAIL_PRINCIPAL = "user:jane@bank.test"
RISK_PRINCIPAL = "group:risk"
SERVICE_PRINCIPAL = "svc:kyc-agent"

# Entitlement groups the seeded dev personas (adapters/local/identity.py) carry, so a
# persona's principals resolve to real ACL tags and switching persona in the picker
# changes which corpus passages the same query admits (per-user access, demoable offline).
# Because admission is all-of/subset, a group grants a tag ONLY if it lists that tag: e.g.
# the risk group alone cannot open a {dept:risk, classification:restricted} passage; that
# needs the approver group, which also holds the restricted classification.
KB_READER_GROUP = "group:kb-reader"  # baseline read: retail + internal
KB_APPROVER_GROUP = "group:kb-approver"  # privileged: adds risk + restricted visibility
AUDIT_GROUP = "group:audit"  # auditor: internal-classification only

PRINCIPAL_TAGS: dict[str, set[str]] = {
    RETAIL_PRINCIPAL: {TAG_RETAIL.label, TAG_INTERNAL.label},
    RISK_PRINCIPAL: {TAG_RISK.label, TAG_INTERNAL.label},
    SERVICE_PRINCIPAL: {TAG_INTERNAL.label},
    KB_READER_GROUP: {TAG_RETAIL.label, TAG_INTERNAL.label},
    KB_APPROVER_GROUP: {
        TAG_RETAIL.label,
        TAG_RISK.label,
        TAG_INTERNAL.label,
        TAG_RESTRICTED.label,
    },
    AUDIT_GROUP: {TAG_INTERNAL.label},
}
