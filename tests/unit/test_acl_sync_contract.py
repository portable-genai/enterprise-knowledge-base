"""The reviewed ACL artifact gives verified IAP principals concrete managed entitlements."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from enterprise_kb.pipelines.acl_sync import parse_bindings
from enterprise_kb.pipelines.refresh_job import _managed_refresh_lease

ROOT = Path(__file__).resolve().parents[2]


def test_iap_email_principal_shape_maps_to_reviewed_binding_tags() -> None:
    bindings = parse_bindings(
        '{"bindings":[{"tenant":"bank.example","principal_id":'
        '"user:analyst@bank.example","tags":["classification:internal","dept:risk"]}]}'
    )
    assert bindings[0].principal_id == "user:analyst@bank.example"
    assert set(bindings[0].tags) == {"classification:internal", "dept:risk"}


@pytest.mark.parametrize(
    "raw",
    (
        "{}",
        '{"bindings":[]}',
        '{"bindings":[{"tenant":"","principal_id":"user:x","tags":["a"]}]}',
        '{"bindings":[{"tenant":"t","principal_id":"user:x","tags":[]}]}',
    ),
)
def test_acl_artifact_refuses_empty_or_incomplete_authority(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_bindings(raw)


def test_refresh_job_synchronizes_acl_before_corpus_and_terraform_injects_same_bucket_uri() -> None:
    refresh = (ROOT / "src/enterprise_kb/pipelines/refresh_job.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "infra/terraform/scheduler.tf").read_text(encoding="utf-8")
    run_body = refresh[refresh.index("def run(") :]
    assert run_body.index("sync_acl_bindings(container)") < run_body.index("ingest.refresh_all")
    assert 'name  = "KB_ACL_BINDINGS_URI"' in scheduler
    assert "var.acl_bindings_uri" in scheduler
    assert '"gs://${google_storage_bucket.control_inputs.name}/acl/"' in scheduler
    assert "task_count  = 1" in scheduler
    assert "parallelism = 1" in scheduler


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _LeaseConnection:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.statements: list[str] = []

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return None

    def exec_driver_sql(self, statement, _params):  # type: ignore[no-untyped-def]
        rendered = str(statement)
        self.statements.append(rendered)
        return _ScalarResult(self.acquired if "pg_try_advisory_lock" in rendered else True)


def _lease_container(connection: _LeaseConnection):  # type: ignore[no-untyped-def]
    engine = SimpleNamespace(connect=lambda: connection)
    access_control = SimpleNamespace(_get_engine=lambda: engine)
    return SimpleNamespace(
        settings=SimpleNamespace(profile="gcp"),
        access_control=access_control,
    )


def test_managed_refresh_lease_serializes_acl_and_corpus_mutations() -> None:
    connection = _LeaseConnection(acquired=True)

    with _managed_refresh_lease(_lease_container(connection)):
        assert connection.statements == ["SELECT pg_try_advisory_lock(%s)"]

    assert connection.statements[-1] == "SELECT pg_advisory_unlock(%s)"


def test_managed_refresh_lease_refuses_concurrent_execution_without_mutation() -> None:
    connection = _LeaseConnection(acquired=False)

    with (
        pytest.raises(RuntimeError, match="another governed corpus refresh"),
        _managed_refresh_lease(_lease_container(connection)),
    ):
        raise AssertionError("busy lease must never enter the mutation block")

    assert connection.statements == ["SELECT pg_try_advisory_lock(%s)"]
