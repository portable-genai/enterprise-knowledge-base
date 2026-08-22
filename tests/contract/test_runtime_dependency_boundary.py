"""Serving imports must not depend on the development-only evaluation scaffold.

Scope, stated so this file is not mistaken for a broader proof than it is: the blocker below
refuses the ``agent_eval_kit`` root and NOTHING else, and the process it runs imports two
modules rather than constructing anything. It says that a deployment installing only the
runtime dependencies can still import the serving app; it says nothing about the Google Cloud
SDKs, and nothing about whether any profile's adapters can be built.

The cloud-SDK claim ("every profile constructs with ``google`` and ``vertexai`` unimportable")
lives next door in ``test_sdk_free_build.py``, which blocks those roots and constructs every
adapter binding of every profile. The two are separate proofs about separate dependency
boundaries, and neither one covers the other.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_serving_app_imports_when_agent_eval_kit_is_blocked() -> None:
    root = Path(__file__).resolve().parents[2]
    script = """
import importlib.abc
import sys

class BlockAgentEvalKit(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agent_eval_kit" or fullname.startswith("agent_eval_kit."):
            raise ModuleNotFoundError("agent_eval_kit is intentionally unavailable")
        return None

sys.meta_path.insert(0, BlockAgentEvalKit())
import enterprise_kb.ports
import enterprise_kb.api.app
"""
    env = dict(os.environ)
    env["KB_PROFILE"] = "local"
    env["PYTHONPATH"] = os.pathsep.join((str(root / "src"), env.get("PYTHONPATH", "")))

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "agent_eval_kit" not in result.stderr
