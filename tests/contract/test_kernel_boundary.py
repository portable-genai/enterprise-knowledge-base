"""The kernel/vertical split is real and one-way (A7).

The catalogue asks for an explicitly NAMED kernel that a fork never edits, with the
vertical models importing from it and not the reverse. A boundary that is only a docstring
drifts the first time someone adds a vertical type to the shared module, so these tests
assert it structurally:

* ``domain/kernel.py`` exists and imports nothing from this package;
* every name in ``kernel.KERNEL_EXPORTS`` is re-exported by ``domain/models.py`` and is
  the SAME object (backward-compatible re-export, not a copy);
* the vertical models actually reference kernel types, so the split is load-bearing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from enterprise_kb.domain import kernel, models

_KERNEL_PATH = Path(kernel.__file__)


def test_kernel_module_exists_and_is_named():
    assert _KERNEL_PATH.name == "kernel.py"
    assert kernel.KERNEL_EXPORTS, "the kernel must declare its export surface"


def test_kernel_imports_nothing_from_this_package():
    """One-way dependency: a fork can lift kernel.py out without pulling the vertical."""
    tree = ast.parse(_KERNEL_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"kernel must not import relatively: {ast.dump(node)}"
            assert not (node.module or "").startswith("enterprise_kb"), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("enterprise_kb"), alias.name


def test_models_reexports_every_kernel_name_as_the_same_object():
    for name in kernel.KERNEL_EXPORTS:
        assert hasattr(models, name), f"models must re-export the kernel name {name}"
        assert getattr(models, name) is getattr(kernel, name), (
            f"{name} is a COPY in models, not a re-export: the two would drift"
        )
        assert name in models.__all__


def test_vertical_models_are_not_in_the_kernel():
    """The artifact models a fork rewrites must NOT have leaked into the shared half."""
    for name in ("GroundedAnswer", "Document", "AclTag", "RetrievedPassage", "FreshnessRecord"):
        assert hasattr(models, name)
        assert not hasattr(kernel, name), f"{name} is vertical and must stay out of the kernel"


def test_vertical_depends_on_the_kernel():
    """GroundedAnswer is assembled from kernel types, so the boundary carries weight."""
    assert models.GroundedAnswer.__annotations__["citations"] == "tuple[Citation, ...]"
    assert models.Citation is kernel.Citation
    assert models.ReviewLevel is kernel.ReviewLevel


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
