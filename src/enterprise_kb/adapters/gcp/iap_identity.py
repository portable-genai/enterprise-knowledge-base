"""GCP IdentityPort adapter: verify the Identity-Aware Proxy (IAP) signed assertion.

In secure mode the deployment is fronted by Cloud IAP (Cloud Run behind an HTTPS load
balancer + IAP), which authenticates the user against the configured IdP (Workspace, or an
external client IdP via Workforce Identity Federation) and injects a signed JWT in the
``x-goog-iap-jwt-assertion`` header. This adapter VERIFIES that assertion (signature,
audience, issuer, expiry) and derives the :class:`Principal` server-side, so authentication
is configured ON the GCP service rather than hand-rolled in the app. The Google SDK imports
are lazy (mirroring the other gcp adapters) so the SDK-free local/onprem profiles never
import them, and the verified assertion is never logged.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.assertion import require_claims, require_pinned_algorithm
from hex_service_kit.identity import IdentityError as AssertionRefused

from ...config import Settings
from ...domain.identity import IdentityError, Principal, RequestContext
from ...envread import read_env_setting
from ...ports.identity import VERIFIED

_ASSERTION_HEADER = "x-goog-iap-jwt-assertion"
_IAP_KEYS_URL = "https://www.gstatic.com/iap/verify/public_key"
_IAP_ISSUER = "https://cloud.google.com/iap"

#: The claims this deployment requires before it reads any of them. `email` is required
#: outright now: it drives the reviewed service-account tenant mapping below, and an absent
#: email silently fell through to `tenant = subject`, which is a partition of one.
_REQUIRED_CLAIMS = ("iss", "sub", "email", "exp")


class IapIdentityAdapter:
    """Verify the IAP-injected JWT assertion and derive a Principal (secure mode)."""

    #: The principal comes from a Google-signed assertion whose signature, issuer, expiry and
    #: audience are checked below; the caller cannot name itself. Read by the app-object
    #: exposure guard, which stands down for a profile that binds this adapter.
    end_user_auth = VERIFIED

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Expected audience: the IAP-protected resource. For an HTTPS LB + IAP it is
        # "/projects/<NUM>/global/backendServices/<ID>"; for App Engine/Cloud Run IAP it is
        # "/projects/<NUM>/apps/<ID>". Configure via KB_IAP_AUDIENCE; required in secure mode.
        self._audience = read_env_setting("KB_IAP_AUDIENCE").value
        self._service_tenants = dict(settings.iap.service_tenants)

    def resolve(self, ctx: RequestContext) -> Principal:
        assertion = ctx.header(_ASSERTION_HEADER)
        if not assertion:
            raise IdentityError("missing IAP assertion header; request did not pass through IAP")
        if not self._audience:
            raise IdentityError("KB_IAP_AUDIENCE is not configured; cannot verify IAP assertion")
        # The algorithm is judged before the verifier is handed the token: no cryptography, no
        # cloud SDK, so the refusal is exercised by the offline gate rather than living inside a
        # library the gate never installs.
        self._refuse_unpinned_algorithm(assertion)
        claims = self._verify(assertion)
        # The claim SET is stated here. verify_token does not require that an assertion identify
        # anybody, and a claim that is present but EMPTY counts as missing.
        self._refuse_unpinned_claims(claims)
        email = str(claims["email"]).strip()
        sub = str(claims["sub"]).strip()
        # The immutable audit actor is IAP's opaque stable subject, not the user's email.
        # Email remains useful only as a server-verified directory lookup principal and
        # tenant fallback; it is never copied into Cloud Logging labels.
        subject = sub
        # Tenant partition: service identities require an exact reviewed mapping. Human users
        # prefer the hosted-domain (`hd`) claim, then email domain, so every secure principal has
        # tenant. An empty tenant disables the domain's tenant partition (`filter_by_tenant`),
        # which is intended only for trusted local tooling : a real IAP user whose token lacks
        # `hd` (personal or federated identities) must never resolve to "see every tenant".
        tenant = str(claims.get("hd") or "").strip()
        service_email = email.lower()
        if service_email.endswith(".iam.gserviceaccount.com"):
            tenant = self._service_tenants.get(service_email, "")
            if not tenant:
                raise IdentityError(
                    "verified IAP service account has no reviewed service-email-to-tenant mapping"
                )
        if not tenant:
            _, _, email_domain = email.partition("@")
            tenant = email_domain or subject
        # Entitlement principals are derived server-side. IAP supplies the verified subject, so
        # the directory synchronizer denormalizes its effective group entitlements into this
        # user binding. A future identity adapter may also supply server-verified group ids.
        principals: tuple[str, ...] = (f"user:{email or subject}",)
        return Principal(
            subject=subject,
            principals=principals,
            tenant=tenant,
            assurance="iap",
            source="gcp-iap",
        )

    def _refuse_unpinned_algorithm(self, assertion: str) -> None:
        """Refuse an assertion signed with an algorithm this deployment does not accept.

        The kit raises its own ``IdentityError``, which is not this repository's, so it is
        re-raised as the local one: otherwise the refusal escapes the 401 mapping and the caller
        gets a bare 500.
        """
        try:
            require_pinned_algorithm(assertion)
        except AssertionRefused as exc:
            raise IdentityError(str(exc)) from exc

    def _refuse_unpinned_claims(self, claims: dict[str, Any]) -> None:
        """Refuse a verified assertion missing a required claim or naming the wrong party."""
        try:
            require_claims(
                claims,
                issuer=_IAP_ISSUER,
                audience=self._audience,
                required=_REQUIRED_CLAIMS,
            )
        except AssertionRefused as exc:
            raise IdentityError(str(exc)) from exc

    def _verify(self, assertion: str) -> dict[str, Any]:
        # Lazy import keeps the SDK-free profiles import-clean (mirrors the other gcp adapters).
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        try:
            claims: dict[str, Any] = id_token.verify_token(
                assertion,
                google_requests.Request(),
                audience=self._audience,
                certs_url=_IAP_KEYS_URL,
            )
        except Exception as exc:  # noqa: BLE001 - any verification failure must become a 401
            raise IdentityError(f"IAP assertion verification failed: {exc}") from exc
        if claims.get("iss") != _IAP_ISSUER:
            raise IdentityError("IAP assertion has an invalid or missing issuer")
        return claims
