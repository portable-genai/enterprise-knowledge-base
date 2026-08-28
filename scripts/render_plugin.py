#!/usr/bin/env python3
"""Render this repo's Agent Plugins 1.0.0 directory from what it already declares.

Nothing here is hand-authored. Identity comes from the A2A agent card the repo already
publishes, keywords from the governed tool catalog, and ``skills/`` from ``.agents/skills``. A
manifest typed out by hand would be a second description of the service, and a second
description is one that can be wrong.

Agent Plugins packages TOOLING and carries no data-portability mechanism, so nothing here
touches the evidence trail: the ledger keeps its own export format, and a plugin only ever
REACHES it through the kit's read-only tools.

Run it with ``make plugin``; the output is build output and is not committed.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from hex_service_kit.plugin import (
    Author,
    PluginSpec,
    StdioServer,
    keywords_from_skill_ids,
    render,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "dist" / "plugin"


def build_spec() -> PluginSpec:
    """Assemble the spec from this repo's own declarations, never from literals."""
    from enterprise_kb.adapters.gcp.mcp_tool_catalog import McpToolCatalogAdapter
    from enterprise_kb.agent.agent_card import agent_card_document
    from enterprise_kb.config import Settings

    settings = Settings.load()
    card = agent_card_document(settings)
    catalog = McpToolCatalogAdapter(settings)

    def _field(name: str) -> str:
        if isinstance(card, dict):
            return str(card.get(name) or "")
        return str(getattr(card, name, "") or "")

    return PluginSpec(
        name="enterprise-knowledge-base",
        version=_field("version") or "0.0.1",
        description=_field("description"),
        license="Apache-2.0",
        repository="https://github.com/portable-genai/enterprise-knowledge-base",
        # The card's skills are CAPABILITIES and reach a client as MCP tools through mcp.json,
        # not as files. They land in the manifest only as keywords.
        keywords=keywords_from_skill_ids([spec.name for spec in catalog.list_tools()]),
        author=Author(name="portable-genai"),
        servers={
            "enterprise-knowledge-base": StdioServer(
                command="python",
                args=("-m", "enterprise_kb.mcp"),
                cwd="${PLUGIN_ROOT}",
            )
        },
        skills_source=REPO_ROOT / ".agents" / "skills",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=pathlib.Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)
    report = render(build_spec(), args.dest)
    print(f"rendered {report.root}: {len(report.skills)} skills, {len(report.servers)} server(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
