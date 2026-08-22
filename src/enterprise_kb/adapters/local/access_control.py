"""Local access-control adapter (AccessControlPort) : in-process principal directory.

The ``local`` profile's stand-in for **IAM / directory groups**: a small, deterministic
in-process map from principal ids to the ACL tag labels they may see. It is seeded from
the built-in synthetic directory (:data:`enterprise_kb.adapters.local._seed.PRINCIPAL_TAGS`)
so a CLI run with ``--principals user:jane@bank.test`` resolves to real tags and the
seeded passages are admitted by the domain ACL filter (P-09).

A principal that is not in the directory resolves to **no tags**: the domain treats an
empty tag set as access-denied (fail-closed), so an unknown caller sees nothing. SDK-free
and unconditional (there is no emulator for the directory).
"""

from __future__ import annotations

from ...config import Settings
from ._seed import PRINCIPAL_TAGS


class LocalAccessControlAdapter:
    """Resolve principals to ACL tag labels from an in-process directory (fail-closed)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Copy so a caller mutating the result never corrupts the shared seed directory.
        self._directory: dict[str, set[str]] = {k: set(v) for k, v in PRINCIPAL_TAGS.items()}

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        """Expand principal ids into the union of ACL tag labels they may see."""
        tags: set[str] = set()
        for principal in principals:
            tags |= self._directory.get(principal, set())
        return tags

    def grant(self, principal: str, tags: set[str]) -> None:
        """Seed knob: add ``tags`` to ``principal`` (used by tests / local setup)."""
        self._directory.setdefault(principal, set()).update(tags)
