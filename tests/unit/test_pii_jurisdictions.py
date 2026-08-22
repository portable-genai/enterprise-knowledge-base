"""Jurisdiction PII packs keep the safety gate honest (C4) and the gate can go red (E2).

These are the two properties the catalogue asks for, and neither is provable by reading
the redactor alone:

* **Config-selected patterns with a false-green proof.** The home-jurisdiction pack must
  NOT mask another jurisdiction's identifiers. A fork that switches market and keeps the
  SG rows would otherwise inherit a gate that is green because it is blind.
* **One pattern source.** The runtime redactor and the eval ``pii_safety`` metric read
  the same rows from the shared ``pii-kit``, so a row a redactor stops masking is a row
  the gate stops passing.

All identifiers below are synthetic and obviously fictional.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from enterprise_kb.adapters.local.redaction import LocalRegexRedactionAdapter
from enterprise_kb.config import PiiSettings, Settings
from enterprise_kb.pii_patterns import patterns_for, re2_custom_info_types

_ROOT = Path(__file__).parents[2]

# Synthetic identifiers, one per market.
SG_NRIC = "S1234567D"
JP_MY_NUMBER = "1234 5678 9018"
HK_HKID = "A123456(3)"


def _redactor(*jurisdictions: str) -> LocalRegexRedactionAdapter:
    settings = replace(Settings(), pii=PiiSettings(jurisdictions=jurisdictions))
    return LocalRegexRedactionAdapter(settings)


def _load_run_eval():
    """Import ``eval/run_eval.py`` by path (it is a script, not a package module)."""
    if "run_eval" in sys.modules:
        return sys.modules["run_eval"]
    spec = importlib.util.spec_from_file_location("run_eval", _ROOT / "eval" / "run_eval.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the module defines dataclasses, whose annotation resolution
    # looks the defining module up in sys.modules.
    sys.modules["run_eval"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# The false-green proof: the home pack does NOT mask a foreign identifier.
# --------------------------------------------------------------------------- #
def test_home_jurisdiction_pack_does_not_mask_another_jurisdictions_identifier():
    """RED before C4: the SG-hardcoded redactor could never be pointed at another market.

    The point is not that SG rows fail on JP text (they must), it is that the failure is
    VISIBLE and fixable by configuration rather than by editing the adapter.
    """
    sg_only = _redactor("SG")
    result = sg_only.redact(f"Applicant {SG_NRIC}, my number {JP_MY_NUMBER}, id {HK_HKID}")
    assert SG_NRIC not in result.text, "the home identifier must be masked"
    assert JP_MY_NUMBER in result.text, "an SG-only pack must NOT silently cover JP"
    assert HK_HKID in result.text, "an SG-only pack must NOT silently cover HK"


def test_adding_a_jurisdiction_is_configuration_not_code():
    multi = _redactor("SG", "JP", "HK")
    result = multi.redact(f"Applicant {SG_NRIC}, my number {JP_MY_NUMBER}, id {HK_HKID}")
    assert SG_NRIC not in result.text
    assert JP_MY_NUMBER not in result.text
    assert HK_HKID not in result.text
    assert {f.info_type for f in result.findings} >= {"SG_NRIC_FIN", "JP_MY_NUMBER", "HK_HKID"}


def test_universal_rows_apply_in_every_jurisdiction():
    result = _redactor("JP").redact("write to jane.doe@bank.test")
    assert "jane.doe@bank.test" not in result.text
    assert any(f.info_type == "EMAIL_ADDRESS" for f in result.findings)


def test_lowercase_nric_is_not_missed():
    """A lower-cased NRIC typed into a free-text field is still a national identifier."""
    result = _redactor("SG").redact("contractor s7654321j has read access")
    assert "s7654321j" not in result.text


# --------------------------------------------------------------------------- #
# One pattern source, shared by the redactor, the DLP adapter and the gate.
# --------------------------------------------------------------------------- #
def test_runtime_redactor_and_eval_gate_read_the_same_rows():
    run_eval = _load_run_eval()
    settings_rows = patterns_for(Settings.load().pii.jurisdictions)
    assert settings_rows == run_eval.PII_PATTERNS
    # ...and the adapter the gate drives IS the runtime adapter, not a fake.
    assert isinstance(run_eval.build_redaction_adapter(), LocalRegexRedactionAdapter)


def test_dlp_custom_info_types_use_the_re2_safe_form():
    """DLP matches with RE2 (no lookaround); a Python-only pattern breaks every call."""
    rows = re2_custom_info_types(("JP",))
    assert rows, "the JP pack must contribute a custom info type"
    for row in rows:
        pattern = str(row["regex"]["pattern"])  # type: ignore[index]
        assert "(?<" not in pattern and "(?=" not in pattern and "(?!" not in pattern


def test_dlp_adapter_binds_the_configured_jurisdictions():
    from enterprise_kb.adapters.gcp.dlp_redaction import DlpRedactionAdapter

    settings = replace(Settings(), pii=PiiSettings(jurisdictions=("SG", "JP")))
    adapter = DlpRedactionAdapter(settings)
    names = {str(row["info_type"]["name"]) for row in adapter._custom_info_types}  # type: ignore[index]
    assert {"SG_NRIC_FIN", "JP_MY_NUMBER"} <= names


# --------------------------------------------------------------------------- #
# E2 : the safety metric is structurally unable to go falsely green.
# --------------------------------------------------------------------------- #
def test_eval_pii_safety_metric_can_go_red():
    """The harness self-check is the gate on the gate (systemic finding 8)."""
    run_eval = _load_run_eval()
    run_eval.self_check(run_eval.load_thresholds_from_rubrics())  # must not raise


def test_eval_pii_safety_scores_zero_when_an_identifier_survives():
    run_eval = _load_run_eval()
    example = run_eval.GoldenExample(
        id="leak",
        query=f"who is {SG_NRIC}",
        acl_principals=(),
        expected_doc_ids=(),
        must_not_see_ids=(),
        planted_pii=(SG_NRIC,),
    )
    assert run_eval.score_pii_safety([f"audit wrote {SG_NRIC}"], example) == 0.0
    assert run_eval.score_pii_safety(["audit wrote [NRIC]"], example) == 1.0


def test_planted_oracle_is_independent_of_the_pack():
    """Half two must fail even when the pack rows cannot see the identifier at all."""
    run_eval = _load_run_eval()
    foreign = run_eval.GoldenExample(
        id="foreign",
        query="q",
        acl_principals=(),
        expected_doc_ids=(),
        must_not_see_ids=(),
        planted_pii=(JP_MY_NUMBER,),
    )
    # The SG pack cannot detect a JP My Number, yet the literal oracle still goes red.
    assert run_eval.score_pii_safety([f"leaked {JP_MY_NUMBER}"], foreign) == 0.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
