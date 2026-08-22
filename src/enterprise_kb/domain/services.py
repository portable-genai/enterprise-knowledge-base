"""Aggregator re-exporting the two orchestration services.

The services live one-per-module (``kb_service``, ``ingestion_service``) so each stays
focused. This module is the single import surface the wiring layers (``api.deps``,
``agent.tools``, ``cli``) use, so they never need to know which module a service lives in.
"""

from __future__ import annotations

from .ingestion_service import IngestionService
from .kb_service import KnowledgeBaseService

__all__ = [
    "KnowledgeBaseService",
    "IngestionService",
]
