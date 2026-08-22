"""Claim-to-anchor resolution : which block does this claim actually come from?

The consequential decision here is *where the evidence is*, and it is PURE CODE. The
model never returns an anchor, a page or a box: it returns prose and the document ids it
used, and this module decides, deterministically and reproducibly, which layout block of
those documents each claim is grounded in. Same claim, same blocks, same anchor, every
run : which is what makes an anchor auditable rather than a plausible-looking string.

The rule, in one line: score every (claim sentence, block) pair by how much of the
sentence's content vocabulary the block contains, take the best pair above the
configured floor, and refine that citation's locator to the block's page, anchor and
bounding box.

Two properties matter as much as the matching itself:

* **A citation without an anchor stays valid.** Below the floor (or with no stored
  chunks at all, e.g. a corpus ingested before layout parsing, or a tenant that may not
  read the document's anchors) the citation is returned UNCHANGED, still carrying its
  document, version and page. Degrading to page-level provenance is the designed
  outcome; raising is not.
* **Nothing is invented.** The anchor and box are copied from a stored chunk of the very
  document the citation already names, so resolution can only ever narrow a locator, it
  can never point somewhere the retrieval did not.

Pure domain code: standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from .models import Citation, DocumentChunk

__all__ = [
    "AnchorMatch",
    "anchor_citation",
    "anchor_citations",
    "resolve_anchor",
    "split_claims",
]

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

#: Content-free words carry no evidence, so they are excluded from both sides of the
#: overlap. Without this, two unrelated sentences share "the of a to" and every block
#: looks like a partial match.
_STOPWORDS: frozenset[str] = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "must",
        "not",
        "of",
        "on",
        "or",
        "should",
        "shall",
        "that",
        "the",
        "their",
        "there",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "within",
        "would",
        "you",
        "your",
    ]
)


def _tokens(text: str) -> set[str]:
    """Content tokens of ``text``: lowercased alphanumerics minus stopwords."""
    return {t for t in (w.lower() for w in _WORD_RE.findall(text or "")) if t not in _STOPWORDS}


def split_claims(text: str) -> list[str]:
    """Split an answer into claim sentences (deterministic, punctuation-based)."""
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text or "")]
    return [part for part in parts if part]


@dataclass(frozen=True, slots=True)
class AnchorMatch:
    """The block a claim resolved to, and how much of the claim it covers."""

    chunk: DocumentChunk
    score: float
    claim: str = ""

    @property
    def anchor(self) -> str | None:
        return self.chunk.anchor

    @property
    def page(self) -> int | None:
        return self.chunk.page


def resolve_anchor(
    claim: str,
    chunks: Sequence[DocumentChunk],
    floor: float,
) -> AnchorMatch | None:
    """Return the best-matching chunk for ``claim``, or ``None`` below ``floor``.

    Score is the fraction of the claim's content tokens the chunk contains, so a block
    that says everything the claim says scores 1.0 and a block that shares one incidental
    word scores near 0. Ties are broken deterministically: more absolute overlap first,
    then the shorter (more specific) block, then the earlier page and ordinal, so the
    same corpus always yields the same anchor.
    """
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return None
    best: AnchorMatch | None = None
    best_key: tuple[float, int, int, int, int] | None = None
    for chunk in chunks:
        chunk_tokens = _tokens(chunk.text)
        if not chunk_tokens:
            continue
        overlap = len(claim_tokens & chunk_tokens)
        if not overlap:
            continue
        score = overlap / len(claim_tokens)
        key = (
            -score,
            -overlap,
            len(chunk_tokens),
            chunk.page if chunk.page is not None else 0,
            chunk.ordinal,
        )
        if best_key is None or key < best_key:
            best_key = key
            best = AnchorMatch(chunk=chunk, score=round(score, 4), claim=claim)
    if best is None or best.score < floor:
        return None
    return best


def anchor_citation(
    claim: str,
    citation: Citation,
    chunks: Sequence[DocumentChunk],
    floor: float,
) -> Citation:
    """Refine one citation's locator to the block ``claim`` resolves to.

    Only chunks belonging to the citation's own document are considered. Returns the
    citation unchanged when nothing clears the floor.
    """
    own = [c for c in chunks if c.document_id == citation.document_id and c.anchor]
    match = resolve_anchor(claim, own, floor)
    if match is None:
        return citation
    return replace(
        citation,
        page=match.chunk.page if match.chunk.page is not None else citation.page,
        anchor=match.chunk.anchor,
        bbox=match.chunk.bbox,
        snippet=match.chunk.text[:280] or citation.snippet,
    )


def anchor_citations(
    answer_text: str,
    citations: Sequence[Citation],
    chunks_by_document: Mapping[str, Sequence[DocumentChunk]],
    floor: float,
) -> tuple[Citation, ...]:
    """Anchor every citation of an answer against the stored chunks of its document.

    Each citation is resolved against the answer's claim sentences: the best (sentence,
    block) pair for that document wins, so a multi-claim answer anchors each source to
    the sentence it actually supports rather than to the whole answer's vocabulary.
    Citations whose document has no stored chunks, or whose best pair is below ``floor``,
    are passed through unchanged at page level.
    """
    claims = split_claims(answer_text) or [answer_text]
    out: list[Citation] = []
    for citation in citations:
        chunks = list(chunks_by_document.get(citation.document_id, ()))
        best: AnchorMatch | None = None
        for claim in claims:
            match = resolve_anchor(
                claim,
                [c for c in chunks if c.document_id == citation.document_id and c.anchor],
                floor,
            )
            if match is None:
                continue
            better = (match.score, -match.chunk.ordinal)
            if best is None or better > (best.score, -best.chunk.ordinal):
                best = match
        if best is None:
            out.append(citation)
            continue
        out.append(
            replace(
                citation,
                page=best.chunk.page if best.chunk.page is not None else citation.page,
                anchor=best.chunk.anchor,
                bbox=best.chunk.bbox,
                snippet=best.chunk.text[:280] or citation.snippet,
            )
        )
    return tuple(out)
