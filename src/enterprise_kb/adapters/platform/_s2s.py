"""Service-to-service (S2S) transport hardening shared by the platform adapters.

The ``platform`` profile's adapters are thin HTTP clients to the sibling
horizontal-platform services. Two controls apply to every call:

* **Transport**: base URLs must be ``https://`` except for loopback development hosts
  (``localhost`` / ``127.0.0.1`` / ``::1``). A plaintext URL to a real host is a
  configuration error caught at adapter construction, not a silent downgrade.
* **Service identity**: when ``S2S_TOKEN`` is set, every request carries it as an
  ``Authorization: Bearer`` header (a Cloud Run ID token, an OIDC service-account JWT,
  or an API gateway key, per deployment). When ``S2S_SIGNING_KEY`` is set, a verified
  end-user actor can be propagated as an HMAC-signed ``X-Kb-Actor`` / ``X-Kb-Actor-Sig``
  header pair so the receiving service can authenticate the asserted user context. Both
  names resolve in THREE states, in the commons: UNSET is the offline zero-secret posture,
  SET-AND-EMPTY raises ``ConfiguredEmptyError`` instead of silently sending the call
  unauthenticated, and SET-AND-VALID is used. Blank a credential and the call refuses; to
  turn the feature off, UNSET the variable.

**Sourced from the shared ``hex-service-kit`` commons.** This module
passes this repo's env-var and header names to :mod:`hex_service_kit.s2s`, so a fix to
the S2S transport rule is a version bump of the package rather than an N-repo edit.
"""

from __future__ import annotations

from hex_service_kit.s2s import client_headers, validate_base_url

#: Env var holding the bearer credential for S2S calls. UNSET attaches no header (the offline
#: zero-secret posture); SET-AND-EMPTY raises rather than being read as unset.
TOKEN_ENV = "S2S_TOKEN"
#: Env var holding the HMAC key for signing the propagated end-user actor. Same three states.
SIGNING_KEY_ENV = "S2S_SIGNING_KEY"
#: This repo's header names for the signed-actor pair (kept stable for the receiving side).
_ACTOR_HEADER = "X-Kb-Actor"
_ACTOR_SIG_HEADER = "X-Kb-Actor-Sig"

# ``validate_base_url`` is re-exported verbatim from the commons; callers import it from
# this module.
__all__ = ["SIGNING_KEY_ENV", "TOKEN_ENV", "headers", "validate_base_url"]


def headers(actor: str = "") -> dict[str, str]:
    """Auth headers for one S2S request (bearer token + optional signed actor)."""
    return client_headers(
        actor,
        token_env=TOKEN_ENV,
        signing_key_env=SIGNING_KEY_ENV,
        actor_header=_ACTOR_HEADER,
        actor_sig_header=_ACTOR_SIG_HEADER,
    )
