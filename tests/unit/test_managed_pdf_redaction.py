"""Binary documents are extracted before redaction; only redacted text is persisted."""

from types import SimpleNamespace

import pytest

from enterprise_kb.adapters.gcp.gcs_ingestion import GcsPortableIngestionAdapter
from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.config import Settings
from enterprise_kb.domain.models import Document


def _one_page_pdf(text: str) -> bytes:
    """Build a small standards-compliant PDF fixture without a PDF-generation dependency."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)


class _Blob:
    uploaded = b""

    def upload_from_string(self, content: bytes, content_type: str) -> None:
        assert content_type == "text/plain; charset=utf-8"
        self.uploaded = content


def test_pdf_remains_parseable_and_raw_pii_never_reaches_managed_storage() -> None:
    settings = Settings(profile="gcp", project_id="fictional-prod")
    adapter = GcsPortableIngestionAdapter(settings)
    blob = _Blob()
    adapter._storage_client = SimpleNamespace(
        bucket=lambda name: SimpleNamespace(blob=lambda path: blob)
    )
    pdf = _one_page_pdf("Customer S7654321Z email alice@bank.test")

    extracted, prepared_mime = adapter.prepare_for_redaction(pdf, "application/pdf")
    extracted_text = extracted.decode()
    assert prepared_mime == "text/plain"
    assert "S7654321Z" in extracted_text
    redacted = LocalRegexRedactionAdapter(settings).redact(extracted_text).text.encode()

    result = adapter.ingest(
        Document(id="policy", title="Policy", uri="gs://bucket/source.pdf"),
        redacted,
        prepared_mime,
    )
    assert result.ok
    assert blob.uploaded
    assert not blob.uploaded.startswith(b"%PDF")
    assert b"S7654321Z" not in blob.uploaded
    assert b"alice@bank.test" not in blob.uploaded


def test_malformed_pdf_is_refused_instead_of_decoded_as_binary_garbage() -> None:
    adapter = GcsPortableIngestionAdapter(Settings(profile="gcp", project_id="fictional-prod"))

    with pytest.raises(ValueError, match="PDF extraction failed"):
        adapter.prepare_for_redaction(b"%PDF-this-is-not-a-document", "application/pdf")
