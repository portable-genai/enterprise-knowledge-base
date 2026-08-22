from pathlib import Path


def _read(name: str) -> str:
    return (Path(__file__).parents[2] / name).read_text(encoding="utf-8")


def test_documentation_authority_order_is_declared_and_reachable() -> None:
    """G1: the order is stated in one place and pointed at from every top document."""
    authority = _read("docs/doc-authority.md")
    for rank, doc in enumerate(
        ["SPEC.md", "ARCHITECTURE.md", "COMPLIANCE.md", "README.md"], start=1
    ):
        assert f"| {rank} |" in authority, f"rank {rank} row missing"
        assert doc in authority
    for doc in ("README.md", "SPEC.md", "ARCHITECTURE.md", "COMPLIANCE.md"):
        assert "docs/doc-authority.md" in _read(doc), f"{doc} does not point at the order"


def test_no_top_document_calls_a_shipped_feature_unbuilt() -> None:
    """G1 'kept true': staleness is a bug, so the claim is mechanised, not just asserted."""
    shipped = ("grounded answer", "tenant", "portability", "hash chain")
    for doc in ("SPEC.md", "ARCHITECTURE.md", "COMPLIANCE.md", "README.md"):
        text = _read(doc).lower()
        for feature in shipped:
            for stale in ("not yet built", "forthcoming", "not implemented"):
                assert f"{feature} is {stale}" not in text, f"{doc}: {feature} marked {stale}"


def test_compliance_has_an_adopter_owned_regulator_crosswalk() -> None:
    """G2: the crosswalk exists AND names who owns it."""
    compliance = _read("COMPLIANCE.md")
    assert "Regulator crosswalk (adopter-owned)" in compliance
    assert "the adopting bank, not this repository" in compliance
    assert "How to fork this appendix for another regulator" in compliance


def test_contributing_covers_the_full_extension_touch_list() -> None:
    """G6: both walkthroughs exist and name the files the contract test enforces."""
    contributing = _read("CONTRIBUTING.md")
    assert "Adding an ADAPTER" in contributing
    assert "Adding a PORT" in contributing
    for touchpoint in (
        "config/settings.yaml",
        "PORT_PROTOCOLS",
        "ports/__init__.py",
        "adapters/onprem/",
        "test_port_protocols_matches_settings_adapters",
        "tests/contract/test_behavioral_parity.py",
    ):
        assert touchpoint in contributing, f"CONTRIBUTING omits {touchpoint}"
