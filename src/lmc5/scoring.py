"""Explainable scoring for LMC-5 recall and patrol decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

RISK_BONUS = {"normal": 0.0, "medium": 0.3, "high": 0.8}
URGENCY_BONUS = {"low": 0.0, "normal": 0.1, "high": 0.5}
STATUS_BONUS = {
    "current": 0.2,
    "review": 0.0,
    "historical": -0.2,
    "candidate_thread": -0.1,
    "superseded": -1.0,
    "archived": -1.2,
}

GateMode = Literal["recall", "surface"]

NOISE_SOURCES = {
    "debug",
    "log",
    "logs",
    "scratch",
    "temp",
    "transient",
    "working",
    "worklog",
}
NOISE_CATEGORIES = {
    "debug",
    "log",
    "logs",
    "scratch",
    "temp",
    "transient",
    "working",
    "worklog",
}
SURFACE_BLOCK_SOURCES = NOISE_SOURCES | {"conversation", "raw", "raw_event"}
SURFACE_BLOCK_CATEGORIES = NOISE_CATEGORIES | {"conversation", "raw", "raw_event"}
PROTECTED_CATEGORIES = {"core", "heartbeat", "identity", "important"}
PROTECTED_SOURCES = {"heartbeat", "identity"}


def _record_get(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_days(value: str | None, now: datetime | None = None) -> float | None:
    created = _parse_datetime(value)
    if created is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max((now - created).total_seconds() / 86400, 0.0)


def freshness_bonus(created_at: str | None, now: datetime | None = None) -> float:
    created = _parse_datetime(created_at)
    if created is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    age_days = max((now - created).total_seconds() / 86400, 0)
    if age_days <= 7:
        return 0.5
    if age_days <= 30:
        return 0.2
    return 0.0


def priority_score(record: Any, now: datetime | None = None) -> float:
    """Return a transparent priority score for a record-like object."""
    get = (
        record.get
        if isinstance(record, dict)
        else lambda key, default=None: getattr(record, key, default)
    )

    score = 1.0
    score += RISK_BONUS.get(get("risk_level", "normal"), 0.0)
    score += URGENCY_BONUS.get(get("urgency", "normal"), 0.0)
    score += STATUS_BONUS.get(get("status", "current"), 0.0)
    score += freshness_bonus(get("created_at"), now=now)
    score += min(int(get("hit_count", 0) or 0) * 0.1, 1.0)

    growth_delta = get("growth_delta", "")
    if growth_delta:
        score += 0.3

    tension = get("tension", None)
    confidence = get("confidence", None)
    if tension is not None and tension >= 0.6:
        score += 0.3
    if tension is not None and tension >= 0.6 and confidence is not None and confidence < 0.6:
        score -= 0.5

    return round(score, 3)


def metabolic_gate(
    record: Any,
    *,
    mode: GateMode = "recall",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the M-axis gate for recall or spontaneous surfacing.

    M is not only decay. It also decides whether a memory is allowed to enter
    an output channel. Explicit recall is intentionally lenient; surfacing is
    strict because it interrupts the agent's working context.
    """
    if mode not in {"recall", "surface"}:
        raise ValueError("mode must be 'recall' or 'surface'")

    status = _norm(_record_get(record, "status", "current"))
    source = _norm(_record_get(record, "source", ""))
    category = _norm(_record_get(record, "category", ""))
    hit_count = int(_record_get(record, "hit_count", 0) or 0)
    risk_level = _norm(_record_get(record, "risk_level", "normal"))
    urgency = _norm(_record_get(record, "urgency", "normal"))
    age = _age_days(_record_get(record, "created_at"), now=now)

    if status != "current":
        return {
            "bucket": "quarantine",
            "allowed": False,
            "factor": 0.0,
            "reason": f"status:{status}",
        }

    protected = (
        source in PROTECTED_SOURCES
        or category in PROTECTED_CATEGORIES
        or risk_level == "high"
        or urgency == "high"
    )
    if protected:
        return {
            "bucket": "retain",
            "allowed": True,
            "factor": 1.0,
            "reason": "protected",
        }

    if source in NOISE_SOURCES:
        return {
            "bucket": "quarantine",
            "allowed": False,
            "factor": 0.0,
            "reason": f"noise_source:{source}",
        }
    if category in NOISE_CATEGORIES:
        return {
            "bucket": "quarantine",
            "allowed": False,
            "factor": 0.0,
            "reason": f"noise_category:{category}",
        }

    priority = priority_score(record, now=now)
    bucket = "retain"
    factor = 1.0
    reason = "retain"

    if age is not None and age >= 90 and hit_count == 0 and priority <= 1.2:
        bucket = "cold"
        factor = 0.45
        reason = "old_unhit_low_priority"
    elif priority < 0.8 and hit_count == 0:
        bucket = "quarantine"
        factor = 0.0
        reason = "low_priority_unhit"
    elif priority < 1.0:
        bucket = "cold"
        factor = 0.6
        reason = "low_priority"

    allowed = factor > 0
    if mode == "surface":
        if bucket != "retain":
            return {
                "bucket": bucket,
                "allowed": False,
                "factor": 0.0,
                "reason": f"surface_blocks_{reason}",
            }
        if source in SURFACE_BLOCK_SOURCES:
            return {
                "bucket": "quarantine",
                "allowed": False,
                "factor": 0.0,
                "reason": f"surface_block_source:{source}",
            }
        if category in SURFACE_BLOCK_CATEGORIES:
            return {
                "bucket": "quarantine",
                "allowed": False,
                "factor": 0.0,
                "reason": f"surface_block_category:{category}",
            }

    return {
        "bucket": bucket,
        "allowed": allowed,
        "factor": factor,
        "reason": reason,
    }
