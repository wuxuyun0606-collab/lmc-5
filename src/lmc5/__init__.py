"""LMC-5 reference implementation."""

from .consolidation import ConsolidationResult, consolidate_events
from .models import (
    EventRecord,
    MemoryRecord,
    MetabolismSuggestion,
    RecallHit,
    RelationRecord,
    VectorRecord,
)
from .redact import redact_obj, redact_text
from .scoring import priority_score
from .store import MemoryStore
from .vector import cosine_similarity, toy_embed

__all__ = [
    "MemoryRecord",
    "MemoryStore",
    "ConsolidationResult",
    "EventRecord",
    "VectorRecord",
    "cosine_similarity",
    "consolidate_events",
    "toy_embed",
    "MetabolismSuggestion",
    "RecallHit",
    "RelationRecord",
    "priority_score",
    "redact_obj",
    "redact_text",
]
