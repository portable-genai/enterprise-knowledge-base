#!/usr/bin/env python3
"""Self-start and strictly walk the live enterprise-knowledge-base demo server without a browser."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import kb_demo_server as demo


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"live demo mismatch: {message}")


def _get(base: str, path: str) -> str:
    with urllib.request.urlopen(base + path, timeout=10) as response:
        _require(response.status == 200, f"GET {path} status")
        return response.read().decode("utf-8")


def _post(base: str, path: str) -> str:
    request = urllib.request.Request(
        base + path,
        data=urllib.parse.urlencode({}).encode(),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        _require(response.status == 200, f"POST {path} redirected status")
        return response.read().decode("utf-8")


def _assert_step(
    base: str,
    index: int,
    key: str,
    marker: str,
    evidence_ids: list[str] | None = None,
) -> None:
    state = json.loads(_get(base, "/state"))
    _require(state == {"step": index}, f"state {key}")
    page = _get(base, "/")
    _require(f"data-demo-step='{key}'" in page, f"hook {key}")
    _require(f"data-demo-index='{index}'" in page, f"index {key}")
    _require(marker in page, f"evidence marker {key}")
    if evidence_ids is not None:
        documents = re.findall(r'data-document-id="([^"]+)"', page)
        citations = re.findall(r'data-citation-id="([^"]+)"', page)
        _require(documents == evidence_ids, f"document ids {key}")
        _require(citations == evidence_ids, f"citation ids {key}")


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo.Handler)
    server.session = demo.DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        _assert_step(
            base,
            0,
            "retail",
            "data-outcome='standard-review'",
            ["policy-cloud-onboarding-v3"],
        )
        _require(
            "data-demo-matrix='acl-visibility'" in _get(base, "/matrix"),
            "matrix endpoint",
        )
        try:
            _get(base, "/missing")
        except urllib.error.HTTPError as exc:
            _require(exc.code == 404, "missing route status")
        else:
            raise RuntimeError("live demo mismatch: missing route did not return 404")
        _post(base, "/advance")
        _assert_step(
            base,
            1,
            "risk",
            "data-outcome='enhanced-review'",
            ["standard-data-residency-v1", "notice-code-of-conduct-v1"],
        )
        _post(base, "/advance")
        _assert_step(base, 2, "unknown", "data-outcome='fail-closed'", [])
        _post(base, "/advance")
        _assert_step(base, 3, "matrix", "data-demo-matrix='acl-visibility'")
        _post(base, "/advance")
        _assert_step(base, 3, "matrix", "Demo complete")
        _post(base, "/restart")
        _assert_step(
            base,
            0,
            "retail",
            "data-outcome='standard-review'",
            ["policy-cloud-onboarding-v3"],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    print("PASS enterprise-knowledge-base live demo: self-start, strict four-step walk and restart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
