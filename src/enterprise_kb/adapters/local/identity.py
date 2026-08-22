"""Local IdentityPort adapter: seeded dev personas, NO IdP / AD / LDAP.

The SDK-free ``local`` profile must run with zero authentication so demos and tests work
fully offline. This adapter resolves a :class:`Principal` from a small set of seeded
personas, selected by the ``X-Dev-Persona`` request header (the UI's persona picker),
defaulting to the first persona when none is supplied. It lets you exercise per-user
authorization (different entitlement principals and tenants, including a cross-tenant
persona) without standing up any identity provider: each persona's ``principals`` are the
group ids the local access-control directory resolves to ACL tags, so switching persona
changes which corpus passages the same query admits. It is bound ONLY under the local
profile; secure mode uses the IAP adapter, which verifies a real assertion.

The personas are an UNAUTHENTICATED grant of read access to the governed corpus, so this
adapter refuses to construct unless the local profile was chosen DELIBERATELY: the profile must
actually be ``local`` and (when the settings came from the environment) ``KB_PROFILE`` must have
been set rather than inherited from the fallback. A missing env var therefore fails closed
instead of resolving every caller to a seeded reader of the bank corpus.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.identity import IdentityError, Principal, RequestContext
from ...ports.identity import CLIENT_ASSERTED

_PERSONA_HEADER = "x-dev-persona"

# Seeded dev personas. Ordered; the first entry is the default when no persona is selected.
# The persona id is the suffix of ``source`` after the colon. The entitlement group ids
# mirror the local access-control directory (adapters/local/_seed.PRINCIPAL_TAGS) so a
# persona's principals resolve to real ACL tags and the demo shows per-user access.
_PERSONAS: tuple[Principal, ...] = (
    Principal(
        subject="demo.analyst@bank.example",
        principals=("group:kb-reader", "group:risk"),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:analyst",
    ),
    Principal(
        subject="demo.approver@bank.example",
        principals=("group:kb-reader", "group:risk", "group:kb-approver"),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:approver",
    ),
    Principal(
        subject="demo.auditor@bank.example",
        principals=("group:audit",),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:auditor",
    ),
    Principal(
        subject="user@other-tenant.example",
        principals=("group:kb-reader",),
        tenant="other-bank",
        assurance="local-demo",
        source="local-persona:other-tenant",
    ),
)


def _persona_id(principal: Principal) -> str:
    _, _, suffix = principal.source.partition(":")
    return suffix or principal.subject


class LocalPersonaProfileError(IdentityError):
    """Raised when seeded dev personas would be served under a non-deliberate local profile."""


class LocalPersonaIdentityAdapter:
    """Resolve a Principal from a seeded dev persona (local profile only, no auth)."""

    #: The persona arrives on a header the CALLER wrote, and an absent header still resolves
    #: the default persona, so this authenticates nobody. Read by the app-object exposure
    #: guard, which therefore confines this profile to a loopback peer.
    end_user_auth = CLIENT_ASSERTED

    def __init__(self, settings: Settings) -> None:
        if settings.profile != "local":
            raise LocalPersonaProfileError(
                "seeded dev personas are local-profile only; "
                f"refusing to serve them under profile {settings.profile!r}"
            )
        if not settings.profile_explicit:
            raise LocalPersonaProfileError(
                "KB_PROFILE is not set, so the local profile was inherited rather than chosen; "
                "the seeded dev personas grant read access to the governed corpus with no "
                "authentication and are refused. Set KB_PROFILE=local deliberately for a dev "
                "or demo run, or KB_PROFILE=gcp for a real deployment."
            )
        self._settings = settings
        self._by_id: dict[str, Principal] = {_persona_id(p): p for p in _PERSONAS}
        self._default: Principal = _PERSONAS[0]

    def resolve(self, ctx: RequestContext) -> Principal:
        chosen = ctx.header(_PERSONA_HEADER).strip()
        if not chosen:
            return self._default
        persona = self._by_id.get(chosen.lower())
        if persona is None:
            raise IdentityError(
                f"unknown dev persona {chosen!r}; valid personas: {sorted(self._by_id)}"
            )
        return persona

    def personas(self) -> tuple[dict[str, str], ...]:
        """List the seeded personas for the local persona picker (id, subject, tenant)."""
        return tuple(
            {
                "id": _persona_id(p),
                "subject": p.subject,
                "tenant": p.tenant,
                "principals": ", ".join(p.principals),
            }
            for p in _PERSONAS
        )
