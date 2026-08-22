"""On-prem IdentityPort placeholder : integrate the client's own enterprise IdP here.

In the managed profile this port binds to the IAP-verified-assertion adapter; switching
``profile`` to ``onprem`` rebinds it here. Fill this in to verify your enterprise IdP's
token/assertion (OIDC/SAML) and map the verified claims to a :class:`Principal`. Like the
other on-prem placeholders it constructs with no external dependency and fails fast rather
than trusting an unauthenticated caller: an unverified identity must never be accepted
(this is the seam that defeats actor spoofing and the confused-deputy risk).
"""

from __future__ import annotations

from ...config import Settings
from ...domain.identity import Principal, RequestContext
from ...ports.identity import UNIMPLEMENTED

_MESSAGE = (
    "On-prem IdentityPort adapter is a migration placeholder; implement verification "
    "against your on-premise enterprise IdP (OIDC/SAML) and map the verified claims to a "
    "Principal. Core domain logic is unchanged."
)


class OnPremIdentityAdapter:
    """Placeholder identity adapter for the on-prem profile (fail-fast)."""

    #: Resolves nobody until an adopter binds their own IdP verifier, so this deployment can
    #: authenticate no end user at all. Read by the app-object exposure guard, which confines
    #: the placeholder to loopback; rebinding this entry to a verifying adapter lifts that
    #: bound with no change to the guard.
    end_user_auth = UNIMPLEMENTED

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(self, ctx: RequestContext) -> Principal:
        raise NotImplementedError(_MESSAGE)
