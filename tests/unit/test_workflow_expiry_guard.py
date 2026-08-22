"""No CI step may branch on a hardcoded calendar date.

The defect this guards was a supply-chain gate that excepted a named advisory "until
2026-08-06" via ``[[ "$(date -u +%F)" > "2026-08-06" ]]``. Every failure mode of that shape
is silent. Before the date it downgrades a hard gate to an allowlist that nobody re-reads,
so an advisory the author never anticipated (here nanoid GHSA-2v37-7h3g-55p8) either slips
through or fails the build for a reason the comment does not explain. After the date the
step starts failing on a schedule, with no commit and no review to point at. And the pin the
exception leaned on had itself become the vulnerability: ``overrides.postcss = 8.5.22`` held
the package AT the version GHSA-fxqj-rqcc-2cmp flags and stopped the resolver moving past it.

A workflow that changes behaviour with the wall clock is untestable by definition: CI is
green today and red tomorrow from an identical tree. So the rule is structural rather than
about any one advisory. Exceptions belong in a commit that a human approves, not in a date
literal that expires unattended.

Scope: the SHELL of each ``run:`` step. Comments and prose may cite dates freely (an
advisory's publication date is useful context); it is the executable text that must not
carry one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

#: A calendar date written out in full. The `date -u +%F` comparison is the shape that bit
#: this repo, but any literal in executable text is the same standing time bomb.
DATE_LITERAL = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _run_scripts(workflow: Path) -> list[tuple[str, str]]:
    """Every ``run:`` script in the file, paired with the step name that owns it.

    Parsed rather than grepped: a literal inside a folded block scalar is indented and
    wrapped however the author left it, and a line-oriented scan cannot tell an executable
    line from the surrounding YAML.
    """
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    scripts: list[tuple[str, str]] = []
    for job_name, job in (document.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                label = step.get("name") or f"step {index}"
                scripts.append((f"{job_name} / {label}", step["run"]))
    return scripts


def _executable_lines(script: str) -> list[str]:
    """The script with its shell comments dropped, so prose about dates stays legal."""
    return [line for line in script.splitlines() if not line.lstrip().startswith("#")]


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOWS.iterdir() if p.suffix in {".yaml", ".yml"})


def test_the_workflow_directory_is_where_this_test_thinks_it_is() -> None:
    """The control: without it, a moved directory would make every cell below vacuously green."""
    assert WORKFLOWS.is_dir(), f"no workflow directory at {WORKFLOWS}"
    assert _workflow_files(), f"no workflow files under {WORKFLOWS}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_no_workflow_step_branches_on_a_hardcoded_date(workflow: Path) -> None:
    offenders = [
        f"{label}: {line.strip()}"
        for label, script in _run_scripts(workflow)
        for line in _executable_lines(script)
        if DATE_LITERAL.search(line)
    ]
    assert not offenders, (
        f"{workflow.name} branches on a hardcoded calendar date:\n  "
        + "\n  ".join(offenders)
        + "\n\nA gate whose verdict depends on the wall clock is green today and red "
        "tomorrow from an identical tree, and it expires with no commit and no review "
        "to point at. Fix the underlying advisory, or take the exception in a reviewed "
        "commit."
    )
