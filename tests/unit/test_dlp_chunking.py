from __future__ import annotations

from types import SimpleNamespace

import pytest

from enterprise_kb.adapters.gcp.dlp_redaction import (
    _MAX_DLP_CHUNK_BYTES,
    DlpRedactionAdapter,
    _safe_chunks,
)
from enterprise_kb.config import PiiSettings, Settings


def test_large_extracted_document_splits_losslessly_below_dlp_limit() -> None:
    text = (("account-holder S1234567D\n" * 20000) + "final").replace("S1234567D", "S7654321J")
    chunks = _safe_chunks(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= _MAX_DLP_CHUNK_BYTES for chunk in chunks)
    assert all(chunk[-1].isspace() for chunk in chunks[:-1])


def test_dense_identifiers_stay_below_finding_limit_per_request() -> None:
    text = "S7654321J " * 10_000
    chunks = _safe_chunks(text)
    assert len(chunks) > 1
    assert all(chunk.count("S7654321J") < 3_000 for chunk in chunks)


def test_oversized_unbroken_identifier_refuses_instead_of_bisecting() -> None:
    with pytest.raises(ValueError, match="token larger"):
        _safe_chunks("S" * (_MAX_DLP_CHUNK_BYTES + 1))


def test_inline_multi_jurisdiction_request_is_used_for_every_chunk() -> None:
    adapter = DlpRedactionAdapter(
        Settings(profile="gcp", pii=PiiSettings(jurisdictions=("SG", "JP")))
    )
    requests: list[dict[str, object]] = []

    class Client:
        def deidentify_content(self, *, request, retry, timeout):  # noqa: ANN001
            requests.append(request)
            return SimpleNamespace(
                item=SimpleNamespace(value=request["item"]["value"]),
                overview=SimpleNamespace(transformation_summaries=[]),
            )

    adapter._client = Client()
    adapter._retry_policy = lambda: object()  # type: ignore[method-assign]
    text = ("safe line\n" * 50000) + "tail"
    result = adapter.redact(text)
    assert result.text == text
    assert len(requests) > 1
    names = {row["info_type"]["name"] for row in requests[0]["inspect_config"]["custom_info_types"]}
    assert {"SG_NRIC_FIN", "JP_MY_NUMBER"} <= names


def test_cross_boundary_pii_is_reinspected_and_masked(monkeypatch) -> None:
    adapter = DlpRedactionAdapter(Settings(profile="gcp"))

    class Client:
        def deidentify_content(self, *, request, retry, timeout):  # noqa: ANN001
            value = request["item"]["value"]
            masked = value.replace("John Smith", "##########")
            return SimpleNamespace(
                item=SimpleNamespace(value=masked),
                overview=SimpleNamespace(transformation_summaries=[]),
            )

    adapter._client = Client()
    adapter._retry_policy = lambda: object()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "enterprise_kb.adapters.gcp.dlp_redaction._safe_chunks",
        lambda text: ("prefix John ", "Smith suffix"),
    )
    result = adapter.redact("prefix John Smith suffix")
    assert result.text == "prefix ########## suffix"
