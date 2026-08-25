"""IdentityPort : resolve a verified Principal from inbound transport context.

The hexagon boundary for authentication. The API layer hands the adapter a
:class:`RequestContext` (the request headers) and gets back a verified
:class:`Principal`, or an :class:`IdentityError`. The active profile picks the adapter:

* ``local`` resolves a seeded dev persona (no IdP/AD/LDAP) so demos and tests run offline,
* ``gcp`` verifies the Identity-Aware-Proxy-injected signed assertion (auth configured on
  the GCP service), and
* ``onprem`` is the placeholder for the client's own enterprise IdP (OIDC/SAML).

This keeps the per-user identity decision swappable by configuration, exactly like every
other port (P-02), and is the single seam where the client-asserted actor/ACL is replaced
by a server-verified one that scopes the ACL-aware retrieval.

This module ALSO carries what an identity adapter DECLARES about the end-user authentication
it can provide, because the exposure guard on the app object has one question to answer before
it can decide anything: are this service's END-USER routes authenticated? Nothing else in the
configuration answers it, and two things that look like they do, do not:

* The PROFILE names an adapter family, not an authentication scheme. A deliberate ``local`` and
  an inherited one bind the same seeded personas, and a client's own IdP adapter can be bound
  under ``onprem`` without the profile string changing at all.
* The SERVICE-TO-SERVICE secret authenticates a calling SERVICE. It authenticates no end user,
  so its presence is not evidence that the ACL-scoped routes are protected. Deriving the guard
  from it is how setting a credential comes to SWITCH THE GUARD OFF.

The adapter bound to the identity port is the only thing that knows, so it says so here, and
the guard reads the answer from the binding rather than inferring it from something else.

Three answers, and the difference between the first two is the whole point:

* :data:`VERIFIED` - the adapter resolves a principal from something it VERIFIES server side (a
  signed assertion whose signature, issuer, expiry and audience it checks). A caller cannot name
  itself, so end-user routes ARE authenticated.
* :data:`CLIENT_ASSERTED` - the adapter resolves a principal from something the CLIENT wrote
  (the seeded ``X-Dev-Persona`` picker, which also has a DEFAULT persona, so a caller that sends
  no header at all is still somebody, and that somebody carries entitlement principals which
  scope the ACL-aware retrieval). A caller chooses who it is, so end-user routes are NOT
  authenticated, however many other credentials the deployment has configured.
* :data:`UNIMPLEMENTED` - the adapter resolves nobody at all: a portability placeholder waiting
  for the client's own IdP. Nobody can be authenticated, so nothing is.

An adapter that declares NOTHING is read as :data:`CLIENT_ASSERTED`, never :data:`VERIFIED`.
Silence is not a claim to verify anything, and a guard that reads silence as "authenticated"
switches itself off for every adapter somebody forgot to annotate, which is the fail-open shape
these declarations exist to remove.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.identity import IdentityError, Principal, RequestContext

#: The adapter verifies a server-side assertion; the client cannot assert who it is.
VERIFIED = "verified"
#: The adapter believes a header the client wrote. Useful offline, not authentication.
CLIENT_ASSERTED = "client-asserted"
#: The adapter resolves nobody: a placeholder for an identity provider not yet bound.
UNIMPLEMENTED = "unimplemented"

#: Every declaration this service understands. Anything else is read as :data:`CLIENT_ASSERTED`.
END_USER_AUTH_KINDS: frozenset[str] = frozenset({VERIFIED, CLIENT_ASSERTED, UNIMPLEMENTED})

#: The class attribute an identity adapter sets to one of the values above. A CLASS attribute,
#: not an instance one, because the posture has to be readable WITHOUT constructing the adapter:
#: the seeded-persona adapter refuses to construct under an inherited profile, and a posture
#: that can only be computed by constructing something disappears exactly when it matters most.
END_USER_AUTH_ATTR = "end_user_auth"


def declared_end_user_auth(adapter: object) -> str:
    """What ``adapter`` (a class or an instance) declares, defaulting to :data:`CLIENT_ASSERTED`.

    The default is the fail-closed one: it withholds the "authenticated" verdict the exposure
    guard would relax on, and claims nothing about an adapter that never spoke. An unrecognised
    value lands in the same place, so a typo in a declaration cannot read as a verification
    claim.
    """
    declared = getattr(adapter, END_USER_AUTH_ATTR, None)
    if isinstance(declared, str) and declared in END_USER_AUTH_KINDS:
        return declared
    return CLIENT_ASSERTED


@runtime_checkable
class IdentityPort(Protocol):
    def resolve(self, ctx: RequestContext) -> Principal:
        """Resolve a VERIFIED principal from ``ctx``; raise ``IdentityError`` if missing/invalid."""
        ...


class EndUserAuthUnavailableError(IdentityError):
    """This deployment can authenticate NO end user at all, and the message says why.

    A plain :class:`IdentityError` means THIS caller did not authenticate; another one might.
    This means the bound adapter cannot authenticate anybody: an audience nobody configured, or
    a verifier that is not installed.

    The distinction is worth a type because the two need different answers. ``get_principal``
    maps every ``IdentityError`` to a bare 401 carrying "authentication required", which sends
    an operator hunting for a missing credential when the truth is in the configuration and no
    credential would have helped. Subclasses carry their own :attr:`http_status` and their own
    message, and ``api/security.py`` answers with both.
    """

    #: The status the API answers with. 401 is right where a caller could have authenticated
    #: and did not; a subclass meaning "nobody can, here" says so with a different code.
    http_status: int = 401
