"""On-prem placeholder for ``AccessControlPort`` : the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the directory-provisioned, tenant-scoped AlloyDB adapter; switching
``profile`` to ``onprem`` rebinds it here. ``resolve`` deliberately raises rather than returning an
empty (or wide-open) tag set: an unimplemented access-control resolver must never
silently grant or deny visibility, so porting on-premise *must* supply a real
principal-to-tags resolver against the enterprise directory. Filling this body in is the
only change required; the ACL-filtering decision stays in the domain (P-09).
"""

from __future__ import annotations

from ...config import Settings

_MESSAGE = (
    "On-prem AccessControlPort adapter is a migration placeholder; implement against "
    "your on-premise directory. Core domain logic is unchanged."
)


class OnPremAccessControlAdapter:
    """Placeholder access-control adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(self, principals: list[str], tenant: str) -> set[str]:
        raise NotImplementedError(_MESSAGE)
