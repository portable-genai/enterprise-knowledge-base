#!/usr/bin/env python3
"""Preview or apply a conservative mechanical rename of an enterprise-knowledge-base fork."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_OLD_PACKAGE = "enterprise_kb"
_OLD_STEM = "enterprise-knowledge-base"
_OLD_ENV_PREFIX = "KB_"
_SKIP_DIRS = {
    ".agents",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "out",
}
_TEXT_SUFFIXES = {
    "",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".mjs",
    ".py",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_TOOL_FILES = {Path("scripts/rename_fork.py"), Path("tests/unit/test_rename_fork.py")}


def _iter_files(include_docs: bool) -> list[Path]:
    files = []
    for path in _ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(_ROOT)
        if relative in _TOOL_FILES or any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix not in _TEXT_SUFFIXES:
            continue
        if not include_docs and path.suffix in {".md", ".html"}:
            continue
        files.append(path)
    return files


def _rewrite_text(
    text: str,
    *,
    package: str,
    stem: str,
    env_prefix: str,
) -> tuple[str, int]:
    prefix = env_prefix.rstrip("_").upper() + "_"
    count = 0
    for old, new in ((_OLD_PACKAGE, package), (_OLD_STEM, stem)):
        changed = text.count(old)
        text = text.replace(old, new)
        count += changed
    text, changed = re.subn(rf"\b{re.escape(_OLD_ENV_PREFIX)}(?=[A-Z0-9])", prefix, text)
    return text, count + changed


def _preflight_package_rename(new_package: str) -> tuple[Path, Path]:
    source = _ROOT / "src" / _OLD_PACKAGE
    destination = _ROOT / "src" / new_package
    if source != destination and destination.exists():
        raise RuntimeError(f"refusing rename: destination package already exists: {destination}")
    if not source.exists():
        raise RuntimeError(f"refusing rename: source package does not exist: {source}")
    return source, destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rename an enterprise-knowledge-base institutional fork."
    )
    parser.add_argument("--package", required=True)
    parser.add_argument(
        "--stem",
        required=True,
        help="Shared distribution, CLI and resource stem replacing enterprise-knowledge-base.",
    )
    parser.add_argument("--env-prefix", required=True)
    parser.add_argument("--include-docs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", args.package):
        parser.error("--package must be a valid snake_case identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.stem):
        parser.error("--stem must be a lowercase kebab-case name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.env_prefix.rstrip("_")):
        parser.error("--env-prefix must contain only letters, digits and underscores")

    source, destination = _preflight_package_rename(args.package)
    apply_changes = args.yes and not args.dry_run
    print(f"  {_OLD_PACKAGE!r} -> {args.package!r}")
    print(f"  {_OLD_STEM!r} -> {args.stem!r}")
    print(f"  {_OLD_ENV_PREFIX!r} -> {args.env_prefix.rstrip('_').upper() + '_'!r}")

    touched: list[tuple[Path, int]] = []
    for path in _iter_files(args.include_docs):
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rewritten, count = _rewrite_text(
            original,
            package=args.package,
            stem=args.stem,
            env_prefix=args.env_prefix,
        )
        if count:
            touched.append((path, count))
            if apply_changes:
                path.write_text(rewritten, encoding="utf-8")

    print(
        f"{'Edited' if apply_changes else 'Would edit'} {len(touched)} file(s), "
        f"{sum(count for _, count in touched)} replacement(s)."
    )
    if source != destination:
        print(f"{'Renaming' if apply_changes else 'Would rename'} {source} -> {destination}")
        if apply_changes:
            source.rename(destination)
    if not apply_changes:
        print("No files were written. Re-run with --yes after reviewing the preview.")
    else:
        print("Rename complete. Recreate the environment and run the full adoption gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
