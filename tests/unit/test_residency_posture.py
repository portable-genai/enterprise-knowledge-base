"""Residency and sovereignty are posture-as-code, checked offline (D5, P-03).

What is provable with no cloud account, and is therefore proved here:

* residency remains configurable and is validated at app load, and the Terraform stack
  validates its deploy-time region against the same allowlist at plan time (the default
  allowlist is the single Singapore region, so an unset deploy cannot leave jurisdiction);
* the Org Policy resource-location allowlist is GENERATED from the same variable;
* the VPC-SC perimeter is dry-run first;
* CMEK is bound per service (it does not cascade) and the WORM bucket keeps retention;
* ``terraform fmt`` is clean (and ``terraform validate`` passes in CI, which needs the
  provider download but no credentials).

What is NOT provable here, and is deliberately not claimed: that the Org Policy, the
perimeter or CMEK are actually ENFORCED in a live project. That evidence needs a named
deployment.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from enterprise_kb.config import ResidencyError, ResidencySettings, Settings, _validate_residency

_TF_DIR = Path(__file__).parents[2] / "infra" / "terraform"


def _tf(name: str) -> str:
    return (_TF_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# App-load half of the allowlist
# --------------------------------------------------------------------------- #
def test_app_load_rejects_a_region_outside_the_allowlist():
    """RED before D5: any region loaded silently and the service started anyway."""
    settings = replace(
        Settings(),
        region="us-central1",
        residency=ResidencySettings(allowed_regions=("asia-southeast1",)),
    )
    with pytest.raises(ResidencyError) as excinfo:
        _validate_residency(settings)
    assert "us-central1" in str(excinfo.value)
    assert "residency.allowed_regions" in str(excinfo.value)


def test_app_load_accepts_an_allowlisted_second_region():
    """Adding a country is configuration, not a fork."""
    settings = replace(
        Settings(),
        region="asia-south1",
        residency=ResidencySettings(allowed_regions=("asia-southeast1", "asia-south1")),
    )
    _validate_residency(settings)  # must not raise


def test_shipped_settings_are_self_consistent():
    settings = Settings.load()
    assert settings.region in settings.residency.allowed_regions


# --------------------------------------------------------------------------- #
# Terraform half of the allowlist
# --------------------------------------------------------------------------- #
def test_terraform_validates_the_region_against_the_allowlist_at_plan_time():
    variables = _tf("variables.tf")
    assert 'variable "allowed_regions"' in variables
    assert "contains(var.allowed_regions, var.region)" in variables
    assert "length(var.allowed_regions) > 0" in variables
    # The region is a deploy-time input, NOT a literal pin: no hard-coded region equality.
    assert 'var.region == "asia-southeast1"' not in variables
    assert 'var.allowed_regions[0] == "asia-southeast1"' not in variables
    # The residency DEFAULT is unchanged, so an unset deploy stays in Singapore.
    assert 'default     = ["asia-southeast1"]' in variables
    assert 'default     = "asia-southeast1"' in variables


def test_org_policy_resource_locations_is_generated_from_the_allowlist():
    """A hand-written location list would drift from the validated variable."""
    org_policy = _tf("org_policy.tf")
    assert 'for r in var.allowed_regions : "in:${r}-locations"' in org_policy


def test_vpc_sc_perimeter_is_dry_run_first():
    vpc_sc = _tf("vpc_sc.tf")
    assert "use_explicit_dry_run_spec = var.vpc_sc_dry_run" in vpc_sc
    assert 'variable "vpc_sc_dry_run"' in _tf("variables.tf")
    assert re.search(r"vpc_sc_dry_run\"\s*\{[^}]*default\s*=\s*true", _tf("variables.tf"), re.S)


def test_no_terraform_resource_hard_codes_a_region():
    """Resources remain parametric: the region literal appears only as a variable default."""
    offenders: list[str] = []
    for path in sorted(_TF_DIR.glob("*.tf")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "asia-southeast1" in code and "default" not in code:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "region literals outside a variable default:\n" + "\n".join(offenders)


def test_cmek_is_bound_per_service_because_it_does_not_cascade():
    kms = _tf("kms.tf")
    members = re.findall(r'resource "google_kms_crypto_key_iam_member" "(\w+)"', kms)
    assert {"alloydb", "storage", "logging", "cloud_run", "observability"} <= set(members)
    assert "aiplatform" not in members, "disabled Agent Runtime receives no dormant key grant"
    assert "location = var.region" in kms, "the key ring must be regional, not global"


def test_worm_bucket_keeps_a_governance_retention_floor():
    variables = _tf("variables.tf")
    assert "var.retention_days >= 2557" in variables
    assert "retention_days = var.retention_days" in _tf("logging_worm.tf")


def test_service_account_keys_and_uploads_are_disabled_by_org_policy():
    org_policy = _tf("org_policy.tf")
    assert "iam.disableServiceAccountKeyCreation" in org_policy
    assert "iam.disableServiceAccountKeyUpload" in org_policy
    assert org_policy.count('enforce = "TRUE"') >= 3


# --------------------------------------------------------------------------- #
# Offline Terraform checks (skipped when the binary is absent; CI has it)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_terraform_fmt_is_clean():
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-recursive"],
        cwd=_TF_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"terraform fmt would rewrite:\n{result.stdout}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
