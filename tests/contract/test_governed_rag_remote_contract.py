"""The enterprise-knowledge-base-owned remote contract is versioned,
discoverable and identity-safe.
"""

from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from enterprise_kb.api.app import app
from enterprise_kb.api.contract import (
    ANSWER_PATH,
    CONTRACT_ID,
    CONTRACT_MANIFEST_PATH,
    CONTRACT_VERSION,
    SEARCH_PATH,
)


def test_contract_manifest_owns_the_search_and_answer_operation_names() -> None:
    response = TestClient(app, client=LOOPBACK_PEER).get(CONTRACT_MANIFEST_PATH)

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["version"]) == (CONTRACT_ID, CONTRACT_VERSION)
    assert body["operations"] == {
        "search": {"method": "POST", "path": SEARCH_PATH},
        "answer": {"method": "POST", "path": ANSWER_PATH},
    }
    assert body["identity"] == {
        "actor_source": "server-verified",
        "tenant_source": "server-verified",
        "acl_principals": "optional-narrowing-only",
    }


def test_openapi_request_contract_cannot_accept_actor_or_tenant_assertions() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]

    for model in ("SearchRequest", "AnswerRequest"):
        properties = components[model]["properties"]
        assert "actor" not in properties
        assert "tenant" not in properties
        assert "acl_principals" in properties


def test_openapi_answer_contract_requires_evidence_and_review_state() -> None:
    answer = app.openapi()["components"]["schemas"]["AnswerResponse"]
    properties = answer["properties"]

    assert {"citations", "requires_human_review", "review_level", "review_reasons"} <= set(
        properties
    )
    assert properties["requires_human_review"]["default"] is True
