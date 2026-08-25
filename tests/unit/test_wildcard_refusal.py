"""A wildcard in either origin policy refuses to boot, rather than being passed through.

Two allowlists decide who may call this knowledge base from a browser and who may frame its
console: ``KB_CORS_ORIGINS`` and ``KB_FRAME_ANCESTORS``. Both were resolved carefully in three
states and then handed on verbatim, so a wildcard travelled straight through to
``CORSMiddleware(allow_origins=[...])`` and to ``Content-Security-Policy: frame-ancestors ...``.
The prohibition existed only as a comment beside the variable ("never ``*``") and, for CORS, as
a sentence in the shared kit's own docstring. A comment is not a control.

A wildcard in either place is the whole origin policy switched off on an ACL-aware corpus: any
page on the internet could frame the console and, with ``allow_credentials=True``, read
cross-origin responses. Both values are resolved at module import, so refusing there makes this
a BOOT failure an operator sees immediately rather than a surprise on some later request.

FOUR SPELLINGS, not one. ``'*'`` is the quoted form CSP also honours; ``*.*`` is the subdomain
wildcard; and ``null`` is the origin a SANDBOXED iframe presents, so accepting it in either
policy is a real bypass rather than a typo. The same set is refused in ``ui/lib/csp.mjs``, which
emits the policy the browser actually enforces for the console DOCUMENT; ``ui/tests/csp.test.mjs``
owns that half and the drift guard at the end of this file keeps the two in step.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from enterprise_kb.api.app import (
    _CORS_ORIGINS_ENV,
    _FRAME_ANCESTORS_ENV,
    _ORIGIN_WILDCARDS,
    _cors_origins,
    _frame_ancestors,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every spelling an operator could reach, asterisk-bearing and not. Only the first four were
#: refused before: the rule was an exact-token set, which matches an entry EXACTLY, so the three
#: host-source forms were in no set and travelled through both allowlists verbatim.
_WILDCARD_SPELLINGS = ["*", "'*'", "null", "*.*", "https://*.example", "*.example", "https://*"]


def _boot(**overrides: str) -> subprocess.CompletedProcess[str]:
    """Import the API module in a fresh interpreter, the way uvicorn does at start-up."""
    env = dict(os.environ)
    env["KB_PROFILE"] = "local"
    env.pop(_CORS_ORIGINS_ENV, None)
    env.pop(_FRAME_ANCESTORS_ENV, None)
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-c", "import enterprise_kb.api.app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


# --------------------------------------------------------------------------- #
# The refusal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_frame_ancestor_is_refused(spelling: str) -> None:
    with pytest.raises(ValueError, match="wildcard"):
        _frame_ancestors(spelling)


@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_hidden_among_real_frame_ancestors_is_refused(spelling: str) -> None:
    """The dangerous shape in practice: an allowlist that looks specific and is not."""
    with pytest.raises(ValueError, match="wildcard"):
        _frame_ancestors(f"'self' https://portal.bank.example {spelling}")


@pytest.mark.parametrize("spelling", _WILDCARD_SPELLINGS)
def test_a_wildcard_cors_origin_is_refused(spelling: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_CORS_ORIGINS_ENV, f"https://portal.bank.example,{spelling}")
    with pytest.raises(ValueError, match="wildcard"):
        _cors_origins()


@pytest.mark.parametrize("variable", [_FRAME_ANCESTORS_ENV, _CORS_ORIGINS_ENV])
@pytest.mark.parametrize("spelling", ["*", "null", "https://*.example"])
def test_a_wildcard_refuses_at_boot_and_not_on_a_later_request(
    variable: str, spelling: str
) -> None:
    """uvicorn imports this module at start-up, which is where an operator can still act.

    Three spellings rather than one: the bare asterisk and the behavioural ``null`` the token
    set already caught, and the subdomain host-source form it structurally could not.
    """
    result = _boot(**{variable: spelling})
    assert result.returncode != 0, f"{variable}={spelling} must refuse to boot"
    assert variable in result.stderr
    assert "wildcard" in result.stderr


def test_the_rule_is_the_union_of_an_exact_token_set_and_an_asterisk_test() -> None:
    """Neither half is sufficient alone, which is the whole reason this file grew.

    The token half cannot see ``https://*.example``, because a set matches an entry exactly and
    nothing else; the asterisk half cannot see ``null``, which carries none. The console
    document policy in ``ui/lib/csp.mjs`` holds the same union.
    """
    assert sorted(_ORIGIN_WILDCARDS) == ["'*'", "*", "*.*", "null"]
    assert "https://*.example" not in _ORIGIN_WILDCARDS
    assert "*" not in "null"


# --------------------------------------------------------------------------- #
# What must NOT change
# --------------------------------------------------------------------------- #
def test_a_legitimate_allowlist_still_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal that also turns away valid configuration is an outage, not a control.

    The rule gained a SUBSTRING test, which is the direction that can start refusing real
    origins, so the two shapes most likely to trip a careless rule are named here: an explicit
    PORT (the colon and digits) and a HYPHENATED host label, which is legal in DNS and common
    in tenant-specific origins.
    """
    named = "'self' https://portal.bank.example:8443 https://a-b-c.bank.example"
    assert _frame_ancestors(named) == named
    monkeypatch.setenv(
        _CORS_ORIGINS_ENV, "https://portal.bank.example:8443,https://a-b-c.admin.bank.example"
    )
    assert _cors_origins() == [
        "https://portal.bank.example:8443",
        "https://a-b-c.admin.bank.example",
    ]

    result = _boot(**{_FRAME_ANCESTORS_ENV: named})
    assert result.returncode == 0, result.stderr


def test_a_host_containing_the_word_null_is_not_a_wildcard() -> None:
    """The token match is exact and must stay exact: hosts may legitimately spell ``null``.

    Adding the asterisk half is a substring test, and the risk it introduces is that the
    exact-token half drifts into one too.
    """
    assert _frame_ancestors("https://nullify.bank.example") == "https://nullify.bank.example"


def test_the_unset_and_emptied_states_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the wildcard case is new; the two states this repo already resolved must hold.

    Unset frame-ancestors keeps the shipped ``'self'`` and emptied still refuses for its own
    reason (an empty CSP directive is a parse error browsers discard), which is a different
    refusal from this one and must not be swallowed by it. Emptied CORS still denies every
    origin rather than falling back to the dev origins.
    """
    assert _frame_ancestors(None) == "'self'"
    with pytest.raises(ValueError, match=_FRAME_ANCESTORS_ENV):
        _frame_ancestors("")

    monkeypatch.setenv(_CORS_ORIGINS_ENV, "")
    assert _cors_origins() == []


def test_a_total_lockdown_is_still_expressible() -> None:
    """``'none'`` is the way to forbid all framing, and refusing a wildcard must not remove it."""
    assert _frame_ancestors("'none'") == "'none'"


def _frame_ancestors_via_node(value: str) -> subprocess.CompletedProcess[str]:
    """Call the console's real ``frameAncestors`` with ``value`` and report what it did.

    Shelling out to node is the point. This test used to GREP ``csp.mjs`` for the literals
    the policy is written with, and the second re-audit pass proved that vacuous by
    execution: changing the guard to ``if (false && isWildcard(part))`` leaves every grepped
    string exactly where it was, so the whole Python suite stayed green with the
    clickjacking control switched off.

    A string a file contains is not a behaviour the file has. The only way to assert the
    second is to run it.
    """
    script = (
        "import { frameAncestors } from './ui/lib/csp.mjs';"
        "try { console.log('ALLOWED:' + frameAncestors({NEXT_PUBLIC_FRAME_ANCESTORS: "
        f"{value!r}"
        "})); }"
        "catch (error) { console.log('REFUSED:' + error.message); }"
    ).replace("'", '"', 0)
    return subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _node() -> str:
    """Node, or a failure. Never a skip.

    A skip here would restore exactly the defect this test was rewritten to close: evidence
    that reports the same green whether or not it ran. ``make check`` already requires node
    for ``ui-check``, so this adds no dependency the gate did not have.
    """
    from shutil import which

    found = which("node")
    if found is None:
        raise AssertionError(
            "node is required to execute the console's CSP policy. This test refuses to skip: "
            "the grep-based version it replaced passed while the clickjacking control was off, "
            "and a skip is the same green for the same reason."
        )
    return found


@pytest.mark.parametrize("spelling", sorted(_ORIGIN_WILDCARDS))
def test_the_console_document_policy_refuses_the_same_spellings(spelling: str) -> None:
    """The CSP a browser enforces for the console page comes from the UI, not from here.

    Closing only the API would leave the more directly exploitable surface open: the console
    document is served by Next.js and never passes through this middleware.

    Executed, not grepped. ``ui/tests/csp.test.mjs`` asserts the same behaviour and is the
    richer suite, but it runs behind ``npm ci`` and therefore behind a network; this half
    runs in the Python gate with nothing but node.
    """
    result = _frame_ancestors_via_node(spelling)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("REFUSED:"), (
        f"the console policy ACCEPTED {spelling!r} as a framing ancestor: {result.stdout.strip()}"
    )
    assert "never contain a wildcard" in result.stdout


def test_the_console_policy_refuses_a_host_source_wildcard() -> None:
    """The half a Set cannot match, and the half most likely to be reached in practice.

    ``https://*.evil.example`` is in no set and was emitted verbatim; CSP honours it as every
    subdomain, including one an attacker obtained by takeover.
    """
    result = _frame_ancestors_via_node("https://*.evil.example")

    assert result.stdout.startswith("REFUSED:"), result.stdout


def test_the_console_policy_still_accepts_a_real_origin() -> None:
    """The allow path, so the refusal above cannot be satisfied by a policy that refuses all.

    Without this, ``throw new Error()`` on every input would pass every test above.
    """
    result = _frame_ancestors_via_node("https://portal.example")

    assert result.stdout.startswith("ALLOWED:"), result.stdout
    assert "https://portal.example" in result.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
