"""Fetch stage of the corpus pipeline.

Each :class:`~enterprise_kb.domain.models.Document` in the registry points at a source
URI; this stage downloads its bytes, checksums them, and hands the (document, content,
mime_type) triple on for redaction and ingestion into the governed store (SPEC §2).

This module is deliberately framework-light: it depends only on ``httpx``, ``pyyaml`` and
the pure domain models, so it imports cleanly under the on-prem/test profile with no
Google Cloud SDK installed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from ..domain.models import AclTag, Document, SourceSystem

_USER_AGENT = (
    "enterprise-knowledge-base/0.1 (+https://github.com/portable-genai/enterprise-knowledge-base) "
    "corpus-fetcher"
)
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MAX_REDIRECTS = 5
MAX_REGISTRY_BYTES = 1 * 1024 * 1024
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

# Fallback MIME types keyed on the URL/content-type suffix; most corpus docs are PDFs.
_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
}
_DEFAULT_MIME = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """A document plus its downloaded bytes and resolved MIME type."""

    document: Document
    content: bytes
    mime_type: str
    checksum: str = ""


def load_registry(path: str | Path) -> list[Document]:
    """Parse the YAML source registry into typed :class:`Document` objects.

    The registry shape is ``{"documents": [ {Document-shaped dict}, ... ]}``. Enum fields
    (``source_system``) are coerced from their string values; ``acl_tags`` is normalised
    to a tuple of :class:`AclTag` so the resulting dataclass stays hashable/frozen.
    """
    rendered_path = str(path)
    if rendered_path.startswith("gs://"):
        raw = yaml.safe_load(_read_gcs_registry(rendered_path)) or {}
    else:
        registry_path = Path(rendered_path)
        size = registry_path.stat().st_size
        if size > MAX_REGISTRY_BYTES:
            raise ValueError(
                f"registry exceeds the {MAX_REGISTRY_BYTES}-byte governed input limit: {size}"
            )
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("documents", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"registry at {path!s} must contain a list of documents")
    return [_to_document(entry) for entry in entries]


def _read_gcs_registry(uri: str) -> str:
    """Read a reviewed registry object with ADC; imported lazily for local portability."""
    return download_gcs_bytes(uri, MAX_REGISTRY_BYTES, "corpus registry").decode("utf-8")


def download_gcs_bytes(uri: str, max_bytes: int, label: str) -> bytes:
    """Download one generation-consistent GCS object after a metadata size gate."""
    bucket_name, object_name = _gcs_location(uri)
    from google.cloud import storage

    blob = storage.Client().bucket(bucket_name).blob(object_name)
    return _bounded_blob_download(blob, max_bytes=max_bytes, label=label)


def _bounded_blob_download(blob: object, *, max_bytes: int, label: str) -> bytes:
    """Reject oversized/unknown metadata before allocating an object payload."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    blob.reload()  # type: ignore[attr-defined]
    raw_size = getattr(blob, "size", None)
    if raw_size is None:
        raise ValueError(f"{label} has no trusted GCS size metadata")
    size = int(raw_size)
    if size < 0 or size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte governed input limit: {size}")
    if size == 0:
        return b""
    generation = getattr(blob, "generation", None)
    if generation is None:
        raise ValueError(f"{label} has no trusted GCS generation metadata")
    content = bytes(
        blob.download_as_bytes(  # type: ignore[attr-defined]
            start=0,
            end=size - 1,
            if_generation_match=int(generation),
        )
    )
    if len(content) != size or len(content) > max_bytes:
        raise ValueError(f"{label} changed size during its generation-bound download")
    return content


def _to_document(entry: dict) -> Document:
    """Build one :class:`Document` from a registry dict, validating enum members.

    Registry documents are the **shared/global** bank corpus and are deliberately left at
    ``tenant=""`` (visible to every tenant); the scheduled refresh job (``corpus-pipeline``)
    is a system actor, not tenant-scoped. The local demo's direct ingest route may create
    tenant-scoped documents, but managed serving is read-only: any future managed per-tenant
    registry needs a separately reviewed pipeline contract. A registry ``tenant:`` field is
    therefore NOT read here on purpose; silently accepting it would advertise isolation the
    scheduled refresh path does not implement.
    """
    try:
        source_raw = entry.get("source_system", SourceSystem.OTHER.value)
        source_system = (
            source_raw if isinstance(source_raw, SourceSystem) else SourceSystem(source_raw)
        )
    except ValueError:
        source_system = SourceSystem.OTHER
    return Document(
        id=str(entry["id"]),
        title=str(entry["title"]).strip(),
        uri=str(entry.get("uri", "")).strip(),
        source_system=source_system,
        acl_tags=tuple(AclTag(label=str(t)) for t in (entry.get("acl_tags", ()) or ())),
        version=str(entry.get("version", "unknown")),
    )


def compute_checksum(content: bytes) -> str:
    """SHA-256 hex digest used as the freshness/version fingerprint in the ledger."""
    return hashlib.sha256(content).hexdigest()


def _gcs_location(uri: str) -> tuple[str, str]:
    bucket_name, separator, object_name = uri.removeprefix("gs://").partition("/")
    if not separator or not bucket_name or not object_name:
        raise ValueError("GCS document URI must be gs://BUCKET/OBJECT")
    return bucket_name, object_name


def _gcs_mime(uri: str, content_type: str | None) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime:
        return mime
    suffix = Path(uri).suffix.lower()
    return _EXTENSION_MIME.get(suffix, _DEFAULT_MIME)


def _fetch_gcs(document: Document) -> FetchedDocument:
    """Fetch one private GCS object with ADC; SDK import remains managed-only/lazy."""
    bucket_name, object_name = _gcs_location(document.uri)
    from google.cloud import storage

    blob = storage.Client().bucket(bucket_name).blob(object_name)
    content = _bounded_blob_download(
        blob,
        max_bytes=MAX_DOCUMENT_BYTES,
        label=f"source document {document.id!r}",
    )
    return FetchedDocument(
        document=document,
        content=content,
        mime_type=_gcs_mime(document.uri, getattr(blob, "content_type", None)),
        checksum=compute_checksum(content),
    )


def _resolve_mime(document: Document, response: httpx.Response) -> str:
    """Prefer the server's Content-Type; fall back to the URL extension."""
    header = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if header:
        return header
    suffix = Path(httpx.URL(document.uri).path).suffix.lower()
    return _EXTENSION_MIME.get(suffix, _DEFAULT_MIME)


def fetch_document(
    document: Document,
    *,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
) -> FetchedDocument:
    """Download one document's bytes into a :class:`FetchedDocument`.

    Computes a SHA-256 checksum (used downstream as the version fingerprint and the
    ledger's change-detection key) and resolves a MIME type from the response headers.
    Raises ``httpx.HTTPStatusError`` on a non-2xx response so the ingest layer can mark
    the document FAILED in the ledger rather than indexing an error page.
    """
    if document.uri.startswith("gs://"):
        return _fetch_gcs(document)

    owns_client = client is None
    client = client or new_client(timeout)
    try:
        with client.stream("GET", document.uri) as response:
            response.raise_for_status()
            header = response.headers.get("content-length")
            if header is not None and int(header) > MAX_DOCUMENT_BYTES:
                raise ValueError(
                    f"source document {document.id!r} exceeds the "
                    f"{MAX_DOCUMENT_BYTES}-byte governed input limit"
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_DOCUMENT_BYTES:
                    raise ValueError(
                        f"source document {document.id!r} exceeds the "
                        f"{MAX_DOCUMENT_BYTES}-byte governed input limit"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            mime_type = _resolve_mime(document, response)
        return FetchedDocument(
            document=document,
            content=content,
            mime_type=mime_type,
            checksum=compute_checksum(content),
        )
    finally:
        if owns_client:
            client.close()


def new_client(timeout: httpx.Timeout = _DEFAULT_TIMEOUT) -> httpx.Client:
    """Construct a polite, redirect-following client to share across a fetch batch."""
    return httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
    )
