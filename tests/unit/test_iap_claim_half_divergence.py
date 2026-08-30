"""Why this repository keeps its own claim half, proved by running both.

Every other user-facing repository in this fleet now ends `resolve()` with one
:func:`hex_service_kit.federation.principal_from_iap_claims` call. This one does not, and the
reason is not that nobody got to it. Adoption EXECUTED the commons against this adapter over
the same claim sets and the two disagreed on every row, in three separate ways. One of the
three ran the wrong direction and has since been fixed on this side; two still stand, and two
is enough.

**1. An empty tenant used to mean the OPPOSITE thing here. CLOSED 2026-08-30, on this side.**
:func:`domain._grounded.filter_by_tenant` applied no partition at all when the caller's tenant
was empty: the trusted-tooling reading, and it saw every tenant's passages. Everywhere else in
the fleet an empty tenant is the fail-closed answer, so the commons resolving "no reviewed
tenant" to `""` would not have failed closed here, it would have failed OPEN: a verified IAP
user carrying no ``hd`` (a personal account the edge admits, an external federated identity)
would have gone from a partition of their own mail domain to no partition at all.

The org decision of 2026-08-30 fixed the direction rather than the callers: an empty tenant now
reads the shared corpus and nothing else, which is what the fleet means by fail-closed and what
``mcp/server.py`` already claimed in writing that its tenant-less callers get. **So this row no
longer excludes the commons** — the substitution would now be a narrowing, and a safe one. It
is kept, inverted, because the row it replaced is the reason anyone would look, and because the
local tail's mail-domain fallback is now a product preference about personal accounts rather
than the safety property it was standing in for. Rows (2) and (3) are what the exclusion rests
on.

**2. The audit subject is the opaque ``sub``, not the email.** IAP's stable subject is what
this repository attributes an action to, precisely so a user's address is never copied into a
Cloud Logging label. ``principal_from_iap_claims`` reads ``email or sub`` and has no knob for
it, so adopting it would rewrite every audit actor in the store.

**3. Service identities are mapped by EMAIL, not by domain.** ``KB_IAP_SERVICE_TENANTS`` maps a
full service-account address to a reviewed tenant and refuses an unmapped one outright.
``FederationPolicy`` carries ``machine_tenant``, one string for every machine caller, and
``allowed_machine_subjects``, an allowlist with no tenant attached; ``tenant_for`` short
circuits on ``machine=True`` before ``domain_tenants`` is consulted at all. Neither expresses a
per-service-account tenant.

Written as a comparison rather than as a comment, so that "we should adopt this" is a question
the suite answers rather than one a reader has to take on trust. If the commons grows a knob
for any of the three, the matching row here goes RED and says so.
"""

from __future__ import annotations

from typing import Any

import pytest
from hex_service_kit.federation import (
    IAP_ISSUER,
    FederationPolicy,
    principal_from_iap_claims,
)

from enterprise_kb.adapters.gcp.iap_identity import IapIdentityAdapter
from enterprise_kb.domain._grounded import filter_by_tenant
from enterprise_kb.domain.identity import IdentityError, RequestContext
from enterprise_kb.domain.models import Citation, RetrievedPassage

_AUDIENCE = "/projects/1234567890/global/backendServices/42"
_SERVICE = "kb-indexer@demo-project.iam.gserviceaccount.com"


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": _AUDIENCE,
        "sub": "accounts.google.com:100000000000000000001",
        "email": "avery.stone@example-bank.test",
        "hd": "example-bank.test",
        "exp": 4102444800,
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not None}


def _resolved(claims: dict[str, Any]) -> Any:
    """The shipped adapter's claim half, with only the cryptography stubbed."""
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = _AUDIENCE
    adapter._audience_configured_empty = False
    adapter._service_tenants = {_SERVICE: "reference-bank"}
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims))
    object.__setattr__(adapter, "_refuse_unpinned_algorithm", lambda assertion: None)
    return adapter.resolve(RequestContext(headers={"x-goog-iap-jwt-assertion": "stub"}))


def _commons(claims: dict[str, Any]) -> Any:
    return principal_from_iap_claims(
        claims,
        FederationPolicy(tenant_from_hosted_domain=True),
        source="gcp-iap",
        include_subject_principal=True,
    )


# --------------------------------------------------------------------------------------- #
# (1) The direction of an empty tenant, which is what makes this an exclusion rather than a
#     preference.
# --------------------------------------------------------------------------------------- #
def test_an_empty_tenant_now_closes_the_partition_here_like_everywhere_else() -> None:
    """The row that CLOSED, asserted rather than remembered.

    This used to read ``filter_by_tenant(passages, "") == passages`` and was the premise the
    other two rows rested on. The org decision of 2026-08-30 deleted the exemption, so an
    empty tenant is now the fail-closed answer here too: the shared corpus, and nothing else.
    """
    citation = Citation(document_id="doc-1", title="Retention standard", uri="kb://doc-1", page=1)
    shared = RetrievedPassage(text="s", citation=citation, tenant="")
    owned = RetrievedPassage(
        text="x",
        citation=Citation(document_id="doc-2", title="Other", uri="kb://doc-2", page=1),
        tenant="other-bank",
    )
    assert filter_by_tenant([shared, owned], "") == [shared]
    assert filter_by_tenant([shared, owned], "reference-bank") == [shared]
    assert filter_by_tenant([owned], "reference-bank") == []


def test_a_verified_user_with_no_hosted_domain_keeps_a_partition_here() -> None:
    """The commons answers ``""``; the tail answers the mail domain. Both fail closed now.

    Before the 2026-08-30 fix this was the dangerous row: ``""`` meant "see every tenant"
    here, so substituting the commons would have been a WIDENING that no offline gate
    elsewhere would have caught, because the local profile never constructs this adapter.
    It is a narrowing now — the commons answer would admit the shared corpus only — so this
    row is a preference about how much a personal-account user should see, and no longer the
    reason the exclusion exists. Rows (2) and (3) are.
    """
    claims = _claims(hd=None, email="someone@personal.test")
    assert _resolved(claims).tenant == "personal.test"
    assert _commons(claims).tenant == ""


# --------------------------------------------------------------------------------------- #
# (2) The audit actor.
# --------------------------------------------------------------------------------------- #
def test_the_audit_subject_is_the_opaque_iap_subject_and_not_the_address() -> None:
    """``principal_from_iap_claims`` reads ``email or sub`` and offers no knob for the choice."""
    claims = _claims()
    assert _resolved(claims).subject == claims["sub"]
    assert _commons(claims).subject == claims["email"]
    # The address survives where it is useful and reviewable: as the entitlement principal.
    assert _resolved(claims).principals == (f"user:{claims['email']}",)


# --------------------------------------------------------------------------------------- #
# (3) The reviewed service-account map.
# --------------------------------------------------------------------------------------- #
def test_a_reviewed_service_account_gets_its_mapped_tenant() -> None:
    claims = _claims(hd=None, email=_SERVICE)
    assert _resolved(claims).tenant == "reference-bank"
    assert _commons(claims).tenant == ""


def test_an_unreviewed_service_account_is_refused_outright() -> None:
    """The commons would admit it with no tenant, which here means every tenant."""
    claims = _claims(hd=None, email="stranger@other-project.iam.gserviceaccount.com")
    with pytest.raises(IdentityError, match="reviewed service-email-to-tenant mapping"):
        _resolved(claims)
    assert _commons(claims).tenant == ""


# --------------------------------------------------------------------------------------- #
# What this repository DOES take from the commons, so the exclusion is narrow and stated.
# --------------------------------------------------------------------------------------- #
def test_the_transport_facts_and_the_assertion_pins_are_still_the_commons_values() -> None:
    """The exclusion is the claim half alone. Nothing here re-declares a shared constant."""
    from hex_service_kit import federation as kit

    from enterprise_kb.adapters.gcp import iap_identity

    assert iap_identity._ASSERTION_HEADER == kit.IAP_ASSERTION_HEADER
    assert iap_identity._IAP_ISSUER == kit.IAP_ISSUER
    assert iap_identity._IAP_KEYS_URL == kit.IAP_KEYS_URL
