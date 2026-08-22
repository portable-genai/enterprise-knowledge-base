"""Emulator opt-ins preserve UNSET, SET-EMPTY and SET-NONEMPTY."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from enterprise_kb.adapters.local._emulator import (
    FIRESTORE_EMULATOR_ENV,
    PUBSUB_EMULATOR_ENV,
    STORAGE_EMULATOR_ENV,
    firestore_emulator_host,
    pubsub_emulator_host,
    storage_emulator_host,
)
from enterprise_kb.envread import ConfiguredEmptyError


@pytest.mark.parametrize(
    ("name", "reader"),
    (
        (FIRESTORE_EMULATOR_ENV, firestore_emulator_host),
        (PUBSUB_EMULATOR_ENV, pubsub_emulator_host),
        (STORAGE_EMULATOR_ENV, storage_emulator_host),
    ),
)
def test_emulator_host_preserves_all_three_states(
    monkeypatch: pytest.MonkeyPatch, name: str, reader: Callable[[], str | None]
) -> None:
    monkeypatch.delenv(name, raising=False)
    assert reader() is None
    monkeypatch.setenv(name, "  ")
    with pytest.raises(ConfiguredEmptyError, match=name):
        reader()
    monkeypatch.setenv(name, " localhost:9999 ")
    assert reader() == "localhost:9999"
