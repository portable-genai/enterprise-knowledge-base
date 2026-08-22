"""Span ATTRIBUTES carry structure, never content, and this is the test that can tell.

The conftest ``RecordingTracer`` records span NAMES (``self.spans.append(name)``), which is
right for the tests that assert a pipeline opened its span and structurally blind to the one
defect that matters here: it throws the attributes away, so a span that started carrying the
user's query or a document body would keep every existing test green. A trace backend is not
the WORM audit trail. It has no redaction stage, a wider read audience and no retention rule
written against a regulator's requirement, so an attribute is OUTSIDE the boundary that
redact-before-retrieval (P-04) holds.

The recording tracer here keeps ``dict(attributes)``, and the content cases drive the two
real request paths with ``PII_QUERY`` and ``SAMPLE_DOCUMENT_CONTENT``, both of which embed a
planted NRIC and email, so a leak fails on the planted literal rather than on a subtlety.
"""

from __future__ import annotations

import pytest
from tests.conftest import _settings
from tests.fixtures import sample_docs

from enterprise_kb.adapters.local.tracer import LocalNoopTracerAdapter
from enterprise_kb.config import Settings

ACTOR = "analyst@bank.test"
RETAIL = (sample_docs.RETAIL_PRINCIPAL,)
DOC = sample_docs.SAMPLE_DOCUMENTS[0]
CONTENT = sample_docs.SAMPLE_DOCUMENT_CONTENT
MIME = sample_docs.SAMPLE_MIME_TYPE

#: The complete attribute key set an A2 span may carry, per span name. Widening one of
#: these is a decision about what leaves the trust boundary, so it is made here rather
#: than at a call site.
_ALLOWED = {
    "kb.search": {"action", "actor"},
    "kb.answer": {"action", "actor"},
    "kb.ingest": {"action", "actor"},
    "kb.delete": {"action", "actor"},
}

#: Planted identifiers: the query PII and the document-body PII the fixtures carry so that
#: redact-before-retrieval is provable. Neither may reach a span attribute either.
_PLANTED = (
    "S1234567A",
    "jane.doe@example.com",
    "S7654321Z",
    "alice@bank.test",
)


class _AttributeRecordingTracer(LocalNoopTracerAdapter):
    """Keeps (name, attributes) per span, unlike the name-only conftest recorder."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.spans: list[tuple[str, dict[str, str]]] = []

    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append((name, dict(attributes)))
        return super().span(name, **attributes)


@pytest.fixture
def tracer() -> _AttributeRecordingTracer:  # type: ignore[override]
    """Override the conftest tracer so both service fixtures assemble with THIS one."""
    return _AttributeRecordingTracer(_settings())


def _drive_every_span_site(kb_service, ingestion_service) -> None:
    """Drive the four real request paths, with the PII-bearing inputs where they exist."""
    kb_service.search(sample_docs.PII_QUERY, actor=ACTOR, acl_principals=RETAIL)
    kb_service.answer(sample_docs.PII_QUERY, actor=ACTOR, acl_principals=RETAIL)
    ingestion_service.ingest(DOC, CONTENT, MIME, actor=ACTOR)
    ingestion_service.delete(DOC.id, actor=ACTOR)


def test_the_request_paths_open_exactly_the_known_spans(
    kb_service, ingestion_service, tracer
) -> None:
    _drive_every_span_site(kb_service, ingestion_service)
    names = {name for name, _ in tracer.spans}
    assert names == set(_ALLOWED), (
        "the set of spans these request paths open changed; a new span site is a "
        "trust-boundary decision, so record it in _ALLOWED here deliberately"
    )


def test_every_span_carries_allowlisted_keys_only(kb_service, ingestion_service, tracer) -> None:
    _drive_every_span_site(kb_service, ingestion_service)
    assert tracer.spans, "the request paths opened no span at all"
    for name, attributes in tracer.spans:
        assert name in _ALLOWED, f"unexpected span {name!r}; add it here deliberately"
        assert set(attributes) == _ALLOWED[name], (
            f"span {name!r} attribute keys changed; widening the set is a trust-boundary "
            "decision, so update _ALLOWED here deliberately"
        )


def test_no_span_attribute_carries_the_planted_identifiers(
    kb_service, ingestion_service, tracer
) -> None:
    """The query's NRIC/email and the document body's NRIC/email stay out of the trace."""
    _drive_every_span_site(kb_service, ingestion_service)
    emitted = " ".join(value for _, attributes in tracer.spans for value in attributes.values())
    for planted in _PLANTED:
        assert planted not in emitted, f"span attribute leaked the planted {planted!r}"
    assert sample_docs.SAMPLE_QUERY not in emitted, "the query text reached a span attribute"


def test_every_attribute_value_is_a_string(kb_service, ingestion_service, tracer) -> None:
    """The port declares str values; a structured object smuggles content past a grep."""
    _drive_every_span_site(kb_service, ingestion_service)
    for name, attributes in tracer.spans:
        for key, value in attributes.items():
            assert isinstance(value, str), f"span {name!r} attribute {key!r} is not a str"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
