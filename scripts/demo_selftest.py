#!/usr/bin/env python3
"""Run the real enterprise-knowledge-base demo and assert its observed audit-first evidence."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PERSONAS = {"retail", "risk", "unknown"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"demo evidence mismatch: {message}")


def validate_evidence(evidence: dict[str, Any]) -> None:
    _require(evidence["profile"] == "local", "profile")
    _require(evidence["region"] == "asia-southeast1", "region")

    ingest = evidence["ingest"]
    _require(ingest["ok"] is True and ingest["chunks"] >= 1, "live ingest")
    _require(
        {item["info_type"]: item["count"] for item in ingest["redacted"]}
        == {"SG_NRIC_FIN": 1, "EMAIL_ADDRESS": 1},
        "redact-before-index findings",
    )
    indexed_text = ingest["indexed_text"]
    _require("jane.doe@bank.test" not in indexed_text, "raw email reached index")
    _require("S1234567A" not in indexed_text, "raw NRIC reached index")
    _require("[EMAIL]" in indexed_text, "email mask absent from index")
    _require("[NRIC]" in indexed_text, "NRIC mask absent from index")

    personas = evidence["personas"]
    keys = [persona["key"] for persona in personas]
    _require(keys == ["retail", "risk", "unknown"], "persona order")
    _require(set(keys) == EXPECTED_PERSONAS, "persona set")
    _require(len(keys) == len(set(keys)), "persona uniqueness")
    by_key = {persona["key"]: persona for persona in personas}

    retail = by_key["retail"]
    _require(
        [item["document_id"] for item in retail["passages"]] == ["policy-cloud-onboarding-v3"],
        "retail ACL result",
    )
    # B3: maker-checker is a floor, so the confident, non-sensitive answer is still
    # reviewed; what distinguishes it from the risk persona is the LEVEL.
    _require(retail["answer"]["requires_human_review"] is True, "retail gate")
    _require(retail["answer"]["review_level"] == "standard", "retail review level")
    _require(retail["answer"]["refused"] is False, "retail refusal")

    risk = by_key["risk"]
    _require(
        [item["document_id"] for item in risk["passages"]]
        == ["standard-data-residency-v1", "notice-code-of-conduct-v1"],
        "risk ACL result",
    )
    _require(risk["answer"]["requires_human_review"] is True, "risk gate")
    _require(risk["answer"]["review_level"] == "enhanced", "risk review level")
    _require(
        "sensitive_classification" in risk["answer"]["review_reasons"],
        "risk escalation reason",
    )

    unknown = by_key["unknown"]
    _require(not unknown["passages"], "unknown caller passages")
    _require(not unknown["answer"]["citations"], "unknown caller citations")
    _require(unknown["answer"]["confidence"] == 0.0, "unknown caller confidence")
    _require(unknown["answer"]["requires_human_review"] is True, "unknown caller gate")
    # B2: an unentitled caller is REFUSED, never given an uncited answer.
    _require(unknown["answer"]["refused"] is True, "unknown caller refusal")

    for persona in personas:
        admitted = [item["document_id"] for item in persona["passages"]]
        cited = [item["document_id"] for item in persona["answer"]["citations"]]
        _require(cited == admitted, f"{persona['key']} citation/admission parity")
        _require(
            all(item["page"] is not None for item in persona["answer"]["citations"]),
            f"{persona['key']} page citations",
        )

    audit = evidence["audit"]
    expected_actions = [
        "ingest",
        "search",
        "search",
        "answer",
        "search",
        "search",
        "answer",
        "search",
        "search",
        "answer",
    ]
    _require(audit["actions"] == expected_actions, "audit actions")
    _require(audit["entries"] == audit["chained"] == len(expected_actions), "audit count")
    _require(audit["chain_ok"] is True, "audit chain")
    _require(audit["raw_pii_absent"] is True, "audit PII boundary")


def _validate_rendered_pages(out: Path) -> None:
    expected = {
        "kb-persona-retail.html": "data-outcome='standard-review'",
        "kb-persona-risk.html": "data-outcome='enhanced-review'",
        "kb-persona-unknown.html": "data-outcome='fail-closed'",
        "kb-acl-matrix.html": "data-demo-matrix='acl-visibility'",
    }
    _require({path.name for path in out.glob("*.html")} == set(expected), "rendered page set")
    for name, marker in expected.items():
        content = (out / name).read_text(encoding="utf-8")
        _require(marker in content, f"rendered marker {name}")

    expected_ids = {
        "kb-persona-retail.html": ["policy-cloud-onboarding-v3"],
        "kb-persona-risk.html": [
            "standard-data-residency-v1",
            "notice-code-of-conduct-v1",
        ],
        "kb-persona-unknown.html": [],
    }
    for name, ids in expected_ids.items():
        content = (out / name).read_text(encoding="utf-8")
        documents = re.findall(r'data-document-id="([^"]+)"', content)
        citations = re.findall(r'data-citation-id="([^"]+)"', content)
        _require(documents == ids, f"rendered document ids {name}")
        _require(citations == ids, f"rendered citation ids {name}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hrz2-demo-selftest-") as directory:
        root = Path(directory)
        artifact = root / "demo.json"
        rendered = root / "rendered"
        env = os.environ.copy()
        env.update(KB_PROFILE="local", PYTHONPATH=str(ROOT / "src"))
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "kb_demo.py"), str(artifact)],
            cwd=ROOT,
            env=env,
            check=True,
        )
        evidence: dict[str, Any] = json.loads(artifact.read_text(encoding="utf-8"))
        validate_evidence(evidence)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "render_kb_ui.py"),
                str(artifact),
                str(rendered),
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        _validate_rendered_pages(rendered)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "demo_server_selftest.py")],
            cwd=ROOT,
            env=env,
            check=True,
        )

    print(
        "PASS enterprise-knowledge-base demo self-test: observed domain, audit and rendered "
        "evidence agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
