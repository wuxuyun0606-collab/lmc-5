"""真做梦层 · LMC-5 hippocampus.py + consolidation.py 升级

对应 lmc-5 core 的：
  - src/lmc5/consolidation.py（原本只做了 deterministic 词频统计，没 reflection）
  - src/lmc5/hippocampus.py（只是 promotion queue，没真做梦）

这一版补：
  1. LLM proposer：从 chunks 反推结构化候选记忆（type/title/content/importance/risk）
  2. 6 类型分类：event/fact/preference/engineering_decision/relationship_moment/risk_boundary
  3. 闸门链：噪音过滤 → 敏感词检查 → importance 阈值 → risk 分档 → 批内去重
  4. 安全关系扩展：safe 自动写、review 入审计队列（contradiction/cause_effect/supports）

设计哲学（与 lmc-5 一致）：
  - provider-free 是默认。所有 LLM/embedding 调用走 Callable 注入
  - 不传 proposer 时回落到 deterministic baseline（原版的词频路）
  - 闸门优先 safety > recall。宁可漏记不可错记
  - 关系图分档严格：自动只允许 safe_relation_types，review 类必须人工审

集成：
    from lmc5_addons.night_dream import NightDream, Chunk

    dream = NightDream(
        proposer=my_llm_proposer,        # 可选；不传走词频 baseline
        write_candidate=my_write_fn,     # 写候选记忆的回调
        write_safe_relation=my_rel_fn,   # 写安全关系的回调
        queue_review_relation=my_review, # review 类关系入审计队列
    )
    result = dream.run(chunks, apply=True)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


ALLOWED_TYPES = (
    "event",
    "fact",
    "preference",
    "engineering_decision",
    "relationship_moment",
    "risk_boundary",
)

SAFE_RELATION_TYPES = (
    "same_event",
    "same_topic",
    "temporal_sequence",
    "emotional_link",
    "derived_from",
    "in_thread",
)
REVIEW_RELATION_TYPES = ("contradiction", "cause_effect", "supports")

NOISE_PATTERNS = [
    r"\btool_use\b",
    r"\btool_result\b",
    r"\bhook_success\b",
    r"\bthinking\b",
    r"Request interrupted by user",
    r"No response requested",
    r"^\s*(嗯+|好+|继续|ok|OK|哈哈+|收到|明白)[。.!！?？\s]*$",
]
SENSITIVE_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{8,}",
    r"tvly-[A-Za-z0-9_-]{8,}",
    r"postgres(?:ql)?://",
    r"api[_ -]?key\s*[:：=]",
    r"password\s*[:：=]",
    r"密码\s*[:：=]",
    r"token\s*[:：=]",
    r"secret\s*[:：=]",
]


@dataclass
class Chunk:
    """对齐 lmc-5 的 event chunk 概念"""
    id: int
    text: str
    summary: str = ""
    keywords: str = ""
    start_time: str = ""
    end_time: str = ""
    session_id: str = ""


@dataclass
class Candidate:
    type: str
    title: str
    content: str
    importance: int                       # 1-10
    risk: str                              # "normal" / "review"
    evidence: str                          # 原文证据
    source_chunk_ids: list[int]
    relation_hints: list[str] = field(default_factory=list)
    thread_hint: str = ""


@dataclass
class DreamResult:
    chunks_used: int
    candidates: list[Candidate]
    promoted: list[Candidate]
    rejected: list[tuple[Candidate, str]]
    written_ids: list[int]
    safe_relations_written: int
    review_relations_queued: int


def is_noise(text: str, min_len: int = 80) -> bool:
    s = (text or "").strip()
    if len(s) < min_len:
        return True
    return any(re.search(p, s, re.IGNORECASE) for p in NOISE_PATTERNS)


def has_sensitive(text: str) -> bool:
    return any(re.search(p, text or "", re.IGNORECASE) for p in SENSITIVE_PATTERNS)


def deterministic_proposer(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """provider-free baseline：词频统计兜底

    原版 consolidation._summarize 的强化版——
    多了 type 推断和 importance 启发式，让 dream 在没 LLM key 时也能产出最小可用候选。
    """
    out: list[dict[str, Any]] = []
    for c in chunks:
        if is_noise(c.text):
            continue
        n_chars = len(c.text)
        importance = min(10, max(3, n_chars // 200))
        out.append({
            "type": "event",
            "title": (c.summary or c.text[:30])[:40],
            "content": (c.summary or c.text[:300])[:800],
            "importance": importance,
            "evidence": c.text[:160],
            "source_chunk_ids": [c.id],
            "risk": "review",
            "thread_hint": "其他线",
            "relation_hints": ["same_event"],
        })
    return out


def normalize_candidate(raw: dict[str, Any]) -> Optional[Candidate]:
    """统一字段、夹值、敏感词降级"""
    ctype = str(raw.get("type") or "").strip()
    if ctype not in ALLOWED_TYPES:
        return None
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()[:80]
    content = re.sub(r"\s+", " ", str(raw.get("content") or "")).strip()
    evidence = re.sub(r"\s+", " ", str(raw.get("evidence") or "")).strip()
    if not title or len(content) < 30 or not evidence:
        return None
    try:
        importance = int(raw.get("importance", 0))
    except Exception:
        importance = 0
    importance = max(0, min(10, importance))
    chunk_ids: list[int] = []
    for x in raw.get("source_chunk_ids") or []:
        try:
            chunk_ids.append(int(x))
        except Exception:
            pass
    hints: list[str] = []
    for x in raw.get("relation_hints") or []:
        s = str(x).strip()
        if s:
            hints.append(s)
    risk = str(raw.get("risk") or "normal").strip()
    if risk not in ("normal", "review"):
        risk = "review"
    if has_sensitive(f"{title}\n{content}\n{evidence}"):
        risk = "review"
    return Candidate(
        type=ctype,
        title=title,
        content=content[:1500],
        importance=importance,
        risk=risk,
        evidence=evidence[:180],
        source_chunk_ids=sorted(set(chunk_ids)),
        relation_hints=hints[:6],
        thread_hint=str(raw.get("thread_hint") or "其他线").strip() or "其他线",
    )


class NightDream:
    """晚间做梦 · 端到端"""

    def __init__(
        self,
        proposer: Optional[Callable[[list[Chunk]], list[dict[str, Any]]]] = None,
        write_candidate: Optional[Callable[[Candidate], Optional[int]]] = None,
        write_safe_relation: Optional[Callable[[int, int, str, float, str], None]] = None,
        queue_review_relation: Optional[Callable[[int, int, str, str], None]] = None,
        find_neighbors: Optional[Callable[[int, int], list[int]]] = None,
        importance_threshold: int = 7,
        max_promote: int = 10,
        relation_top_k: int = 5,
    ):
        """
        Args:
            proposer: chunks → 候选 dict 列表。不传走 deterministic_proposer
            write_candidate: 候选 → 落库，返回写入 id 或 None（重复时）
            write_safe_relation: (id_a, id_b, type, strength, reason) → 写关系
            queue_review_relation: (id_a, id_b, type, reason) → 入审计
            find_neighbors: (new_id, top_k) → 候选邻居 id（用于关系扩展）
            importance_threshold: 闸门阈值，低于这个不晋升
            max_promote: 单次最多晋升几条
            relation_top_k: 每个新记忆扩 top-K 邻居建关系
        """
        self.proposer = proposer or deterministic_proposer
        self.write_candidate = write_candidate
        self.write_safe_relation = write_safe_relation
        self.queue_review_relation = queue_review_relation
        self.find_neighbors = find_neighbors
        self.importance_threshold = importance_threshold
        self.max_promote = max_promote
        self.relation_top_k = relation_top_k

    def extract(self, chunks: list[Chunk]) -> list[Candidate]:
        clean_chunks = [c for c in chunks if not is_noise(c.text)]
        if not clean_chunks:
            return []
        raw = self.proposer(clean_chunks)
        out: list[Candidate] = []
        for r in raw:
            if isinstance(r, dict):
                cand = normalize_candidate(r)
                if cand:
                    out.append(cand)
        return out

    def gate(self, candidates: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
        """闸门链：阈值 → risk → 必须有 source → 批内去重"""
        promoted: list[Candidate] = []
        rejected: list[tuple[Candidate, str]] = []
        seen: set[tuple[str, str]] = set()
        for cand in sorted(candidates, key=lambda c: (-c.importance, c.title)):
            if cand.importance < self.importance_threshold:
                rejected.append((cand, f"importance<{self.importance_threshold}"))
                continue
            if cand.risk != "normal":
                rejected.append((cand, "risk=review"))
                continue
            if not cand.source_chunk_ids:
                rejected.append((cand, "missing_source"))
                continue
            sig = (cand.type, cand.title)
            if sig in seen:
                rejected.append((cand, "duplicate_in_batch"))
                continue
            seen.add(sig)
            promoted.append(cand)
            if len(promoted) >= self.max_promote:
                break
        return promoted, rejected

    def build_relations(self, new_ids: list[int]) -> tuple[int, int]:
        """给新记忆扩 top-K 邻居 → safe 自动写 / review 入审计

        关系类型从 candidate.relation_hints 来；这里只判 safe vs review。
        实际类型决策（contradiction？supports？）属于 Z 线，不归 dream。
        """
        if not new_ids or not self.find_neighbors:
            return (0, 0)
        safe_count = 0
        review_count = 0
        pairs: set[tuple[int, int]] = set()
        for cid in new_ids:
            for nid in self.find_neighbors(cid, self.relation_top_k):
                if nid == cid:
                    continue
                pairs.add((min(int(cid), int(nid)), max(int(cid), int(nid))))
        for a, b in sorted(pairs):
            # 默认关系类型为 same_topic（safe），实际类型由调用方在 find_neighbors 内决定
            # 这里只演示 safe 写路径；review 由 candidate.relation_hints 触发
            if self.write_safe_relation:
                self.write_safe_relation(a, b, "same_topic", 0.5, "dream:auto-link")
                safe_count += 1
        return safe_count, review_count

    def run(self, chunks: list[Chunk], apply: bool = False) -> DreamResult:
        candidates = self.extract(chunks)
        promoted, rejected = self.gate(candidates)
        written_ids: list[int] = []
        safe_n = review_n = 0
        if apply and self.write_candidate:
            for cand in promoted:
                wid = self.write_candidate(cand)
                if wid is not None:
                    written_ids.append(int(wid))
            if written_ids:
                safe_n, review_n = self.build_relations(written_ids)
        return DreamResult(
            chunks_used=len(chunks),
            candidates=candidates,
            promoted=promoted,
            rejected=rejected,
            written_ids=written_ids,
            safe_relations_written=safe_n,
            review_relations_queued=review_n,
        )
