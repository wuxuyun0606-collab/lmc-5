"""Explainable scoring for LMC-5 recall and patrol decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
    get = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)

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
