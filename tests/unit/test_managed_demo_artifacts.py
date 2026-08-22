from __future__ import annotations

import pytest
import yaml
from scripts.render_managed_demo_artifacts import render

from enterprise_kb.pipelines.acl_sync import parse_bindings
from enterprise_kb.pipelines.fetch import _to_document


def test_managed_demo_artifacts_render_to_the_real_registry_and_acl_contracts(tmp_path) -> None:
    render(
        raw_bucket="enterprise-knowledge-base-raw-demo-project",
        user_email="analyst@bank.example",
        output_dir=tmp_path,
    )
    registry = yaml.safe_load((tmp_path / "registry.yaml").read_text(encoding="utf-8"))
    document = _to_document(registry["documents"][0])
    bindings = parse_bindings((tmp_path / "bindings.json").read_text(encoding="utf-8"))
    assert document.uri == (
        "gs://enterprise-knowledge-base-raw-demo-project/sources/cloud-onboarding-policy.txt"
    )
    assert document.tenant == "", "the reviewed registry is the shared/global corpus"
    assert {tag.label for tag in document.acl_tags} == set(bindings[0].tags)
    assert bindings[0].principal_id == "user:analyst@bank.example"
    assert bindings[0].tenant == "bank.example"


@pytest.mark.parametrize(
    ("field", "value"),
    (("raw_bucket", ""), ("user_email", "not-an-email")),
)
def test_managed_demo_renderer_refuses_unsafe_identity_or_location(
    tmp_path, field: str, value: str
) -> None:
    values = {
        "raw_bucket": "enterprise-knowledge-base-raw-demo-project",
        "user_email": "analyst@bank.example",
    }
    values[field] = value
    with pytest.raises(ValueError):
        render(**values, output_dir=tmp_path)
