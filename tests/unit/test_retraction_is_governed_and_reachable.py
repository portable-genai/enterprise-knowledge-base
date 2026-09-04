"""An indexed document must be withdrawable through the governed surface, on every profile.

enterprise-knowledge-base exposed ingest, search, answer and a DELETE. The DELETE was gated behind
`_require_local_write_surface`, which refuses anything that is not the local demo profile,
because managed serving identities are deliberately read-only for corpus WRITES and the bulk
ingest path runs as a reviewed pipeline job.

Retraction is not a bulk corpus write and the two do not belong behind one switch. The
consequence of collapsing them was recorded in cdd-sow-research, whose platform adapter
raises `NotImplementedError` by name rather than reporting a removal that did not happen: on
the platform profile the system could not honour an erasure request, could not withdraw
evidence filed against the wrong case, and could not correct a document later found to be
forged, while continuing to cite all three.

So retraction gets its own path with its own entitlement, and stays reachable on every profile:

* it is per-document, synchronous and audited, not a pipeline job;
* it is authorized by a reviewed entitlement on the SERVER-VERIFIED principal, never by the
  request body and never by the mere possession of a service credential;
* it is tenant-scoped, so a caller cannot retract another tenant's document by naming its id;
* and the entitlement is three-state configuration: unset takes the reference group, and an
  emptied allowlist refuses EVERYONE rather than inheriting the unset default.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit.identity import Principal
from tests.conftest import LOOPBACK_PEER

from enterprise_kb.api import app as app_module
from enterprise_kb.api import deps
from enterprise_kb.api.security import get_principal, require_service_caller
from enterprise_kb.domain.policy import DEFAULT_RETRACTION_ENTITLEMENTS, KbPolicy, RetractionPolicy


class TestTheEntitlementIsThreeState:
    def test_unset_takes_the_reference_group(self) -> None:
        policy = KbPolicy.from_mapping({}).retraction_policy()

        assert policy.entitlements == DEFAULT_RETRACTION_ENTITLEMENTS
        assert policy.permits(DEFAULT_RETRACTION_ENTITLEMENTS[:1])

    def test_set_and_empty_refuses_everyone(self) -> None:
        """The emptied allowlist must not inherit the permissive unset default."""
        policy = KbPolicy.from_mapping({"retraction": {"entitlements": []}}).retraction_policy()

        assert policy.entitlements == ()
        assert not policy.permits(DEFAULT_RETRACTION_ENTITLEMENTS)
        assert not policy.permits(("anything",))

    def test_set_and_valid_takes_the_configured_group(self) -> None:
        policy = KbPolicy.from_mapping(
            {"retraction": {"entitlements": ["records-office"]}}
        ).retraction_policy()

        assert policy.permits(("records-office",))
        assert not policy.permits(DEFAULT_RETRACTION_ENTITLEMENTS)


class TestTheEntitlementFailsClosed:
    def test_an_empty_principal_may_not_retract(self) -> None:
        policy = KbPolicy.from_mapping({}).retraction_policy()

        assert not policy.permits(())

    def test_a_verified_but_unentitled_principal_may_not_retract(self) -> None:
        policy = KbPolicy.from_mapping({}).retraction_policy()

        assert not policy.permits(("kb-readers", "everyone"))

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_entitlement_never_matches(self, blank: str) -> None:
        policy = RetractionPolicy(entitlements=(blank,))

        assert not policy.permits((blank,))


# --------------------------------------------------------------------------- #
# The served route. Authorization is the point, so the ports are stubbed and the
# assertions are about who is refused and what the service is asked to do.
# --------------------------------------------------------------------------- #
RETRACT_PATH = "/v1/documents/doc-1/retract"


class _RecordingIngestion:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def delete(self, document_id: str, actor: str, tenant: str = "") -> None:
        self.calls.append((document_id, actor, tenant))


def _client(principal: Principal, service: Any, policy: Any = None) -> TestClient:
    app = app_module.app
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[require_service_caller] = lambda: None
    app.dependency_overrides[deps.get_ingestion_service] = lambda: service
    if policy is not None:
        app.dependency_overrides[deps.get_retraction_policy] = lambda: policy
    return TestClient(app, client=LOOPBACK_PEER)


def _teardown() -> None:
    app_module.app.dependency_overrides.clear()


def test_an_entitled_principal_retracts_and_the_delete_is_tenant_scoped() -> None:
    service = _RecordingIngestion()
    principal = Principal(
        subject="records@example.test",
        principals=DEFAULT_RETRACTION_ENTITLEMENTS,
        tenant="bank-a",
    )
    try:
        resp = _client(principal, service).post(RETRACT_PATH)

        assert resp.status_code == 200
        assert resp.json()["status"] == "retracted"
        # The tenant comes from the verified principal, never the request.
        assert service.calls == [("doc-1", "records@example.test", "bank-a")]
    finally:
        _teardown()


def test_an_unentitled_principal_is_refused_and_nothing_is_deleted() -> None:
    service = _RecordingIngestion()
    principal = Principal(
        subject="reader@example.test", principals=("kb-readers",), tenant="bank-a"
    )
    try:
        resp = _client(principal, service).post(RETRACT_PATH)

        assert resp.status_code == 403
        assert service.calls == []
    finally:
        _teardown()


def test_retraction_is_not_behind_the_local_only_write_surface() -> None:
    """The defect this closes: the only withdrawal path was local-profile-only.

    Asserted on the ROUTE's dependencies rather than by driving a request, because a request
    proves nothing here: the handler never consults the profile, so a test that flipped
    `exposure_profile` and watched a 200 come back would pass just as happily with the local-only
    guard reinstated. This assertion can actually fail, and it was watched failing by adding
    `_require_local_write_surface` back to the retract route.

    The DELETE route keeps that guard, and this pins BOTH halves: bulk corpus writes stay
    pipeline-only outside the local demo, and withdrawal does not.
    """
    routes = {
        (r.path, tuple(sorted(d.call.__name__ for d in r.dependant.dependencies)))
        for r in app_module.app.routes
        if getattr(r, "path", "").startswith("/v1/documents")
    }
    retract = next(deps_ for path, deps_ in routes if path.endswith("/retract"))
    delete = next(deps_ for path, deps_ in routes if not path.endswith("/retract"))

    assert "_require_local_write_surface" not in retract
    assert "_require_local_write_surface" in delete


def test_the_retraction_entitlement_is_not_satisfied_by_a_service_credential() -> None:
    """`ServiceCaller` proves which SERVICE calls; it says nothing about which human is behind it.

    The invariant is that setting a service credential must never widen an end-user route, so a
    verified-but-unentitled human stays refused however the calling service authenticated.
    """
    service = _RecordingIngestion()
    principal = Principal(subject="svc-user@example.test", principals=(), tenant="bank-a")
    try:
        resp = _client(principal, service).post(RETRACT_PATH)

        assert resp.status_code == 403
        assert service.calls == []
    finally:
        _teardown()
