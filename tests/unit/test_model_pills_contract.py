"""The half of the model-pill contract that lives in the BROWSER.

Every served console shows two small pills at the top right of every page: the model that
ANSWERED the last request, and ``Search`` when that answer used an online search tool (owner
decision, 2026-09-23). They replaced a full-width provenance banner that stated WHERE the console
runs and WHICH model configuration would call. Before any answer, the model pill still shows
that configured model, dimmed, with where it runs in its title. The SERVICE half -- the profile
implies a runtime, ``/healthz`` answers from the binding the container builds, and an answer
carries ``X-Answered-By`` / ``X-Search-Used`` readable cross-origin -- is pinned in
``tests/unit/test_health_provenance.py`` and ``tests/unit/test_answer_provenance.py`` and is not
restated here.

This file pins the other half, and the other half is the one that broke. On 2026-09-04 eight
consoles were found to have been rendering NOTHING on every page load since the banner landed:
the component named ``/api/agent``, the same-origin route handler the service template ships,
in trees that ship no such handler. The health call reached a path nothing serves, took the
failure branch, and the failure branch renders nothing -- deliberately, because a strip that
guessed would assert provenance it does not have. A check that cannot fail loudly fails as an
ABSENCE, and an absent strip is exactly what no reviewer notices.

Every service-side assertion was true and green throughout. That is why these live in their own
file: a green service half says nothing about whether a reader ever sees the pill.

The assertions pin AGREEMENT rather than literals, because this fleet legitimately runs the
console in more than one shape and a check keyed on one of them passes by blindness in the
others -- which is how a working strip in nine consoles came to be reported as missing.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path("ui")

#: The runtime wording the configured pill carries in its title, spelled once in whichever
#: component owns the pills.
#:
#: Locating that component by what it SAYS rather than by where it sits is the point. This fleet
#: kept the old strip in three different places: ``ui/app/ProvenanceBanner.tsx`` in the trees that
#: took the 2026-08-31 sweep, ``ui/components/ProvenanceBanner.tsx`` in the launch-set consoles
#: that adopted it a day earlier, and inline in the app chrome in ``cdd-sow-research``, which
#: carried it before either. A path-keyed check reports two of those three as having no banner
#: at all, and a class-keyed one (``.provenance-banner``) misses every console that styles the
#: strip with utility classes instead. Both mistakes have been made, and the pills inherit the
#: same three homes (here ``ui/components/ModelPills.tsx``).
_WORDING = "running on GCP"

#: Build output and vendored packages are not this console's source.
_NOT_SOURCE = frozenset({"node_modules", ".next", "dist", "out", "coverage"})


def _console_sources() -> list[Path]:
    """Every ``.tsx`` this console actually ships, build output and vendored trees pruned."""
    found: list[Path] = []
    pending = [UI]
    while pending:
        for child in pending.pop().iterdir():
            if child.is_dir():
                if child.name not in _NOT_SOURCE:
                    pending.append(child)
            elif child.suffix == ".tsx":
                found.append(child)
    return sorted(found)


def _pills_source() -> Path:
    """The component that renders the pills, wherever this console keeps it."""
    hits = [p for p in _console_sources() if _WORDING in p.read_text()]
    assert hits, (
        "no component under ui/ carries the runtime wording, so this console states neither "
        "where it runs nor which model answers at the top of any page"
    )
    assert len(hits) == 1, (
        f"more than one component renders provenance ({[str(p) for p in hits]}), so two pages "
        "can phrase the same fact differently and only one of them can be the one a screenshot "
        "came from"
    )
    return hits[0]


def test_the_pills_are_mounted_in_the_layout_rather_than_in_a_page() -> None:
    """Being at the top of EVERY page is a property of the console, not of any page.

    Mounted per page, the pills are present on the pages somebody remembered and absent on the
    one a screenshot came from -- and the absence is invisible, because they render nothing
    until the service answers anyway. The layout is the only mount that cannot be forgotten by
    adding a route.
    """
    layout = Path("ui/app/layout.tsx")
    assert layout.is_file(), "this console has no root layout, so nothing can be mounted for it"
    owner = _pills_source().stem
    assert f"<{owner} />" in layout.read_text(), (
        f"{owner} renders the model pills but the root layout does not mount it, so the pills "
        "reach only the pages that remember to mount them"
    )


def test_the_console_shows_the_model_that_answered_as_pills_not_a_banner() -> None:
    """Two pills name the model that ANSWERED, and Search when it searched.

    The whole browser chain is held here from the offline gate: the pills start from
    ``/healthz``, read both answer headers through the one fetch wrapper over the same base the
    console calls, and the old banner is gone. ``ui/tests/answer-provenance.test.mjs`` proves the
    wrapper itself. This console calls its service directly, with no proxy route in between, so
    the headers reach it only because the service lists them in
    ``Access-Control-Expose-Headers`` (pinned in ``test_answer_provenance.py``); a proxy route,
    if one is ever added, must forward both.
    """
    source = _pills_source()
    pills = source.read_text()
    assert source.name == "ModelPills.tsx"
    assert "api.health()" in pills or '"/healthz"' in pills, "the pills do not start from /healthz"
    assert "watchAnswers(window, BASE_URL" in pills, "the pills do not read the answer headers"
    assert "generator_model" in pills and "runtime" in pills
    assert 'data-testid="model-pills"' in pills
    for title in (
        "running locally",
        "answered the last request",
        "the last answer used an online search tool",
    ):
        assert title in pills, title
    watcher = Path("ui/lib/answer-provenance.mjs").read_text()
    for header in ('"x-answered-by"', '"x-search-used"'):
        assert header in watcher, "the pills never read " + header
    assert Path("ui/tests/answer-provenance.test.mjs").is_file()
    assert not Path("ui/components/ProvenanceBanner.tsx").exists(), "the old banner is back"
    for route in sorted(Path("ui/app").rglob("route.ts")):
        text = route.read_text()
        for header in ('"x-answered-by"', '"x-search-used"'):
            assert header in text, f"{route} proxies the service but drops {header}"


def test_the_pills_call_a_base_this_console_actually_serves() -> None:
    """The defect that shipped, stated as an assertion.

    Both architectures are legitimate, so this pins AGREEMENT rather than a literal. A tree with
    ``ui/app/api/agent`` proxies through its own origin and the strip should name that path; a
    tree without one must reach its backend the way the rest of the console does, through the
    ``NEXT_PUBLIC_*`` base resolved once in ``ui/lib/api``. The combination that shipped --
    naming the proxy while having none -- is the only one that is never right.

    Sharing the base is what makes the health call REACHABLE rather than merely tidy: the
    ``connect-src`` the console ships is built from that same value, and a cross-origin
    standalone run is on the service's CORS allowlist because every other call already needs to
    be. A health check on a base of its own would have to earn both of those separately, and
    would be silently refused until it did.
    """
    source = _pills_source()
    pills = source.read_text()
    proxies_through_own_origin = Path("ui/app/api/agent").is_dir()

    assert ('"/api/agent"' in pills) == proxies_through_own_origin, (
        f"{source} names /api/agent but this console has no route handler at ui/app/api/agent, "
        "so the health call reaches nothing and the pills render nothing"
        if not proxies_through_own_origin
        else f"this console ships a /api/agent route handler but {source} does not use it"
    )

    if not proxies_through_own_origin:
        # Either spelling of the same fact: the component may import the resolved base itself,
        # or call the client function that already closes over it. What it must not do is spell
        # a base of its own -- a second, independently resolved origin is how the two drift
        # apart again, and the drift is invisible until a deployment serves through a proxy.
        reaches_the_shared_client = "API_BASE" in pills or re.search(
            r'from\s+"(?:\.\./)+lib/api(?:\.mjs)?"', pills
        )
        assert reaches_the_shared_client, (
            f"{source} must reach its backend through the base the rest of this console reads "
            "(ui/lib/api, which resolves NEXT_PUBLIC_*) rather than spelling one of its own"
        )


#: Utility classes that move an element UP. Tailwind spells a negative offset with a leading
#: dash, so this is the whole vocabulary that can hoist the pills out of the viewport.
_PULLS_UP = ("-mt-", "-my-", "-top-", "-inset-y-", "-inset-")


def test_the_pills_are_fixed_at_the_top_right_where_a_reader_can_see_them() -> None:
    """Pills that render off-screen have satisfied every other assertion in this file.

    The old strip once rendered 32px ABOVE the viewport in eight consoles: it held the right
    text and was in the DOM on every page load, and was visible on none. The pills cannot
    scroll away, because they are ``fixed``, and they are anchored to the top and right edges
    with a non-negative offset, inside the page's own top padding, so they never cover the
    header below it.
    """
    pills = _pills_source().read_text()
    container = re.search(r'className="([^"]*)"\s*\n?\s*data-testid="model-pills"', pills)
    assert container, "the pills' container carries no class list this check can read"
    classes = container.group(1).split()
    assert "fixed" in classes, "the pills scroll with the page instead of staying in view"
    assert any(c.startswith("top-") for c in classes), "the pills are not anchored to the top"
    assert any(c.startswith("right-") for c in classes), "the pills are not anchored to the right"
    offenders = sorted(
        {token for token in re.findall(r"[-\w:./\[\]%]+", pills) if token.startswith(_PULLS_UP)}
    )
    assert not offenders, f"the pills carry {offenders}, which pulls them out of the viewport"
    css = Path("ui/app/globals.css").read_text()
    assert ".provenance-banner" not in css, "the old banner's rule outlived the banner"
