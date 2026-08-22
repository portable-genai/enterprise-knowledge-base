"""The scheduled pipeline image is reproducible and lock-driven."""

from pathlib import Path


def test_pipeline_image_uses_pinned_base_and_runtime_lock() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "src/enterprise_kb/pipelines/Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count("python:3.12-slim@sha256:") == 2
    assert "COPY pyproject.toml README.md requirements-gcp.lock" in dockerfile
    assert "pip install -r requirements-gcp.lock" in dockerfile
    assert "pip install --no-deps ." in dockerfile
    assert "pip install --upgrade" not in dockerfile
    assert 'pip install ".[gcp]"' not in dockerfile
    assert ":latest" not in dockerfile
