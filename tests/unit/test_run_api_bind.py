"""Executable contract for the operator-facing ``make run-api`` bind guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _check_bind(host: str, *, allow_insecure: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("KB_ALLOW_INSECURE_DEMO", None)
    if allow_insecure:
        env["KB_ALLOW_INSECURE_DEMO"] = "1"
    return subprocess.run(
        ["make", "check-api-bind", f"API_HOST={host}"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_run_api_accepts_only_loopback_without_an_explicit_opt_in(host: str) -> None:
    assert _check_bind(host).returncode == 0


def test_run_api_refuses_non_loopback_without_an_explicit_opt_in() -> None:
    result = _check_bind("0.0.0.0")
    assert result.returncode != 0
    assert "KB_ALLOW_INSECURE_DEMO=1" in result.stderr


def test_run_api_allows_non_loopback_only_with_the_explicit_opt_in() -> None:
    assert _check_bind("0.0.0.0", allow_insecure=True).returncode == 0


def test_run_api_target_uses_the_application_bind_resolver() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "API_HOST    ?= 127.0.0.1" in makefile
    assert "$(PYTHON) -m enterprise_kb.api.app" in makefile
    assert "uvicorn $(API_APP)" not in makefile
