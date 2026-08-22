"""Live, click-through demo server for the A2 governed-RAG flow (stdlib only).

Holds a real :class:`KnowledgeBaseService` over the built-in ``local`` stack and runs the
*actual* governed-RAG pipeline one ACL scope per click : retail RM -> risk officer ->
unentitled principal (fail-closed) -> ACL matrix overview : rendering the audit-first UI at each
step. No Google Cloud, no API key, no emulator, no extra dependencies. The demo port
(8092) is deliberately distinct from the API port (8082) so both can run at once.

    KB_PROFILE=local PYTHONPATH=src python scripts/kb_demo_server.py [--port 8092]

Then open http://localhost:8092 and click "Next ▶", or drive it with
``scripts/kb_demo_playwright.py`` for a presenter-controlled walkthrough.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("KB_PROFILE", "local")

import kb_demo as demo  # noqa: E402  sibling script: reuse the synthetic scenario + flow
import render_kb_ui as r  # noqa: E402  sibling script: reuse the audit-first rendering

# The scripted demo steps. Each "Next" reveals the next persona / overview page.
STEPS = [
    {
        "key": "retail",
        "label": "Retail RM : sees internal + retail, auto-cleared",
        "next": "Same corpus as the risk officer : a restricted source forces review",
    },
    {
        "key": "risk",
        "label": "Risk officer : a restricted source forces maker-checker review",
        "next": "Same corpus under an unentitled ACL scope : fail-closed access",
    },
    {
        "key": "unknown",
        "label": "Unentitled principal : no ACL tags, sees nothing (fail-closed)",
        "next": "Show the ACL visibility matrix across all three callers",
    },
    {"key": "matrix", "label": "ACL visibility matrix : who saw what, and why", "next": None},
]

_CONTROL_CSS = """
.democtl{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  margin:-24px -18px 16px;padding:12px 18px;background:#0b101a;color:#fff}
.democtl .lbl{font-size:13px}.democtl .lbl b{color:#90b2ff}
.democtl .spacer{flex:1}
.democtl form{margin:0}
.democtl button{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:7px;
  padding:7px 14px;cursor:pointer}
.democtl .next{background:#3a60f0;color:#fff}.democtl .next:disabled{opacity:.4;cursor:default}
.democtl .restart{background:transparent;color:#a6b6cc;border:1px solid #33445b}
.democtl .pct{font-variant-numeric:tabular-nums;color:#cdd7e4;font-size:12px}
"""


class DemoSession:
    """Runs the real governed-RAG flow once, then reveals it one persona per click."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # Run the whole flow up front (deterministic, fast), then reveal step by step.
        self.data = demo.run()
        self.personas = {p["key"]: p for p in self.data["personas"]}
        self.idx = 0

    @property
    def at_end(self) -> bool:
        return self.idx >= len(STEPS) - 1

    def advance(self) -> None:
        if not self.at_end:
            self.idx += 1

    # -- rendering --------------------------------------------------------- #
    def render(self) -> str:
        key = STEPS[self.idx]["key"]
        if key == "matrix":
            html = r.render_matrix(self.data)
        else:
            html = r.render_persona(self.data, self.personas[key])
        return self._inject_controls(html)

    def _inject_controls(self, html: str) -> str:
        step = STEPS[self.idx]
        nxt = step["next"]
        meta = ""
        key = step["key"]
        if key in self.personas:
            p = self.personas[key]
            conf = round(float(p["answer"]["confidence"]) * 100)
            meta = f"<span class='pct'>{len(p['passages'])} passages · confidence {conf}%</span>"
        next_btn = (
            f"<form method='post' action='/advance'><button class='next' "
            f"data-demo-action='advance' type='submit'>"
            f"Next ▶ &nbsp;·&nbsp; {r.esc(nxt)}</button></form>"
            if nxt
            else "<button class='next' data-demo-action='complete' disabled>Demo complete ✓</button>"
        )
        bar = (
            f"<div class='democtl' data-demo-step='{r.esc(key)}' "
            f"data-demo-index='{self.idx}'>"
            f"<span class='lbl'>Step {self.idx + 1}/{len(STEPS)} : <b>{r.esc(step['label'])}</b></span>"
            f"{meta}<span class='spacer'></span>{next_btn}"
            "<form method='post' action='/restart'><button class='restart' "
            "data-demo-action='restart' type='submit'>Restart</button></form>"
            "</div>"
        )
        html = html.replace("</style>", _CONTROL_CSS + "</style>", 1)
        return html.replace("<div class='wrap'>", "<div class='wrap'>" + bar, 1)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, to: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    @property
    def _sess(self) -> DemoSession:
        return self.server.session  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/":
                self._send(self._sess.render())
            elif path == "/matrix":
                self._send(r.render_matrix(self._sess.data))
            elif path == "/state":
                self._send(json.dumps({"step": self._sess.idx}), 200)
            elif path == "/restart":
                # Allowed over GET so the walkthrough can reset with a plain navigation.
                self._sess.reset()
                self._redirect("/")
            else:
                self._send("<h1>404</h1>", 404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/advance":
                self._sess.advance()
            elif path == "/restart":
                self._sess.reset()
        self._redirect("/")

    def log_message(self, *args: object) -> None:  # quiet console
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Live A2 KB governed-RAG demo server")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.session = DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    print(f"A2 KB demo server on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
