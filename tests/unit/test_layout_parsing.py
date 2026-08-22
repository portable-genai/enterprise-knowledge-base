"""Layout-aware parsing (slice 1) and the anchor model (slice 2).

Two properties are load-bearing and are asserted here rather than assumed:

* the offline parser produces the SAME shape the managed portable-parser adapter produces
  (pages -> classified, boxed blocks -> anchored chunks), deterministically; and
* the anchor fields are ADDITIVE : a citation or chunk without an anchor is still a
  valid, usable value, because a corpus ingested before this work exists in the wild.
"""

from __future__ import annotations

from enterprise_kb.adapters.local.document import LocalDocumentParser
from enterprise_kb.config import LocalSettings, Settings
from enterprise_kb.domain.kernel import BlockKind, BoundingBox
from enterprise_kb.domain.layout import (
    ParsedDocument,
    analyze_text_pages,
    anchor_id,
    blocks_to_chunks,
    parse_anchor,
)
from enterprise_kb.domain.models import Citation, DocumentChunk
from enterprise_kb.domain.serialization import to_jsonable

_FIXTURE = (
    "Cloud Onboarding Policy\n"
    "\n"
    "Providers are reviewed before onboarding, and the outcome is recorded.\n"
    "\n"
    "- security review\n"
    "- resilience assessment\n"
    "\f"
    "Provider tier   Review depth\n"
    "Tier one        full\n"
)


def _settings() -> Settings:
    return Settings(profile="local", local=LocalSettings(db_path=":memory:"))


def test_local_parser_emits_pages_blocks_kinds_and_boxes() -> None:
    extract = LocalDocumentParser(_settings()).parse(_FIXTURE.encode("utf-8"), "text/plain")
    layout = extract.layout
    assert isinstance(layout, ParsedDocument)
    assert len(layout.pages) == 2, "the form feed must start a new page"

    kinds = [b.kind for b in layout.pages[0].blocks]
    assert kinds == [BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.LIST]
    assert [b.kind for b in layout.pages[1].blocks] == [BlockKind.TABLE]

    for block in layout.blocks:
        assert isinstance(block.bbox, BoundingBox)
        assert 0.0 <= block.bbox.x0 <= block.bbox.x1 <= 1.0
        assert 0.0 <= block.bbox.y0 <= block.bbox.y1 <= 1.0


def test_flat_page_view_agrees_with_the_layout_page_count() -> None:
    """The legacy ``pages`` view is derived from the layout, so the two cannot disagree."""
    extract = LocalDocumentParser(_settings()).parse(_FIXTURE.encode("utf-8"), "text/plain")
    assert len(extract.pages) == len(extract.layout.pages)


def test_parsing_is_deterministic() -> None:
    parser = LocalDocumentParser(_settings())
    first = blocks_to_chunks("doc", parser.parse(_FIXTURE.encode("utf-8"), "text/plain").layout)
    second = blocks_to_chunks("doc", parser.parse(_FIXTURE.encode("utf-8"), "text/plain").layout)
    assert first == second


def test_chunks_carry_stable_ordinals_and_anchors() -> None:
    layout = analyze_text_pages([_FIXTURE])
    chunks = blocks_to_chunks("doc", layout)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert [c.anchor for c in chunks] == ["p1#b0", "p1#b1", "p1#b2", "p2#b0"]
    assert [c.page for c in chunks] == [1, 1, 1, 2]
    assert all(c.document_id == "doc" for c in chunks)


def test_blank_blocks_are_dropped() -> None:
    chunks = blocks_to_chunks("doc", analyze_text_pages(["\n\n   \n\nreal text\n"]))
    assert [c.text for c in chunks] == ["real text"]


def test_anchor_id_roundtrips() -> None:
    assert anchor_id(3, 2) == "p3#b2"
    assert parse_anchor("p3#b2") == (3, 2)
    assert parse_anchor("not-an-anchor") is None
    assert parse_anchor(None) is None


# --------------------------------------------------------------------------- #
# Slice 2: the anchor fields are additive, never required
# --------------------------------------------------------------------------- #
def test_citation_without_an_anchor_is_still_valid() -> None:
    citation = Citation(document_id="d", title="t", uri="u", page=4)
    assert citation.anchor is None
    assert citation.bbox is None
    payload = to_jsonable(citation)
    assert payload["page"] == 4
    assert payload["anchor"] is None
    assert payload["bbox"] is None


def test_chunk_without_an_anchor_is_still_valid() -> None:
    chunk = DocumentChunk(document_id="d", ordinal=0, text="body", page=1)
    assert chunk.anchor is None
    assert chunk.bbox is None
    assert chunk.kind is BlockKind.PARAGRAPH


def test_anchored_citation_serializes_its_box() -> None:
    citation = Citation(
        document_id="d",
        title="t",
        uri="u",
        page=2,
        anchor="p2#b1",
        bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3),
    )
    payload = to_jsonable(citation)
    assert payload["anchor"] == "p2#b1"
    assert payload["bbox"] == {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.3}
