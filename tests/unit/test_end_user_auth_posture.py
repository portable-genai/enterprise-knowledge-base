"""The exposure guard rides the APP OBJECT, and is derived from the IDENTITY BINDING.

The defect this file is the standing guard for: the only bound on serving the no-auth `local`
posture to the network lived in `resolve_bind_host(...)`, inside `main()`. The shipped entry
point never reaches `main()` -- the Dockerfile CMD is

    python -m enterprise_kb.managed_preflight && exec uvicorn enterprise_kb.api.app:app \
        --host 0.0.0.0 --port ${PORT}

so the bound was a property of one entry point rather than of the application. Executed against
this repo with `KB_PROFILE=local`, a peer at 203.0.113.7 carrying no credential read the
whole seeded-persona roster, entitlement principals included, off `GET /v1/personas`.

The fix puts the guard on the `app` object, registered LAST so it is the OUTERMOST middleware:
an off-loopback caller is refused before CORS, before the header baseline and before any route
or dependency runs. This file asserts BOTH directions, because a guard that refuses everybody is
not a fix:

* a LAN peer is refused with 503 on every route, including the ones that carry no credential
  by design (`/healthz`);
* a loopback peer is still served 200, so the offline demo and the whole local workflow work.

The posture itself is derived from the identity BINDING, never from a credential. Whether
`KB_S2S_TOKEN` happens to be set is evidence about a calling SERVICE and says nothing
about the end-user routes, so a scanner here fails the build if the guard's argument reaches a
credential at any depth.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from enterprise_kb.adapters.gcp.iap_identity import IapIdentityAdapter
from enterprise_kb.adapters.local.identity import LocalPersonaIdentityAdapter
from enterprise_kb.adapters.onprem.identity import OnPremIdentityAdapter
from enterprise_kb.api.app import app
from enterprise_kb.config import (
    RUNTIME_PROFILES,
    Settings,
    end_user_auth_kind,
    identity_adapter_class,
)
from enterprise_kb.ports.identity import (
    CLIENT_ASSERTED,
    END_USER_AUTH_ATTR,
    END_USER_AUTH_KINDS,
    UNIMPLEMENTED,
    VERIFIED,
    declared_end_user_auth,
)

#: A peer somewhere else on the LAN: exactly the address the leak was executed from.
LAN_PEER = ("203.0.113.7", 51234)


def _settings_for(profile: str, identity: dict[str, str] | None = None) -> Settings:
    """The shipped settings, re-pointed at ``profile`` (and optionally at a REBOUND adapter).

    ``replace`` rather than mutation: ``Settings`` is frozen and its ``adapters`` map is shared
    with the process-wide container, so writing into it would leak a test's rebinding into every
    later test in the session.
    """
    loaded = Settings.load()
    adapters = {port: dict(bindings) for port, bindings in loaded.adapters.items()}
    if identity is not None:
        adapters["identity"] = dict(identity)
    return replace(loaded, profile=profile, adapters=adapters)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_MODULE = _REPO_ROOT / "src" / "enterprise_kb" / "api" / "app.py"

#: The guard call whose argument must never be derived from a credential.
_GUARD_CALL = "add_loopback_exposure_guard"

#: Anything naming a SERVICE credential. The guard bounds the whole app, including routes that
#: carry no credential at all, so none of these may appear anywhere in the expression that
#: decides whether it is on, at any depth.
_CREDENTIAL_MARKERS: tuple[str, ...] = ("S2S", "TOKEN", "SECRET", "BEARER")


# --------------------------------------------------------------------------- #
# 1. The guard is ON the app object: a LAN peer is refused, on every route.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/v1/personas", "/healthz"])
def test_a_lan_peer_is_refused_by_the_app_object(path: str) -> None:
    """The exact leak, as a test: no `main()` involved, just the app uvicorn is handed."""
    response = TestClient(app, client=LAN_PEER).get(path)
    assert response.status_code == 503, (
        f"{path} was served to a non-loopback peer under the no-auth local profile. The bound "
        "in main() does not apply: the Dockerfile CMD hands this app object straight to uvicorn "
        "with --host 0.0.0.0."
    )
    detail = response.json()["detail"]
    assert "203.0.113.7" in detail, "the refusal must name the peer it refused"
    assert "KB_ALLOW_INSECURE_DEMO" in detail, "the refusal must name the opt-out"


def test_the_refusal_does_not_leak_the_persona_roster() -> None:
    """A 503 whose body still carried the data would be no fix at all."""
    body = TestClient(app, client=LAN_PEER).get("/v1/personas").text
    assert "demo.analyst@bank.example" not in body


# --------------------------------------------------------------------------- #
# 2. The other direction: loopback still works. A guard that refuses everybody
#    is a broken service, not a secure one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/v1/personas", "/healthz"])
def test_a_loopback_peer_is_still_served(path: str) -> None:
    response = TestClient(app, client=LOOPBACK_PEER).get(path)
    assert response.status_code == 200, (
        f"{path} must still answer a loopback peer: the offline demo, the local UI and this "
        "suite all reach the API that way."
    )


def test_the_roster_reaches_loopback_and_not_the_lan() -> None:
    """The whole point, in one assertion pair: same route, two peers, two answers."""
    served = TestClient(app, client=LOOPBACK_PEER).get("/v1/personas")
    refused = TestClient(app, client=LAN_PEER).get("/v1/personas")
    assert served.status_code == 200
    assert refused.status_code == 503
    assert refused.text != served.text
    if Settings.load().profile_explicit:
        # Under a DELIBERATE local profile (what `make test` and the demo run, and what the
        # leak was executed under) the seeded adapter constructs and the roster is real. Under
        # an INHERITED one it refuses to construct and the route answers an empty list, which
        # is a different control and is asserted elsewhere; either way the LAN peer gets 503.
        assert [p["id"] for p in served.json()] == [
            "analyst",
            "approver",
            "auditor",
            "other-tenant",
        ]


def test_the_insecure_demo_opt_in_lifts_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's explicit, per-request-read consent. The SAME variable the bind guard uses.

    Read per request rather than at import, and it must be exactly "1": the relaxation fails
    closed in the opposite direction from the restriction.
    """
    monkeypatch.setenv("KB_ALLOW_INSECURE_DEMO", "1")
    assert TestClient(app, client=LAN_PEER).get("/healthz").status_code == 200
    monkeypatch.setenv("KB_ALLOW_INSECURE_DEMO", "true")
    assert TestClient(app, client=LAN_PEER).get("/healthz").status_code == 503


def test_a_forwarding_header_is_disqualifying_even_from_loopback() -> None:
    """A proxy has already overwritten the scope peer, so the header's PRESENCE is the signal."""
    response = TestClient(app, client=LOOPBACK_PEER).get(
        "/healthz", headers={"X-Forwarded-For": "127.0.0.1"}
    )
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# 3. Every shipped adapter declares what it does, explicitly.
# --------------------------------------------------------------------------- #
def test_the_seeded_persona_adapter_declares_client_asserted() -> None:
    """The persona rides a header the caller wrote, and an absent header still resolves one."""
    assert declared_end_user_auth(LocalPersonaIdentityAdapter) == CLIENT_ASSERTED


def test_the_iap_adapter_declares_that_it_verifies() -> None:
    assert declared_end_user_auth(IapIdentityAdapter) == VERIFIED


def test_the_onprem_placeholder_declares_that_it_verifies_nothing() -> None:
    assert declared_end_user_auth(OnPremIdentityAdapter) == UNIMPLEMENTED


@pytest.mark.parametrize("profile", sorted(RUNTIME_PROFILES))
def test_every_bound_adapter_declares_explicitly(profile: str) -> None:
    """A new adapter must SAY what it does; inheriting the safe default silently is not enough."""
    adapter = identity_adapter_class(_settings_for(profile))
    declared = [klass for klass in adapter.__mro__ if END_USER_AUTH_ATTR in vars(klass)]
    assert declared, (
        f"{adapter.__name__} (the {profile} identity binding) sets no {END_USER_AUTH_ATTR}. "
        f"Declare one of {sorted(END_USER_AUTH_KINDS)} on the class: the exposure guard reads "
        "it, and silence is read as client-asserted."
    )
    assert declared_end_user_auth(adapter) in END_USER_AUTH_KINDS


class _UndeclaredAdapter:
    """An adapter that says nothing at all."""


class _MisdeclaredAdapter:
    """An adapter whose declaration is a typo, which must not read as a verification claim."""

    end_user_auth = "Verified"


@pytest.mark.parametrize("adapter", [_UndeclaredAdapter, _MisdeclaredAdapter, object()])
def test_silence_and_typos_are_read_as_client_asserted(adapter: object) -> None:
    """The fail-closed default, in the only direction that matters: never VERIFIED."""
    assert declared_end_user_auth(adapter) == CLIENT_ASSERTED


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", CLIENT_ASSERTED),
        ("gcp", VERIFIED),
        ("platform", VERIFIED),
        ("onprem", UNIMPLEMENTED),
    ],
)
def test_the_posture_follows_the_profile_binding(profile: str, expected: str) -> None:
    assert end_user_auth_kind(_settings_for(profile)) == expected


def test_an_unresolvable_binding_fails_CLOSED_rather_than_raising_past_the_guard() -> None:
    """A guard that switches off because a lookup raised is a guard that fails open."""
    broken = _settings_for("local", identity={"local": "enterprise_kb.nope:Missing"})
    assert end_user_auth_kind(broken) == CLIENT_ASSERTED


def test_the_posture_follows_a_REBOUND_adapter_not_the_profile_name() -> None:
    """The on-premises migration path: bind a real verifier and the posture changes with it.

    This is why the guard reads the BINDING rather than the profile string. An adopter who wires
    their own verifying adapter under `onprem` has an authenticated service, and a guard keyed
    off the word "onprem" would confine it to loopback forever.
    """
    rebound = _settings_for(
        "onprem",
        identity={"onprem": "enterprise_kb.adapters.gcp.iap_identity:IapIdentityAdapter"},
    )
    assert end_user_auth_kind(rebound) == VERIFIED


# --------------------------------------------------------------------------- #
# 4. The guard's argument names no credential, at any depth.
# --------------------------------------------------------------------------- #
class _StripDocstrings(ast.NodeTransformer):
    """Drop every docstring from a subtree before it is scanned.

    The scan looks for the NAME of a credential in what the guard's posture reaches, and a
    docstring is prose, not a read. Without this, a comment or docstring saying that the S2S
    token is NOT in the expression would fail the build for saying so.
    """

    def _strip(self, node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        first = body[0] if isinstance(body, list) and body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined,index]
        return self.generic_visit(node)

    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip
    visit_Module = _strip


def _module_definitions(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = <expr>`` assignments AND function bodies, as source text."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                found[target.id] = ast.unparse(node.value)
        elif isinstance(node, ast.FunctionDef):
            stripped = _StripDocstrings().visit(ast.parse(ast.unparse(node)))
            found[node.name] = ast.unparse(stripped)
    return found


def guard_posture_source(source: str) -> str:
    """Everything the exposure guard's ``unauthenticated`` argument reaches, as one blob.

    Transitive on purpose: the posture is one indirection deep (`_END_USER_AUTHENTICATED`), and
    a check that only read the call site would see nothing.
    """
    tree = ast.parse(source)
    definitions = _module_definitions(tree)
    expressions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(_GUARD_CALL):
            expressions += [
                ast.unparse(kw.value) for kw in node.keywords if kw.arg == "unauthenticated"
            ]
    assert expressions, f"no {_GUARD_CALL}(unauthenticated=...) call found"
    seen: set[str] = set()
    reached = list(expressions)
    pending = list(expressions)
    while pending:
        for name_node in ast.walk(ast.parse(pending.pop())):
            if isinstance(name_node, ast.Name) and name_node.id not in seen:
                seen.add(name_node.id)
                if name_node.id in definitions:
                    reached.append(definitions[name_node.id])
                    pending.append(definitions[name_node.id])
    return "\n".join(reached + sorted(seen))


def test_the_exposure_guard_reads_no_service_credential() -> None:
    """A credential may not decide whether the guard is on."""
    reached = guard_posture_source(_APP_MODULE.read_text(encoding="utf-8")).upper()
    offenders = [marker for marker in _CREDENTIAL_MARKERS if marker in reached]
    assert offenders == [], (
        f"the exposure guard's posture reaches {offenders}. A service credential authenticates a "
        "calling SERVICE and no end user, so it is no evidence that the end-user routes are "
        "protected. Derive the posture from the identity binding (config.end_user_auth_kind)."
    )


def test_the_exposure_guard_is_derived_from_the_identity_binding() -> None:
    """Not merely "no credential": the posture must come from the thing that actually knows."""
    reached = guard_posture_source(_APP_MODULE.read_text(encoding="utf-8"))
    assert "end_user_auth_kind" in reached, (
        "the guard no longer reads the identity binding, so nothing checks whether this "
        "deployment can authenticate anybody at all"
    )


#: The defect shape a credential-derived posture would have, one indirection deep. A scanner
#: nobody proved can find anything is a green tick over an empty set.
_MUTANT = (
    "_TOKEN_ENV = 'KB_S2S_TOKEN'\n"
    "_END_USER_AUTHENTICATED = not read_env_setting(_TOKEN_ENV).is_unset\n"
    "add_loopback_exposure_guard(\n"
    "    app,\n"
    "    unauthenticated=not _END_USER_AUTHENTICATED,\n"
    "    insecure_demo_env='KB_ALLOW_INSECURE_DEMO',\n"
    ")\n"
)


def test_the_scan_finds_the_defect_it_was_written_for() -> None:
    reached = guard_posture_source(_MUTANT).upper()
    caught = {marker for marker in _CREDENTIAL_MARKERS if marker in reached}
    assert caught == {"S2S", "TOKEN"}, (
        "the scan no longer finds the credential in the expression the defect was written as, "
        "so a green result from it means nothing"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
