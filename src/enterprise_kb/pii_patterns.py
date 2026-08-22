"""The one PII pattern source this repo uses, selected by configured jurisdiction (C4).

Everything that must agree about "what a national identifier looks like" reads its rows
from here, and this module reads them from the shared, versioned ``pii-kit`` package
(pinned by tag in ``pyproject.toml`` and by exact SHA in both lockfiles). Consumers:

* the ``local`` runtime redactor (:mod:`enterprise_kb.adapters.local.redaction`),
* the ``gcp`` DLP adapter's custom info types, via the RE2-safe form
  (:mod:`enterprise_kb.adapters.gcp.dlp_redaction`),
* the offline eval's ``pii_safety`` metric (``eval/run_eval.py``).

Because the redactor and the gate share one source, a fork that switches market changes
``pii.jurisdictions`` in ``config/settings.yaml`` and BOTH move together. Because the
gate ALSO scores a pack-independent planted-literal oracle (``pii_kit.planted_leak``),
narrowing or deleting a row cannot make the gate falsely green: the redactor stops
masking and the literal check fails. That two-part rule is the catalog-wide lesson from
the C4 rollout, not a local invention.

This module deliberately owns only the ORDER and the masking labels, which are
application-specific; the rows, validators and RE2 forms belong to the package.
"""

from __future__ import annotations

from collections.abc import Iterable

from pii_kit import (
    UNIVERSAL_PATTERNS,
    Pattern,
    national_patterns_for,
    re2_pattern_for,
)

__all__ = ["Pattern", "mask_for", "patterns_for", "re2_custom_info_types", "re2_pattern_for"]

#: Masking token per info type. A2 has shipped ``[NRIC]`` / ``[EMAIL]`` / ``[PHONE]``
#: since the first release and the demo evidence asserts on them, so the repo keeps its
#: own labels while sharing the pack's detection rows (the package documents masking
#: style as a consumer concern). Anything not listed falls back to the pack convention.
_MASKS: dict[str, str] = {
    "SG_NRIC_FIN": "[NRIC]",
    "EMAIL_ADDRESS": "[EMAIL]",
    "PHONE_NUMBER": "[PHONE]",
    "SG_PHONE": "[PHONE]",
}


def mask_for(info_type: str) -> str:
    """The replacement token for ``info_type``."""
    return _MASKS.get(info_type, f"[REDACTED:{info_type}]")


def patterns_for(jurisdictions: Iterable[str]) -> tuple[Pattern, ...]:
    """The ordered rows for ``jurisdictions``: universal rows first, then national.

    A2 indexes prose, not statements, so it carries no account-number row; the ordering
    caveat in ``pii_kit.patterns`` (account row before or after the national rows) does
    not arise here. Universal rows lead so an email or international phone is masked with
    its own label before a national digit-run row can claim part of it.
    """
    codes = tuple(str(j).strip().upper() for j in jurisdictions if str(j).strip())
    return (*UNIVERSAL_PATTERNS, *national_patterns_for(codes))


def re2_custom_info_types(jurisdictions: Iterable[str]) -> list[dict[str, object]]:
    """DLP ``custom_info_types`` for the national rows of ``jurisdictions``.

    DLP matches with RE2, which has no lookaround, so the pack's RE2-safe source is used
    rather than the Python pattern: a lookaround would make DLP reject the whole inspect
    config with INVALID_ARGUMENT and fail every managed call. Only the national rows are
    emitted; email and phone are DLP built-ins.
    """
    codes = tuple(str(j).strip().upper() for j in jurisdictions if str(j).strip())
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for info_type, pattern, _validator in national_patterns_for(codes):
        source = re2_pattern_for(info_type, pattern)
        key = f"{info_type}:{source}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "info_type": {"name": info_type},
                "regex": {"pattern": source},
                "likelihood": "POSSIBLE",
            }
        )
    return out
