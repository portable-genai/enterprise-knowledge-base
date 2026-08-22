"""Local PII redaction adapter (PIIRedactionPort) : regex de-identification.

The ``local`` profile's stand-in for **Sensitive Data Protection / DLP**. It masks the
national identifiers of the CONFIGURED jurisdictions (``pii.jurisdictions``) plus the
universal email/phone rows, using the shared ``pii-kit`` rows re-exported by
:mod:`enterprise_kb.pii_patterns` (C4). There is no Google emulator for DLP, so this path
is unconditional and imports no google-cloud package.

The rows are the SAME object the eval's ``pii_safety`` metric scans with, so a narrowed
or deleted row shows up as a red gate rather than as a silent stop-masking.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import RedactionFinding, RedactionResult
from ...pii_patterns import mask_for, patterns_for


class LocalRegexRedactionAdapter:
    """Mask the configured jurisdictions' identifiers, like DLP de-identify."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Materialise once: the row list is the adapter's contract with the eval gate.
        self._patterns = patterns_for(settings.pii.jurisdictions)

    def redact(self, text: str) -> RedactionResult:
        findings: list[RedactionFinding] = []
        redacted = text or ""

        for info_type, pattern, validator in self._patterns:
            hits = [m for m in pattern.finditer(redacted) if validator is None or validator(m[0])]
            if not hits:
                continue
            replacement = mask_for(info_type)
            redacted = pattern.sub(
                lambda m, _r=replacement, _v=validator: (  # type: ignore[misc]
                    _r if (_v is None or _v(m[0])) else m[0]
                ),
                redacted,
            )
            findings.append(RedactionFinding(info_type=info_type, count=len(hits)))

        return RedactionResult(text=redacted, findings=tuple(findings))
