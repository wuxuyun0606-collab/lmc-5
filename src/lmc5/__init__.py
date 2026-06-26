"""LMC-5 reference implementation."""

from .consolidation import ConsolidationResult, consolidate_events
from .fact_evolution import ZAuditResult, ZConflictCandidate, run_z_audit
from .hippocampus import HippocampusResult, MemoryCandidate, RelationPlan, run_hippocampus
from .models import (
    EventRecord,
    MemoryRecord,
    MetabolismSuggestion,
    RecallHit,
    RelationRecord,
    VectorRecord,
)
from .redact import redact_obj, redact_text
from .scoring import metabolic_gate, priority_score
from .store import MemoryStore
from .vector import cosine_similarity, toy_embed

__all__ = [
    "MemoryRecord",
    "MemoryStore",
    "ConsolidationResult",
    "HippocampusResult",
    "MemoryCandidate",
    "RelationPlan",
    "ZAuditResult",
    "ZConflictCandidate",
    "EventRecord",
    "VectorRecord",
    "cosine_similarity",
    "consolidate_events",
    "run_hippocampus",
    "run_z_audit",
    "toy_embed",
    "MetabolismSuggestion",
    "RecallHit",
    "RelationRecord",
    "priority_score",
    "metabolic_gate",
    "redact_obj",
    "redact_text",
]
