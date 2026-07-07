"""Quality checks for hippocampus memory atoms.

The checks are deterministic by design. They are meant for dry-run audits and
CI fixtures, not for judging nuanced production memories by themselves.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


COARSE_PATTERNS = (
    re.compile(r"\bEvent chunk \d+-\d+\b", re.I),
    re.compile(r"\b\d+\s+events?\b", re.I),
    re.compile(r"\bFirst:\b.*\bLast:\b", re.I | re.S),
    re.compile(r"\bKeywords:\b", re.I),
)
REWRITE_PATTERNS = (
    re.compile(r"\bHippocampus observation from chunk\b", re.I),
    re.compile(r"\bObservation from event chunk\b", re.I),
    re.compile(r"\bevent_chunks\.id=\d+\b", re.I),
    re.compile(r"\bevents=\d+-\d+\b", re.I),
)
NOISE_PATTERNS = (
    re.compile(r"\b(tool_use|tool_result|hook_success|traceback|stack trace)\b", re.I),
    re.compile(r"\bNo response requested\b", re.I),
    re.compile(r"\bRequest interrupted by user\b", re.I),
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class AtomQualityAssessment:
    label: str
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return self.label == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "flags": self.flags,
            "reasons": self.reasons,
            "passes": self.passes,
        }


@dataclass(frozen=True)
class AtomQualityReport:
    total: int
    labels: dict[str, int]
    flags: dict[str, int]
    ratios: dict[str, float]
    items: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "labels": self.labels,
            "flags": self.flags,
            "ratios": self.ratios,
            "items": self.items,
        }


def _text(candidate: Mapping[str, Any], key: str) -> str:
    return " ".join(str(candidate.get(key) or "").split())


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def assess_atom_quality(candidate: Mapping[str, Any]) -> AtomQualityAssessment:
    """Return a coarse deterministic quality label for one candidate atom."""

    title = _text(candidate, "title")
    content = _text(candidate, "content")
    evidence = _text(candidate, "evidence")
    combined = "\n".join(part for part in (title, content, evidence) if part)

    flags: list[str] = []
    reasons: list[str] = []

    if not title or not content:
        flags.append("needs_rewrite")
        reasons.append("missing title/content")

    if any(pattern.search(combined) for pattern in COARSE_PATTERNS):
        flags.append("too_coarse")
        reasons.append("contains chunk metadata instead of an atomic memory")

    if len(content) > 520:
        flags.append("too_coarse")
        reasons.append("content is too long for a single recall atom")

    if _word_count(content) < 5:
        flags.append("needs_rewrite")
        reasons.append("content is too short to stand alone")

    if any(pattern.search(combined) for pattern in REWRITE_PATTERNS):
        flags.append("needs_rewrite")
        reasons.append("title/evidence exposes pipeline scaffolding")

    if any(pattern.search(combined) for pattern in NOISE_PATTERNS):
        flags.append("needs_rewrite")
        reasons.append("contains tool or interruption noise")

    if title.lower().startswith(("hippocampus observation", "observation from event chunk")):
        flags.append("needs_rewrite")
        reasons.append("generic pipeline title")

    source_chunk_ids = candidate.get("source_chunk_ids") or []
    if isinstance(source_chunk_ids, Sequence) and not isinstance(source_chunk_ids, (str, bytes)):
        if len(source_chunk_ids) > 1:
            flags.append("too_coarse")
            reasons.append("candidate spans multiple source chunks")

    deduped_flags = list(dict.fromkeys(flags))
    deduped_reasons = list(dict.fromkeys(reasons))
    if "too_coarse" in deduped_flags:
        label = "too_coarse"
    elif "needs_rewrite" in deduped_flags:
        label = "needs_rewrite"
    else:
        label = "pass"
    return AtomQualityAssessment(label=label, flags=deduped_flags, reasons=deduped_reasons)


def build_atom_quality_report(
    candidates: Sequence[Mapping[str, Any]],
    *,
    sample_limit: int = 10,
) -> AtomQualityReport:
    """Assess candidate atoms and return aggregate counts plus sample items."""

    assessed: list[dict[str, Any]] = []
    labels: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    for candidate in candidates:
        assessment = assess_atom_quality(candidate)
        labels[assessment.label] += 1
        for flag in assessment.flags:
            flags[flag] += 1
        if len(assessed) < sample_limit or not assessment.passes:
            assessed.append(
                {
                    "title": _text(candidate, "title"),
                    "content": _text(candidate, "content"),
                    "source_chunk_ids": candidate.get("source_chunk_ids") or [],
                    "assessment": assessment.to_dict(),
                }
            )

    total = len(candidates)
    for key in ("pass", "too_coarse", "needs_rewrite"):
        labels.setdefault(key, 0)
    for key in ("too_coarse", "needs_rewrite"):
        flags.setdefault(key, 0)
    ratios = {
        key: round(flags[key] / total, 4) if total else 0.0
        for key in ("too_coarse", "needs_rewrite")
    }
    ratios["pass"] = round(labels["pass"] / total, 4) if total else 0.0
    return AtomQualityReport(
        total=total,
        labels=dict(labels),
        flags=dict(flags),
        ratios=ratios,
        items=assessed[:sample_limit],
    )
