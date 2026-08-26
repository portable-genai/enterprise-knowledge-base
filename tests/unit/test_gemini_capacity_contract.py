"""Gemini calls use the reviewed Single-Zone PT capacity contract, at the MODEL location.

The location assertion below used to read ``asia-southeast1``, the compute region, and that is
the behaviour this test was written to pin. It was wrong in a way nothing could see offline:
model availability is per-location, ``us-central1`` and ``asia-southeast1`` serve no Gemini 3 at
all, and this repository pinned Gemini 3 ids -- so the configured model could never have
resolved on a deployment. ``gemini-3.1-pro`` sat in the hard-reasoning slot resolving nowhere.

``models.location`` is now separate from the compute region and defaults to the ``us``
multi-region, which carries an ML-processing residency guarantee where ``global`` carries none.

**Note what this means for a Singapore deployment**, since this file names one: Gemini 3 serves
``us`` and ``eu`` only, so an APAC deployment cannot have both Gemini 3 and in-region
processing. It must pin a model its own region serves, or set the location deliberately and
accept what that means. The capacity contract below is unchanged and still asserted.
"""

from __future__ import annotations

import sys
import types

from enterprise_kb.adapters.gcp.gemini_llm import GeminiLLMAdapter
from enterprise_kb.config import Settings


def test_managed_gemini_client_selects_dedicated_provisioned_throughput(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class HttpOptions:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.Client = Client
    genai.types = types.SimpleNamespace(HttpOptions=HttpOptions)
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    GeminiLLMAdapter(
        Settings(profile="gcp", project_id="bank-kb-prod", region="asia-southeast1")
    )._get_client()

    assert captured["vertexai"] is True
    # The MODEL location, not the compute region the Settings above carry.
    assert captured["location"] == "us"
    options = captured["http_options"]
    assert isinstance(options, HttpOptions)
    assert options.kwargs == {
        "api_version": "v1",
        "headers": {"X-Vertex-AI-LLM-Request-Type": "dedicated"},
    }
