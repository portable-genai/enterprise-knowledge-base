"""Security-header baseline on every surface (C6).

The API and the UI are two separate surfaces and a header set on one proves nothing about
the other, so both are asserted: the API by driving a real response through the app, the
UI by reading the header block its framework config emits (there is no Node runtime in
this suite, so the config is parsed as text rather than executed).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

_UI_CONFIG = Path(__file__).parents[2] / "ui" / "next.config.mjs"
# The policy moved out of the static `headers()` table into its own module: it now carries a
# per-request script nonce, and a static header table cannot express one.
_UI_CSP = Path(__file__).parents[2] / "ui" / "lib" / "csp.mjs"
_UI_PROXY = Path(__file__).parents[2] / "ui" / "proxy.ts"
_UI_LAYOUT = Path(__file__).parents[2] / "ui" / "app" / "layout.tsx"


@pytest.fixture
def client() -> TestClient:
    """The real app. /healthz needs no container, so no wiring is required here."""
    from enterprise_kb.api import app as app_module

    return TestClient(app_module.app, client=LOOPBACK_PEER)


def _headers(client: TestClient) -> dict[str, str]:
    response = client.get("/healthz")
    assert response.status_code == 200
    return {k.lower(): v for k, v in response.headers.items()}


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
def test_api_sets_the_full_header_baseline(client: TestClient):
    """RED before C6: only CSP frame-ancestors and X-Frame-Options were emitted."""
    headers = _headers(client)
    assert "frame-ancestors" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_api_omits_hsts_under_the_local_profile():
    """HSTS on a plain-http dev origin would pin the browser to a scheme we do not serve.

    This test used to take the shared ``client`` fixture, which builds the app with KB_PROFILE
    UNSET. Unset is not a local profile: it is "nobody chose", which the app deliberately treats
    as the hardened posture and therefore sends HSTS on. So the assertion was checking the
    unconfigured path while its name and docstring described the local one, and it failed for the
    documented behaviour rather than for a defect. The profile now has to be CHOSEN here, which
    is what the name always claimed.
    """
    from fastapi import FastAPI
    from hex_service_kit.web import add_security_headers

    app = FastAPI()
    app.get("/healthz")(lambda: {"ok": True})
    add_security_headers(app, frame_ancestors="'self'", profile="local")
    with TestClient(app, client=LOOPBACK_PEER) as local_client:
        headers = {k.lower(): v for k, v in local_client.get("/healthz").headers.items()}
    assert "strict-transport-security" not in headers


def test_api_sends_hsts_on_a_secure_profile(monkeypatch: pytest.MonkeyPatch):
    """TLS terminates in front of the service on every non-local profile."""
    from fastapi import FastAPI
    from hex_service_kit.web import add_security_headers

    app = FastAPI()
    app.get("/healthz")(lambda: {"ok": True})
    add_security_headers(app, frame_ancestors="'self'", profile="gcp")
    with TestClient(app, client=LOOPBACK_PEER) as secure_client:
        headers = {k.lower(): v for k, v in secure_client.get("/healthz").headers.items()}
    assert headers["strict-transport-security"].startswith("max-age=")


# --------------------------------------------------------------------------- #
# Three-state KB_FRAME_ANCESTORS
# --------------------------------------------------------------------------- #
def test_frame_ancestors_resolves_three_states_not_two():
    """Unset keeps the default; a set-and-empty value is never resolved to that default."""
    from enterprise_kb.api.app import _FRAME_ANCESTORS_ENV, _frame_ancestors

    assert _frame_ancestors(None) == "'self'"
    assert _frame_ancestors("https://portal.example") == "https://portal.example"
    assert _frame_ancestors(" https://portal.example  https://admin.example ") == (
        "https://portal.example https://admin.example"
    )
    with pytest.raises(ValueError, match=_FRAME_ANCESTORS_ENV):
        _frame_ancestors("")
    with pytest.raises(ValueError, match=_FRAME_ANCESTORS_ENV):
        _frame_ancestors("   ")


def test_an_emptied_frame_ancestors_refuses_to_boot_rather_than_dropping_the_control():
    """RED before: the service booted and every response carried an EMPTY CSP directive.

    ``Content-Security-Policy: frame-ancestors `` is a parse error browsers discard, and the
    ``'self'`` branch that adds ``X-Frame-Options`` was skipped too, so the clickjacking
    restriction was gone from both channels with nothing in the response saying so. Boot now
    fails instead, because uvicorn imports this module at start-up.
    """
    env = dict(os.environ)
    env["KB_FRAME_ANCESTORS"] = ""
    env["KB_PROFILE"] = "local"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parents[2] / "src"), env.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "-c", "import enterprise_kb.api.app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "an emptied frame-ancestors allowlist must refuse to boot"
    assert "KB_FRAME_ANCESTORS" in result.stderr
    assert "'none'" in result.stderr, "the refusal must name the way to express a lockdown"


def test_a_total_lockdown_stays_expressible():
    """Refusing on empty must not remove the operator's ability to forbid all framing."""
    from fastapi import FastAPI
    from hex_service_kit.web import add_security_headers

    from enterprise_kb.api.app import _frame_ancestors

    locked = FastAPI()
    locked.get("/healthz")(lambda: {"ok": True})
    add_security_headers(locked, frame_ancestors=_frame_ancestors("'none'"), profile="gcp")
    with TestClient(locked, client=LOOPBACK_PEER) as locked_client:
        headers = {k.lower(): v for k, v in locked_client.get("/healthz").headers.items()}
    assert headers["content-security-policy"] == "frame-ancestors 'none'"


# --------------------------------------------------------------------------- #
# UI surface
# --------------------------------------------------------------------------- #
def _ui_csp() -> str:
    text = _UI_CSP.read_text(encoding="utf-8")
    block = re.search(r"return \[(.*?)\]\.join", text, re.S)
    assert block, "the UI policy module must build an explicit CSP list"
    return block.group(1)


def test_ui_emits_a_full_csp_not_only_frame_ancestors():
    """RED before C6: the UI CSP was `frame-ancestors <x>` and nothing else."""
    csp = _ui_csp()
    for directive in (
        "default-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors",
        "connect-src",
    ):
        assert directive in csp, f"the UI CSP omits {directive}"


def test_ui_connect_src_is_never_a_wildcard():
    text = _UI_CSP.read_text(encoding="utf-8")
    assert "connect-src *" not in text
    assert "'self'" in text
    # the cross-origin case is an explicit single origin from configuration
    api_base = (_UI_CSP.parents[2] / "ui" / "lib" / "api-base.mjs").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_KB_API_URL" in api_base
    assert "new URL(base).origin" in api_base


def test_ui_sets_nosniff_and_referrer_policy():
    text = _UI_CONFIG.read_text(encoding="utf-8")
    assert '"X-Content-Type-Options", value: "nosniff"' in text
    assert '"Referrer-Policy", value: "no-referrer"' in text


def test_ui_script_src_has_no_unsafe_inline():
    text = _UI_CSP.read_text(encoding="utf-8")
    script = next(line for line in text.splitlines() if "script-src 'self'" in line)
    assert "unsafe-inline" not in script


def test_ui_script_src_is_nonced_and_the_route_is_dynamic():
    """RED before this fix: the console served dead markup.

    `script-src 'self'` blocked Next's inline hydration bootstrap, so React never attached and
    no control did anything, while every header, type-check and test stayed green. The nonce
    fixes it only if the route is dynamically rendered: a prerendered page was built before the
    nonce existed, and `'strict-dynamic'` then also blocks the chunk scripts that plain `'self'`
    had been loading, which is strictly worse than the defect.
    """
    policy = _UI_CSP.read_text(encoding="utf-8")
    assert "'nonce-${nonce}' 'strict-dynamic'" in policy
    # The nonce reaches Next only through the REQUEST header, under this exact name.
    assert 'requestHeaders.set("Content-Security-Policy", csp)' in _UI_PROXY.read_text(
        encoding="utf-8"
    )
    # And the build refuses the half-configured combination outright.
    assert "assertHydratableCsp" in _UI_CONFIG.read_text(encoding="utf-8")
    assert 'export const dynamic = "force-dynamic"' in _UI_LAYOUT.read_text(encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
