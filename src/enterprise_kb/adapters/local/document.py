"""Portable document parser shared by the local and managed ingestion profiles.

SDK-free, deterministic **layout-aware** extraction. If ``pypdf`` is importable and the
bytes look like a PDF, each PDF page becomes one page of text; otherwise the bytes are
decoded as UTF-8 text (a form feed starts a new page). Each page is then run through the
pure domain layout analyser
(:func:`enterprise_kb.domain.layout.analyze_text_pages`), which splits it into
classified, boxed blocks.

That is the point of this adapter: it produces the SAME
:class:`~enterprise_kb.domain.layout.ParsedDocument` shape managed ingestion stores, so offline
yields anchors of the same form from fixtures and every downstream stage (chunking,
citation store, claim-to-anchor resolution) is exercised offline. The offline geometry is
a reproducible estimate from the character grid rather than measured glyph boxes.

It is unconditional and imports no google-cloud package.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config import Settings
from ...domain.layout import ParsedDocument, analyze_text_pages


@dataclass(frozen=True, slots=True)
class DocumentExtract:
    """Layout-aware extraction of a document.

    ``layout`` is the structured parse (pages -> classified, boxed blocks) every
    downstream stage reads; ``text`` and ``pages`` are the flat views kept for the
    existing plain-text consumers, so nothing that predates the anchor work had to
    change.
    """

    text: str
    pages: tuple[str, ...]
    mime_type: str
    layout: ParsedDocument = ParsedDocument()


class LocalDocumentParser:
    """Parse document bytes into layout-aware pages and blocks, no SDK required."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, content: bytes | str, mime_type: str) -> DocumentExtract:
        if isinstance(content, str):
            content = content.encode("utf-8")
        if self._looks_like_pdf(content, mime_type):
            pdf_pages = self._extract_pdf_pages(content)
            if pdf_pages:
                return self._extract(pdf_pages, "application/pdf")
            raise ValueError("PDF extraction failed; refusing to decode binary bytes as text")
        # Plain-text passthrough: decode as UTF-8; a form feed starts a new page.
        text = content.decode("utf-8", errors="replace")
        return self._extract([text], mime_type or "text/plain")

    @staticmethod
    def _extract(pages: list[str], mime_type: str) -> DocumentExtract:
        """Run the pure layout analyser and assemble the extract (single code path)."""
        layout = analyze_text_pages(pages, mime_type=mime_type)
        # The flat view is derived FROM the layout, so a form feed that splits a source
        # string into two layout pages splits ``pages`` the same way: the two views can
        # never disagree about how many pages the document has.
        page_texts = layout.page_texts
        return DocumentExtract(
            text="\n\n".join(page_texts),
            pages=page_texts,
            mime_type=mime_type,
            layout=layout,
        )

    @staticmethod
    def _looks_like_pdf(content: bytes, mime_type: str) -> bool:
        if "pdf" in (mime_type or "").lower():
            return True
        return isinstance(content, bytes) and content[:5] == b"%PDF-"

    @staticmethod
    def _extract_pdf_pages(content: bytes) -> list[str]:
        """Extract per-page text via pypdf when available; empty list if it is not."""
        try:
            import io

            from pypdf import PdfReader  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 - caller refuses the unavailable parser explicitly
            return []
        try:
            reader = PdfReader(io.BytesIO(content))
            return [(page.extract_text() or "") for page in reader.pages]
        except Exception:  # noqa: BLE001 - caller refuses malformed PDFs explicitly
            return []
