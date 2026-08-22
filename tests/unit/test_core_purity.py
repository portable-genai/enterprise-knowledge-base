"""The core imports only the standard library, its own package and the workspace kits.

``domain/`` and ``ports/`` are the code a client owns outright after an extraction: the policy,
the decisions and the port contracts. A cloud or model SDK import there is lock-in wearing a
green gate. The rule is an allowlist rather than an SDK blocklist, because a blocklist rots the
day a vendor renames a distribution.

Twin of the core-purity section of the fleet-wide portfolio gate,
which repeats this scan across every repository in the workspace. This copy travels with the
repository when it is extracted and handed over, which is exactly when the workspace scan can
no longer see it.

This repository has no dynamic blocked-import probe for the CLOUD SDKs:
``tests/contract/test_runtime_dependency_boundary.py`` blocks ``agent_eval_kit`` and imports two
modules, which is a different and narrower claim. Until such a probe exists, this static scan is
the only check standing between the core and a vendor import, so it is also the one that catches
a lazy import inside a function body that no construction path exercises.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

#: The workspace kits a core may import: stdlib-pure, versioned, owned in this catalog.
ALLOWED_KITS = frozenset(
    {
        "agent_eval_kit",
        "consent_preference_kit",
        "hex_service_kit",
        "review_kit",
        "obligation_register",
        "pii_kit",
        "speech_lexicon_kit",
    }
)

#: The trees that make up the core in this repository.
CORE_LAYERS = ("domain", "ports")

_STDLIB = frozenset(sys.stdlib_module_names)


def _core_trees() -> list[tuple[str, Path]]:
    trees: list[tuple[str, Path]] = []
    for package in sorted(p for p in SRC.iterdir() if (p / "__init__.py").is_file()):
        for layer in CORE_LAYERS:
            if (package / layer).is_dir():
                trees.append((package.name, package / layer))
    return trees


def _violations(
    trees: list[tuple[str, Path]], allowed_kits: frozenset[str]
) -> tuple[int, list[str]]:
    """(files scanned, violation lines). Factored so the control test can aim it at a bad tree."""
    scanned = 0
    found: list[str] = []
    for own_pkg, tree in trees:
        for source in sorted(tree.rglob("*.py")):
            scanned += 1
            syntax = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(syntax):
                if isinstance(node, ast.Import):
                    imported = [(alias.name, node.lineno) for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    imported = [(node.module or "", node.lineno)]
                else:
                    continue
                for dotted, lineno in imported:
                    top = dotted.split(".")[0]
                    if not top or top in _STDLIB or top == own_pkg or top in allowed_kits:
                        continue
                    location = (
                        source.relative_to(REPO_ROOT)
                        if source.is_relative_to(REPO_ROOT)
                        else source
                    )
                    found.append(f"{location}:{lineno}: imports {top}")
    return scanned, found


def test_the_core_imports_only_what_it_owns() -> None:
    trees = _core_trees()
    assert trees, "no core trees found under src/; a scan of nothing proves nothing"
    scanned, found = _violations(trees, ALLOWED_KITS)
    assert scanned > 0, "core trees exist but hold no python modules; the scan saw nothing"
    assert not found, "the core imports code it does not own:\n" + "\n".join(found)


def test_the_scan_can_see_a_violation(tmp_path: Path) -> None:
    """The positive control: a scanner that cannot go red is decoration, not a gate."""
    bad = tmp_path / "domain"
    bad.mkdir()
    (bad / "impure.py").write_text("import boto3\n", encoding="utf-8")
    scanned, found = _violations([("synthetic_pkg", bad)], ALLOWED_KITS)
    assert scanned == 1
    assert found and "imports boto3" in found[0]


def test_the_scan_treats_relative_and_own_imports_as_owned(tmp_path: Path) -> None:
    """``from . import x`` and imports of the owning package must not false-positive."""
    tree = tmp_path / "domain"
    tree.mkdir()
    (tree / "clean.py").write_text(
        "from . import sibling\nimport json\nfrom synthetic_pkg.ports import thing\n",
        encoding="utf-8",
    )
    scanned, found = _violations([("synthetic_pkg", tree)], ALLOWED_KITS)
    assert scanned == 1
    assert not found
