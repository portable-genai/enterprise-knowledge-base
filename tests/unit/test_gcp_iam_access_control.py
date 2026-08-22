"""Offline contract tests for the managed, directory-provisioned AlloyDB ACL adapter."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from enterprise_kb.adapters.gcp.iam_access_control import IamAccessControlAdapter
from enterprise_kb.config import AlloyDBSettings, Settings


class _Statement:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.bindings: list[Any] = []

    def bindparams(self, *bindings: Any) -> _Statement:
        self.bindings.extend(bindings)
        return self


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]], *, error: Exception | None = None) -> None:
        self._rows = rows
        self._error = error
        self.calls: list[tuple[_Statement, dict[str, Any]]] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: _Statement, params: dict[str, Any]) -> _Result:
        self.calls.append((statement, params))
        if self._error is not None:
            raise self._error
        requested = set(params["principals"])
        rows = [
            {"tag_label": row["tag_label"]}
            for row in self._rows
            if row["tenant"] == params["tenant"]
            and row["principal_id"] in requested
            and row["enabled"]
        ]
        return _Result(rows)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


@pytest.fixture
def fake_sqlalchemy(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(
        text=lambda sql: _Statement(sql),
        bindparam=lambda name, **kwargs: (name, kwargs),
    )
    monkeypatch.setitem(sys.modules, "sqlalchemy", module)


def _adapter() -> IamAccessControlAdapter:
    return IamAccessControlAdapter(
        Settings(
            profile="gcp",
            alloydb=AlloyDBSettings(
                instance_uri="projects/fictional/locations/sg/clusters/kb/instances/primary",
                user="enterprise-kb-app@fictional.iam",
            ),
        )
    )


def test_alloydb_lookup_is_tenant_scoped_parameterized_and_maps_enabled_tags(
    fake_sqlalchemy: None,
) -> None:
    connection = _Connection(
        [
            {
                "tenant": "bank-a",
                "principal_id": "user:analyst@bank-a.example",
                "tag_label": "classification:internal",
                "enabled": True,
            },
            {
                "tenant": "bank-b",
                "principal_id": "user:analyst@bank-a.example",
                "tag_label": "classification:restricted",
                "enabled": True,
            },
            {
                "tenant": "bank-a",
                "principal_id": "user:analyst@bank-a.example",
                "tag_label": "disabled:grant",
                "enabled": False,
            },
            {
                "tenant": "bank-a",
                "principal_id": "group:risk",
                "tag_label": " risk:read ",
                "enabled": True,
            },
        ]
    )
    adapter = _adapter()
    adapter._engine = _Engine(connection)

    tags = adapter._tags_for_principals({"group:risk", "user:analyst@bank-a.example"}, "bank-a")

    assert tags == {"classification:internal", "risk:read"}
    statement, params = connection.calls[0]
    assert "tenant = :tenant" in statement.sql
    assert "principal_id IN :principals" in statement.sql
    assert "enabled IS TRUE" in statement.sql
    assert "bank-a" not in statement.sql
    assert "user:analyst@bank-a.example" not in statement.sql
    assert statement.bindings == [("principals", {"expanding": True})]
    assert params == {
        "tenant": "bank-a",
        "principals": ("group:risk", "user:analyst@bank-a.example"),
    }


def test_empty_verified_tenant_or_principals_deny_without_managed_calls() -> None:
    adapter = _adapter()

    assert adapter.resolve(["user:analyst@bank-a.example"], "") == set()
    assert adapter.resolve([], "bank-a") == set()
    assert adapter.resolve(["  "], "bank-a") == set()


def test_verified_user_and_group_principals_flow_into_the_tenant_query(
    fake_sqlalchemy: None,
) -> None:
    connection = _Connection(
        [
            {
                "tenant": "bank-a",
                "principal_id": "group:risk",
                "tag_label": "classification:confidential",
                "enabled": True,
            }
        ]
    )
    adapter = _adapter()
    adapter._engine = _Engine(connection)

    assert adapter.resolve(["group:risk", "user:analyst@bank-a.example"], "bank-a") == {
        "classification:confidential"
    }
    assert connection.calls[0][1]["principals"] == (
        "group:risk",
        "user:analyst@bank-a.example",
    )


def test_database_failure_propagates_instead_of_becoming_an_empty_success(
    fake_sqlalchemy: None,
) -> None:
    adapter = _adapter()
    adapter._engine = _Engine(_Connection([], error=RuntimeError("database unavailable")))

    with pytest.raises(RuntimeError, match="database unavailable"):
        adapter.resolve(["user:analyst@bank-a.example"], "bank-a")


def test_acl_table_identifier_is_validated_before_any_sql_is_built() -> None:
    with pytest.raises(ValueError, match="invalid AlloyDB ACL table identifier"):
        IamAccessControlAdapter(
            Settings(alloydb=AlloyDBSettings(acl_table="principal_acl_tags; DROP TABLE corpus"))
        )
