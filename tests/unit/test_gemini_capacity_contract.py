"""Singapore Gemini calls use the reviewed Single-Zone PT capacity contract."""

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
    assert captured["location"] == "asia-southeast1"
    options = captured["http_options"]
    assert isinstance(options, HttpOptions)
    assert options.kwargs == {
        "api_version": "v1",
        "headers": {"X-Vertex-AI-LLM-Request-Type": "dedicated"},
    }
