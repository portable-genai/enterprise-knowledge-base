#!/usr/bin/env python3
"""Render reviewed fictional managed-demo control artifacts without cloud access."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "deploy" / "managed-demo"
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+$")


def render(*, raw_bucket: str, user_email: str, output_dir: Path) -> None:
    """Render fixed-schema registry and ACL files from reviewed synthetic templates."""
    if not _BUCKET.fullmatch(raw_bucket):
        raise ValueError("raw bucket must be an explicit valid GCS bucket name")
    if not _EMAIL.fullmatch(user_email):
        raise ValueError("demo user must be an explicit email address")
    normalized_email = user_email.lower()
    _, _, tenant = normalized_email.partition("@")
    if not tenant:
        raise ValueError("demo user email must carry the tenant domain resolved by IAP")

    registry = (TEMPLATES / "registry.template.yaml").read_text(encoding="utf-8")
    registry = registry.replace("__RAW_SOURCE_BUCKET__", raw_bucket)
    acl = json.loads((TEMPLATES / "acl-bindings.template.json").read_text(encoding="utf-8"))
    binding = acl["bindings"][0]
    binding["tenant"] = tenant
    binding["principal_id"] = f"user:{normalized_email}"

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "registry.yaml").write_text(registry, encoding="utf-8")
    (output_dir / "bindings.json").write_text(
        json.dumps(acl, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-bucket", required=True)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    render(
        raw_bucket=args.raw_bucket,
        user_email=args.user_email,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
