"""FastAPI security dependencies for exact browser and service caller modes.

This module resolves **two orthogonal identities** for a request, each with its own
header and its own dependency:

* ``get_principal`` / :data:`CurrentPrincipal` authenticate the **end user**. It builds a
  :class:`RequestContext` from the inbound headers and asks the active profile's
  :class:`IdentityPort` adapter to resolve a verified :class:`Principal` from the
  IAP-signed ``x-goog-iap-jwt-assertion`` (secure) or the ``X-Dev-Persona`` header
  (local). The request-body ``actor``/ACL are ignored entirely: the audit actor and the
  entitlement principals fed into ACL-aware retrieval flow from here, closing the
  spoofable-identity gap. A failure to resolve a verified principal is a 401.

* A browser behind IAP carries the verified IAP assertion and no redundant application
  bearer. A service caller sends its IAP credential in ``Proxy-Authorization`` (consumed by
  IAP) and its application OIDC token in ``Authorization``. The assertion then identifies
  the verified service account; a reviewed mapping supplies its tenant.
  The shared S2S contract is:

  - ``local`` profile, chosen DELIBERATELY: a static shared secret from ``KB_S2S_TOKEN``,
    compared in constant time. When the env var is UNSET the route stays open (loopback dev
    only), so the offline test gate runs with zero secrets; when SET, a request without the
    matching token is 401.
  - ``KB_PROFILE`` unset: no profile was chosen, so no scheme was chosen, and every S2S
    route answers 401. An absent variable is not consent to the zero-secret opening above.
  - ``gcp``/``platform`` (secure) profile: the bearer is a Google-signed OIDC ID token;
    its signature, issuer, expiry and audience (``KB_S2S_AUDIENCE``) are verified, then
    the caller service account is authorized against the ``KB_S2S_ALLOWED_CALLERS``
    allowlist (403 if not listed). The google verification libs are lazy-imported so the
    SDK-free local/onprem profiles import this module with no GCP SDK installed.

The IdentityPort is the application verification behind edge IAP. S2S adds an application-token
check; browser mode does not pretend to have that second credential. ``/healthz`` is intentionally
unauthenticated.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from hex_service_kit.web import make_require_service_caller

from ..domain.identity import IdentityError, Principal, RequestContext
from . import deps

# S2S env-var NAMES (not secret values). The KB_ prefix mirrors KB_PROFILE / KB_SETTINGS.
_TOKEN_ENV = "KB_S2S_TOKEN"  # noqa: S105 - env var NAME, not a secret value
_ALLOWED_CALLERS_ENV = "KB_S2S_ALLOWED_CALLERS"
_AUDIENCE_ENV = "KB_S2S_AUDIENCE"


def get_principal(request: Request) -> Principal:
    """Resolve the verified end-user principal for this request, or raise 401.

    Building the adapter is inside the try on purpose: the local persona adapter REFUSES to
    construct when ``KB_PROFILE`` was never set, and that refusal is an authentication failure
    (nobody consented to seeded personas), not a server fault.
    """
    ctx = RequestContext(headers={k.lower(): v for k, v in request.headers.items()})
    try:
        return deps.get_container().identity.resolve(ctx)
    except IdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        ) from exc


# Reusable typed dependency for route signatures.
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


# --------------------------------------------------------------------------- #
# Service-to-service (S2S) auth : authenticate the *calling service*, beside the
# end-user Principal above. Reads the Authorization bearer, never an identity header.
# Sourced from the shared hex-service-kit commons: the verification
# logic (constant-time shared secret under local, Google-signed OIDC ID token +
# caller allowlist under gcp/platform) now delegates to
# make_require_service_caller with this repo's env-var names, so behaviour and
# this module's public surface are unchanged.
# --------------------------------------------------------------------------- #
def _exposure_profile(request: Request) -> str:
    """The RELAXATION view of the profile: an unset KB_PROFILE is never read as ``local``.

    The commons dependency opens the shared-secret path only on an EXACT ``local`` match, so
    handing it the unconsented sentinel is what stops an absent variable from selecting the
    zero-secret opening.
    """
    return str(deps.get_settings().choice.exposure_profile)


_authenticate_service_caller = make_require_service_caller(
    _exposure_profile,
    token_env=_TOKEN_ENV,
    allowed_callers_env=_ALLOWED_CALLERS_ENV,
    audience_env=_AUDIENCE_ENV,
)


def require_service_caller(request: Request) -> None:
    """Authenticate the calling SERVICE, refusing to decide at all without a chosen profile.

    The commons dependency picks its scheme from the profile string: a Google-signed OIDC ID
    token under a secure profile, the shared-secret bearer otherwise. The shared-secret path
    stays OPEN when ``KB_S2S_TOKEN`` is unset (loopback dev with zero secrets), so an UNSET
    ``KB_PROFILE`` must never be allowed to select it: that combination let an unauthenticated
    caller reach the governed data plane. When no profile was chosen, no scheme was chosen
    either, and the answer is 401.

    Known limit, stated rather than papered over: with ``KB_PROFILE=local`` chosen DELIBERATELY
    and ``KB_S2S_TOKEN`` unset, these endpoints remain unauthenticated. That is the offline
    demo posture, and it is bounded by EXPOSURE (the loopback bind in ``api.app.main``) rather
    than by this dependency. Set ``KB_S2S_TOKEN`` for any local run reachable by anything other
    than the operator's own machine.
    """
    choice = deps.get_settings().choice
    authorization = request.headers.get("authorization", "").strip()
    iap_assertion = request.headers.get("x-goog-iap-jwt-assertion", "").strip()

    # The exact IAP-fronted browser path carries a verified end-user assertion but cannot mint
    # a service-account OIDC bearer. Governed routes also require CurrentPrincipal, which verifies
    # this assertion's signature/issuer/audience before domain work. If Authorization IS present,
    # it is always verified as S2S; this branch cannot downgrade a bad bearer into browser auth.
    if choice.profile == "gcp" and not authorization and iap_assertion:
        return

    if not choice.service_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "service-to-service authentication is unconfigured: KB_PROFILE is not set, "
                "so no authentication scheme has been chosen"
            ),
        )
    _authenticate_service_caller(request)


# Reusable dependency for route decorators (returns None; used for its side effect).
ServiceCaller = Depends(require_service_caller)
