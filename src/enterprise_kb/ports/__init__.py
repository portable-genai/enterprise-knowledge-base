"""Ports : the abstract interfaces (the hexagon boundary).

Every port is a ``typing.Protocol`` so adapters need only structural conformance and
contract tests can verify any adapter (GCP, remote-platform, or on-prem placeholder)
satisfies the same contract.
"""

from .citations import CitationStorePort
from .generation import GroundingPort, LLMPort
from .governance import AgentRegistryPort, ToolCatalogPort
from .identity import EndUserAuthUnavailableError, IdentityPort
from .ingestion import FreshnessLedgerPort, IngestionPort
from .observability import (
    AuditSinkPort,
    EvaluationGatePort,
    ObservabilityTracerPort,
    TokenUsage,
)
from .retrieval import AccessControlPort, RetrievalPort
from .runtime import AgentRuntimePort, MemoryPort, SessionPort
from .safety import GuardrailPort, PIIRedactionPort

__all__ = [
    "RetrievalPort",
    "AccessControlPort",
    "IngestionPort",
    "FreshnessLedgerPort",
    "CitationStorePort",
    "LLMPort",
    "GroundingPort",
    "GuardrailPort",
    "PIIRedactionPort",
    "AgentRuntimePort",
    "SessionPort",
    "MemoryPort",
    "AuditSinkPort",
    "ObservabilityTracerPort",
    # The tracer's value type travels with the port, so a call site binding the port never has
    # to guess which TokenUsage it is holding.
    "TokenUsage",
    "EvaluationGatePort",
    "AgentRegistryPort",
    "ToolCatalogPort",
    "IdentityPort",
    "EndUserAuthUnavailableError",
]
