from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "rename_fork.py"
_SPEC = importlib.util.spec_from_file_location("rename_fork", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_rename_rewrites_package_stem_and_env_prefix() -> None:
    rewritten, count = _MODULE._rewrite_text(
        "enterprise_kb enterprise-knowledge-base KB_PROFILE",
        package="bank_knowledge",
        stem="bank-knowledge",
        env_prefix="BANK_KB",
    )
    assert count == 3
    assert rewritten == "bank_knowledge bank-knowledge BANK_KB_PROFILE"


def test_apply_preflights_collision_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / _MODULE._OLD_PACKAGE
    destination = tmp_path / "src" / "bank_knowledge"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    config = tmp_path / "settings.py"
    original = 'PROFILE = "KB_PROFILE"\n'
    config.write_text(original)
    monkeypatch.setattr(_MODULE, "_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rename_fork.py",
            "--package",
            "bank_knowledge",
            "--stem",
            "bank-knowledge",
            "--env-prefix",
            "BANK_KB",
            "--yes",
        ],
    )

    with pytest.raises(RuntimeError, match="destination package already exists"):
        _MODULE.main()
    assert config.read_text() == original


def test_preview_is_non_mutating_and_apply_moves_package_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / _MODULE._OLD_PACKAGE
    source.mkdir(parents=True)
    module = source / "__init__.py"
    original = 'SERVICE = "enterprise-knowledge-base"\nENV = "KB_PROFILE"\n'
    module.write_text(original)
    monkeypatch.setattr(_MODULE, "_ROOT", tmp_path)
    arguments = [
        "rename_fork.py",
        "--package",
        "bank_knowledge",
        "--stem",
        "bank-knowledge",
        "--env-prefix",
        "BANK_KB",
    ]

    monkeypatch.setattr(sys, "argv", [*arguments, "--dry-run"])
    assert _MODULE.main() == 0
    assert module.read_text() == original
    assert source.exists()

    monkeypatch.setattr(sys, "argv", [*arguments, "--yes"])
    assert _MODULE.main() == 0
    destination = tmp_path / "src" / "bank_knowledge"
    assert not source.exists()
    assert destination.is_dir()
    assert (destination / "__init__.py").read_text() == (
        'SERVICE = "bank-knowledge"\nENV = "BANK_KB_PROFILE"\n'
    )


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--package", "bad-package"),
        ("--stem", "Bad Stem"),
        ("--env-prefix", "BANK KB"),
    ],
)
def test_invalid_output_names_fail_before_writes(
    flag: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / _MODULE._OLD_PACKAGE
    source.mkdir(parents=True)
    config = tmp_path / "settings.py"
    original = 'PROFILE = "KB_PROFILE"\n'
    config.write_text(original)
    monkeypatch.setattr(_MODULE, "_ROOT", tmp_path)
    arguments = [
        "rename_fork.py",
        "--package",
        "bank_knowledge",
        "--stem",
        "bank-knowledge",
        "--env-prefix",
        "BANK_KB",
        "--yes",
    ]
    arguments[arguments.index(flag) + 1] = value
    monkeypatch.setattr(sys, "argv", arguments)

    with pytest.raises(SystemExit, match="2"):
        _MODULE.main()
    assert config.read_text() == original
    assert source.exists()
    assert not (tmp_path / "src" / "bank_knowledge").exists()
