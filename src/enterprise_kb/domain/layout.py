"""Layout model and the pure text layout analyser (anchor precision, slice 1 + 2).

A parsed document is not a bag of page strings: it is an ordered set of **layout
blocks** (headings, paragraphs, lists, tables) each of which occupies a rectangle on a
page. That structure is what turns a page-level citation into an *anchor-level* one, so
a reviewer opening the evidence lands on the paragraph or the table rather than on
page 7 of 40.

This module owns three things, all pure standard library:

* the **shape** every parser produces : :class:`BlockKind`, :class:`LayoutBlock`,
  :class:`ParsedPage`, :class:`ParsedDocument`. The portable managed parser fills it
  from real ``blocks`` / ``tables`` / ``layout`` / ``bounding_poly`` responses; the local
  document adapter fills the SAME shape from offline fixtures, so the offline profile
  produces anchors of the same form as the managed one (P-02);
* the **anchor id** derivation (:func:`anchor_id`), the stable ``p{page}#b{index}``
  locator that a :class:`~enterprise_kb.domain.kernel.Citation` carries; and
* the **offline layout analyser** (:func:`analyze_text_pages`), a deterministic,
  dependency-free block splitter/classifier the local adapter uses. It lives in the
  domain, not in the adapter, because it is pure text analysis with no I/O: same input,
  same blocks, same boxes, every run.

The geometry the analyser produces for plain text is an *estimate* from the character
grid (there is no glyph metrics source offline), which is exactly the honest offline
portable deterministic geometry estimate: same shape and anchors, coordinates
that are reproducible rather than physically measured.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .kernel import BlockKind, BoundingBox
from .models import DocumentChunk

__all__ = [
    "BlockKind",  # re-exported from the kernel so parsers import one module
    "LayoutBlock",
    "ParsedDocument",
    "ParsedPage",
    "analyze_text_pages",
    "anchor_id",
    "blocks_to_chunks",
    "parse_anchor",
]

#: Character grid the offline analyser lays text out on. A page is assumed to hold
#: ``_GRID_ROWS`` lines of ``_GRID_COLS`` columns; a block's box is the rectangle its
#: lines occupy on that grid, clamped to the page. These two numbers only affect the
#: offline coordinate estimate, never the anchor id, so a change here cannot move an
#: anchor.
_GRID_ROWS = 60
_GRID_COLS = 90

_PAGE_BREAK = "\f"
_LIST_RE = re.compile(r"^\s*(?:[-*•]|\(?\d+[.)])\s+")
_TABLE_COLUMN_RE = re.compile(r"\S {2,}\S")


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    """One layout element on a page, with its text, role and bounding box."""

    page: int  # 1-based page number
    index: int  # 0-based position of the block within the page
    text: str
    kind: BlockKind = BlockKind.PARAGRAPH
    bbox: BoundingBox | None = None

    @property
    def anchor(self) -> str:
        """The stable ``p{page}#b{index}`` locator for this block."""
        return anchor_id(self.page, self.index)


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page of a parsed document."""

    number: int  # 1-based
    blocks: tuple[LayoutBlock, ...] = ()

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The layout-aware parse of one document: the shape every parser produces."""

    pages: tuple[ParsedPage, ...] = ()
    mime_type: str = "text/plain"

    @property
    def blocks(self) -> tuple[LayoutBlock, ...]:
        return tuple(b for p in self.pages for b in p.blocks)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def page_texts(self) -> tuple[str, ...]:
        return tuple(p.text for p in self.pages)


# --------------------------------------------------------------------------- #
# Anchor ids
# --------------------------------------------------------------------------- #
def anchor_id(page: int, index: int) -> str:
    """The stable anchor locator for block ``index`` on ``page`` (1-based page)."""
    return f"p{page}#b{index}"


def parse_anchor(anchor: str | None) -> tuple[int, int] | None:
    """Inverse of :func:`anchor_id`: ``"p3#b2"`` -> ``(3, 2)``; ``None`` if unparseable."""
    if not anchor:
        return None
    match = re.fullmatch(r"p(\d+)#b(\d+)", anchor.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


# --------------------------------------------------------------------------- #
# Chunk projection
# --------------------------------------------------------------------------- #
def blocks_to_chunks(document_id: str, parsed: ParsedDocument) -> list[DocumentChunk]:
    """Project a parsed document into ordinal-ordered, anchored ``DocumentChunk`` rows.

    One chunk per layout block, in reading order across pages, so the chunk ordinal is
    stable for a given parse and the anchor is stable for a given block position. Blocks
    whose text is blank are dropped: an empty chunk can ground nothing.
    """
    chunks: list[DocumentChunk] = []
    ordinal = 0
    for page in parsed.pages:
        for block in page.blocks:
            text = block.text.strip()
            if not text:
                continue
            chunks.append(
                DocumentChunk(
                    document_id=document_id,
                    ordinal=ordinal,
                    text=text,
                    page=block.page,
                    anchor=block.anchor,
                    bbox=block.bbox,
                    kind=block.kind,
                )
            )
            ordinal += 1
    return chunks


# --------------------------------------------------------------------------- #
# Offline layout analyser
# --------------------------------------------------------------------------- #
def analyze_text_pages(pages: Sequence[str], mime_type: str = "text/plain") -> ParsedDocument:
    """Split plain-text pages into classified, boxed layout blocks (deterministic).

    Blocks are separated by blank lines; a form feed inside a page string splits it into
    further pages first, so a single-string fixture can describe a multi-page document.
    """
    expanded: list[str] = []
    for raw in pages:
        expanded.extend(str(raw).split(_PAGE_BREAK))
    out: list[ParsedPage] = []
    for page_no, page_text in enumerate(expanded, start=1):
        out.append(ParsedPage(number=page_no, blocks=_blocks_for_page(page_no, page_text)))
    return ParsedDocument(pages=tuple(out), mime_type=mime_type or "text/plain")


def _blocks_for_page(page_no: int, page_text: str) -> tuple[LayoutBlock, ...]:
    lines = page_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[LayoutBlock] = []
    buffer: list[str] = []
    start_line = 0
    index = 0
    for lineno, line in enumerate(lines):
        if line.strip():
            if not buffer:
                start_line = lineno
            buffer.append(line)
            continue
        if buffer:
            blocks.append(_make_block(page_no, index, buffer, start_line))
            index += 1
            buffer = []
    if buffer:
        blocks.append(_make_block(page_no, index, buffer, start_line))
    return tuple(blocks)


def _make_block(page_no: int, index: int, lines: list[str], start_line: int) -> LayoutBlock:
    text = "\n".join(line.rstrip() for line in lines).strip()
    return LayoutBlock(
        page=page_no,
        index=index,
        text=text,
        kind=classify_block(lines),
        bbox=_grid_bbox(lines, start_line),
    )


def classify_block(lines: Iterable[str]) -> BlockKind:
    """Classify a block from its raw lines (deterministic, order of checks is the rule).

    Table first (a pipe table or two or more column-gapped lines), then list (every
    non-blank line starts with a bullet or an enumerator), then heading (a single short
    line that does not end in sentence punctuation), else paragraph.
    """
    rows = [line for line in lines if line.strip()]
    if not rows:
        return BlockKind.PARAGRAPH
    pipe_rows = sum(1 for line in rows if line.count("|") >= 2)
    gapped_rows = sum(1 for line in rows if _TABLE_COLUMN_RE.search(line))
    if pipe_rows >= 2 or gapped_rows >= 2:
        return BlockKind.TABLE
    if all(_LIST_RE.match(line) for line in rows):
        return BlockKind.LIST
    if len(rows) == 1:
        single = rows[0].strip()
        if len(single) <= 80 and not single.endswith((".", "!", "?")):
            return BlockKind.HEADING
    return BlockKind.PARAGRAPH


def _grid_bbox(lines: list[str], start_line: int) -> BoundingBox:
    """Estimate a normalised box for a block from its position on the character grid."""
    widths = [len(line.rstrip()) for line in lines] or [0]
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()] or [0]
    x0 = _clamp01(min(indents) / _GRID_COLS)
    x1 = _clamp01(max(widths) / _GRID_COLS)
    y0 = _clamp01(start_line / _GRID_ROWS)
    y1 = _clamp01((start_line + len(lines)) / _GRID_ROWS)
    return BoundingBox(x0=round(x0, 4), y0=round(y0, 4), x1=round(max(x1, x0), 4), y1=round(y1, 4))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
