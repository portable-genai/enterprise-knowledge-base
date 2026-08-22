"""Presenter-controlled Playwright walkthrough of the live A2 KB demo.

Drives a headed browser through the governed-RAG flow served by
``scripts/kb_demo_server.py``. It is **paced by the presenter**: before each step it
prints what is about to happen and waits for you to press Enter, then performs the action
(click "Next ▶") and highlights the panel to look at. You stay in control of timing.

Usage (two terminals)::

    # terminal 1 : the live demo server
    KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py

    # terminal 2 : the guided walkthrough (a real Chrome window opens)
    pip install playwright && playwright install chromium     # one-time
    python scripts/kb_demo_playwright.py

It can also point at the real Next.js console instead of the demo server by setting
``DEMO_URL=http://localhost:3000`` (then you drive the live UI manually between steps).

Environment overrides:
    DEMO_URL    server base URL (default http://127.0.0.1:8092)
    HEADLESS=1  run headless (used for the self-test; no window)
    DEMO_AUTO=1 don't wait for Enter : advance automatically (self-test / recording)
    SLOWMO_MS   per-action slow-motion in ms (default 250 headed, 0 headless)
    CHROME_PATH explicit Chromium/Chrome binary (else Playwright's own)
"""

from __future__ import annotations

import contextlib
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_URL", "http://127.0.0.1:8092")
HEADLESS = os.environ.get("HEADLESS") == "1"
AUTO = os.environ.get("DEMO_AUTO") == "1"
SLOWMO = int(os.environ.get("SLOWMO_MS", "0" if HEADLESS else "250"))
CHROME_PATH = os.environ.get("CHROME_PATH") or None

# (narration shown in the terminal, whether this step clicks "Next", panel to spotlight)
STEPS = [
    (
        "Retail RM (user:jane@bank.test). She asks what due diligence is required before "
        "onboarding a cloud provider. The query is PII-redacted at the boundary, the "
        "corpus is ACL-filtered to her tags (retail + internal), and the answer is cited "
        "to source pages : auto-cleared, no human review needed.",
        False,
        ".panel",
    ),
    (
        "Risk officer (group:kb-approver) asks where restricted records must be stored. The SAME "
        "corpus now surfaces a restricted-classification source she is entitled to see, "
        "so the HUMAN REVIEW gate fires (P-06) : the agent proposes, a checker disposes.",
        True,
        ".review",
    ),
    (
        "An unentitled principal (user:nobody@bank.test) asks the same cloud-onboarding "
        "question. This domain-level ACL scenario resolves to NO tags, so the filter drops "
        "everything (fail-closed, P-09) : no authenticated-unknown claim is made.",
        True,
        ".denied",
    ),
    (
        "The ACL visibility matrix : one corpus, three callers, side by side : who saw "
        "what, how many citations, and which calls hit the maker-checker gate. Plus the "
        "live ingest that redacted an email and an NRIC before indexing (P-04).",
        True,
        ".panel",
    ),
]


def _pause(prompt: str) -> None:
    if AUTO:
        time.sleep(1.2)
        return
    try:
        input(prompt)
    except EOFError:  # non-interactive stdin
        time.sleep(1.0)


def _spotlight(page, selector: str | None) -> None:
    if not selector:
        return
    with contextlib.suppress(Exception):  # cosmetic only
        page.eval_on_selector_all(
            selector,
            "els => els.forEach((e,i)=>{ if(i<6){ e.style.transition='box-shadow .3s';"
            " e.style.boxShadow='0 0 0 3px #3a60f0'; setTimeout(()=>e.style.boxShadow='',1600);} })",
        )


def _reachable() -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(BASE + "/state", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        # The real Next.js console has no /state route; treat the root as reachable too.
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            return False


def main() -> int:
    if not _reachable():
        print(f"Cannot reach the demo server at {BASE}.")
        print("Start it first:  KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO, executable_path=CHROME_PATH)
        page = browser.new_context(viewport={"width": 1100, "height": 900}).new_page()

        print("\n=== A2 governed-RAG live demo : press Enter to advance each step ===\n")
        with contextlib.suppress(Exception):
            page.goto(BASE + "/restart", wait_until="load")  # demo server starts clean
        page.goto(BASE + "/", wait_until="load")

        for i, (say, click, spotlight) in enumerate(STEPS):
            print(f"[{i + 1}/{len(STEPS)}] {say}")
            _pause("        ⏎  press Enter to run this step… ")
            if click:
                btn = page.locator(".democtl button.next")
                if btn.count() and btn.is_enabled():
                    btn.click()
                    page.wait_for_load_state("load")
            page.wait_for_timeout(200)
            _spotlight(page, spotlight)
            page.wait_for_timeout(700)
            print()

        print("Demo complete. The browser stays open for questions.")
        _pause("        ⏎  press Enter to close the browser… ")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
