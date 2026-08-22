"""Managed registry, ACL and source inputs are bounded before payload allocation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from enterprise_kb.domain.models import Document
from enterprise_kb.pipelines import acl_sync, fetch


class _Blob:
    def __init__(self, *, size: int | None, content: bytes = b"", generation: int = 7) -> None:
        self.size = size
        self.content = content
        self.generation = generation
        self.reloads = 0
        self.downloads: list[dict[str, int]] = []

    def reload(self) -> None:
        self.reloads += 1

    def download_as_bytes(self, **kwargs: int) -> bytes:
        self.downloads.append(kwargs)
        return self.content


@pytest.mark.parametrize("size", [None, 11])
def test_gcs_metadata_gate_refuses_unknown_or_oversized_object_before_download(
    size: int | None,
) -> None:
    blob = _Blob(size=size, content=b"x" * 11)

    with pytest.raises(ValueError, match="size metadata|exceeds"):
        fetch._bounded_blob_download(blob, max_bytes=10, label="source document")

    assert blob.reloads == 1
    assert blob.downloads == []


def test_gcs_download_is_byte_bounded_and_generation_consistent() -> None:
    blob = _Blob(size=4, content=b"safe", generation=19)

    content = fetch._bounded_blob_download(blob, max_bytes=4, label="registry")

    assert content == b"safe"
    assert blob.downloads == [{"start": 0, "end": 3, "if_generation_match": 19}]


def test_local_registry_is_bounded_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text("documents: []\n" + ("x" * 32), encoding="utf-8")
    monkeypatch.setattr(fetch, "MAX_REGISTRY_BYTES", 16)

    with pytest.raises(ValueError, match="registry exceeds"):
        fetch.load_registry(registry)


def test_acl_download_uses_dedicated_artifact_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, str]] = []

    def _download(uri: str, max_bytes: int, label: str) -> bytes:
        calls.append((uri, max_bytes, label))
        return b'{"bindings":[]}'

    monkeypatch.setattr(acl_sync, "download_gcs_bytes", _download)

    assert acl_sync._download("gs://control/acl/bindings.json") == '{"bindings":[]}'
    assert calls == [
        ("gs://control/acl/bindings.json", acl_sync.MAX_ACL_BINDINGS_BYTES, "ACL bindings")
    ]


def test_http_source_stream_refuses_over_limit_without_buffering_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "MAX_DOCUMENT_BYTES", 4)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"12345"))
    client = httpx.Client(transport=transport)

    with pytest.raises(ValueError, match="source document 'large'.*exceeds"):
        fetch.fetch_document(
            Document(id="large", title="Large", uri="https://bank.example/large.pdf"),
            client=client,
        )
