from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parents[2]
_SCRIPT = _ROOT / "scripts" / "demo_selftest.py"
_SPEC = importlib.util.spec_from_file_location("demo_selftest", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _valid_evidence() -> dict[str, Any]:
    return json.loads((_ROOT / "tests" / "fixtures" / "demo_evidence.json").read_text())


def test_recorded_demo_fixture_is_valid() -> None:
    _MODULE.validate_evidence(_valid_evidence())


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_persona",
        "citation_outside_acl",
        "duplicate_risk_passage",
        "duplicate_risk_citation",
        "risk_not_reviewed",
        "risk_not_escalated",
        "retail_auto_cleared",
        "unknown_answered_ungrounded",
        "unknown_leak",
        "audit_pii_leak",
        "wrong_audit_sequence",
    ],
)
def test_false_green_demo_evidence_is_rejected(mutation: str) -> None:
    evidence = copy.deepcopy(_valid_evidence())
    if mutation == "duplicate_persona":
        evidence["personas"].append(copy.deepcopy(evidence["personas"][0]))
    elif mutation == "citation_outside_acl":
        evidence["personas"][0]["answer"]["citations"].append(
            {"document_id": "forbidden-document", "page": 9}
        )
    elif mutation == "duplicate_risk_passage":
        evidence["personas"][1]["passages"].append(
            copy.deepcopy(evidence["personas"][1]["passages"][0])
        )
    elif mutation == "duplicate_risk_citation":
        evidence["personas"][1]["answer"]["citations"].append(
            copy.deepcopy(evidence["personas"][1]["answer"]["citations"][0])
        )
    elif mutation == "risk_not_reviewed":
        evidence["personas"][1]["answer"]["requires_human_review"] = False
    elif mutation == "risk_not_escalated":
        # B3: a restricted-classification grounding must raise the bar to enhanced.
        evidence["personas"][1]["answer"]["review_level"] = "standard"
    elif mutation == "retail_auto_cleared":
        # B3: maker-checker is a floor; no answer may be auto-cleared.
        evidence["personas"][0]["answer"]["requires_human_review"] = False
    elif mutation == "unknown_answered_ungrounded":
        # B2: an unentitled caller must be refused, never answered without citations.
        evidence["personas"][2]["answer"]["refused"] = False
    elif mutation == "unknown_leak":
        evidence["personas"][2]["passages"].append({"document_id": "forbidden-document"})
    elif mutation == "wrong_audit_sequence":
        evidence["audit"]["actions"][0] = "answer"
    else:
        evidence["audit"]["raw_pii_absent"] = False

    with pytest.raises(RuntimeError, match="demo evidence mismatch"):
        _MODULE.validate_evidence(evidence)
