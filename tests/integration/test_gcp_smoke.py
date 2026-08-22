"""Authenticated post-deployment smoke for the managed IAP/API journey.

This test deliberately calls the deployed HTTPS surface rather than constructing adapters in
the test process. It therefore proves that IAP admits the reviewed identity, injects an assertion
the application accepts, and that the released corpus is fresh, searchable, and answerable.

External prerequisites (all are intentionally absent from offline CI):

* ``KB_MANAGED_BASE_URL``: the Terraform ``managed_api_url`` output (HTTPS, no path);
* ``KB_MANAGED_IAP_ID_TOKEN``: a short-lived OIDC token for an exact reviewed IAP accessor,
  minted for this deployment's IAP OAuth client id and sent only to the HTTPS origin; and
* ``KB_MANAGED_EXPECTED_DOCUMENT_ID``: a reviewed document published and refreshed by the
  release (``cloud-onboarding-policy`` for the shipped fictional demo).

The token identity must also have a matching tenant/principal binding in the reviewed ACL
authority. Never store the token in source, an artifact, or pytest output. Run with::

    pytest tests/integration/test_gcp_smoke.py -q -m integration
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx
import pytest

_BASE_URL_ENV = "KB_MANAGED_BASE_URL"
_TOKEN_ENV = "KB_MANAGED_IAP_ID_TOKEN"
_DOCUMENT_ENV = "KB_MANAGED_EXPECTED_DOCUMENT_ID"
_QUERY = "What due diligence is required before onboarding a cloud provider?"
_REQUIRED_ENV = (_BASE_URL_ENV, _TOKEN_ENV, _DOCUMENT_ENV)
_CONFIGURED_ENV = tuple(name for name in _REQUIRED_ENV if os.environ.get(name, "").strip())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _CONFIGURED_ENV,
        reason="set the managed URL, short-lived IAP token and expected document id",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def complete_external_evidence() -> None:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        pytest.fail(f"managed smoke configuration is partial; missing: {', '.join(missing)}")


@pytest.fixture(scope="module")
def expected_document_id() -> str:
    return os.environ[_DOCUMENT_ENV].strip()


@pytest.fixture(scope="module")
def managed_client() -> Iterator[httpx.Client]:
    base_url = os.environ[_BASE_URL_ENV].strip().rstrip("/")
    parsed = urlsplit(base_url)
    assert parsed.scheme == "https" and parsed.netloc and not parsed.path, (
        f"{_BASE_URL_ENV} must be an HTTPS origin without a path"
    )
    token = os.environ[_TOKEN_ENV].strip()
    assert token and not token.lower().startswith("bearer "), (
        f"{_TOKEN_ENV} must contain only the token, without the Bearer prefix"
    )
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
        timeout=httpx.Timeout(90.0),
    ) as client:
        yield client


def _json(client: httpx.Client, method: str, path: str, **kwargs: object) -> dict[str, object]:
    response = client.request(method, path, **kwargs)
    assert response.status_code == 200, (
        f"{method} {path} returned {response.status_code}: {response.text[:500]}"
    )
    payload = response.json()
    assert isinstance(payload, dict), f"{method} {path} did not return a JSON object"
    return payload


def test_iap_protected_api_reports_the_managed_singapore_profile(
    managed_client: httpx.Client,
) -> None:
    health = _json(managed_client, "GET", "/healthz")
    assert health == {"status": "ok", "profile": "gcp", "region": "asia-southeast1"}


def test_released_document_is_fresh_for_the_iap_identity(
    managed_client: httpx.Client, expected_document_id: str
) -> None:
    corpus = _json(managed_client, "GET", "/v1/corpus/status")
    records = corpus.get("records")
    assert isinstance(records, list)
    matching = [record for record in records if record.get("document_id") == expected_document_id]
    assert matching, f"{expected_document_id!r} is absent from the identity-scoped corpus ledger"
    assert all(record.get("status") == "fresh" for record in matching)


def test_iap_identity_can_search_the_released_document(
    managed_client: httpx.Client, expected_document_id: str
) -> None:
    search = _json(
        managed_client,
        "POST",
        "/v1/search",
        json={"query": _QUERY, "top_k": 10, "acl_principals": []},
    )
    passages = search.get("passages")
    assert isinstance(passages, list) and passages
    document_ids = {passage["citation"]["document_id"] for passage in passages}
    assert expected_document_id in document_ids


def test_iap_identity_gets_a_grounded_answer_citing_the_released_document(
    managed_client: httpx.Client, expected_document_id: str
) -> None:
    answer = _json(
        managed_client,
        "POST",
        "/v1/answer",
        json={"query": _QUERY, "acl_principals": []},
    )
    assert isinstance(answer.get("answer"), str) and answer["answer"]
    citations = answer.get("citations")
    assert isinstance(citations, list) and citations
    assert expected_document_id in {citation["document_id"] for citation in citations}
    assert answer.get("requires_human_review") is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-m", "integration"]))
