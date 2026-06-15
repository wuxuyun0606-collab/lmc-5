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
    "same_person",
    "in_episode",
    "instance_of",
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


DEFAULT_HIPPOCAMPUS_PROMPT = None  # 延迟构造，避免 import 循环


def make_hippocampus_prompt(chunks: list) -> str:
    """提供给 LLM proposer 用的标准 prompt 模板。

    嵌入反幻觉铁律 + hippocampus 特定提醒，强制要求 evidence 字段从原文一字不差引用。
    用户写自己的 proposer 时直接调这个函数即可。
    """
    from .anti_hallucination import (
        ANTI_HALLUCINATION_HEADER,
        HIPPOCAMPUS_TASK_REMINDERS,
    )

    items = []
    for c in chunks:
        items.append(
            f"[chunk_id={c.id} session={c.session_id} "
            f"time={c.start_time}..{c.end_time}]\n"
            f"summary: {c.summary[:700]}\n"
            f"keywords: {c.keywords[:240]}\n"
            f"content: {c.text[:900]}"
        )
    items_text = "\n\n".join(items)

    return ANTI_HALLUCINATION_HEADER + HIPPOCAMPUS_TASK_REMINDERS + f"""

任务：从以下 conversation chunks 里筛出值得长期记住的候选记忆。

只能输出 JSON，不要解释。格式：
{{
  "candidates": [
    {{
      "type": "event|fact|preference|engineering_decision|relationship_moment|risk_boundary",
      "title": "20字以内标题",
      "content": "一段可落库的记忆——不脑补、不情绪加工、只记原文里发生过的事",
      "importance": 1到10整数,
      "thread_hint": "事业线|恋爱线|人际线|日常线|其他线",
      "relation_hints": ["same_event"|"same_topic"|"temporal_sequence"|"derived_from"],
      "source_chunk_ids": [数字 chunk_id],
      "evidence": "从原文一字不差引用的一句短证据，不超过80字，引用不出来这条作废",
      "risk": "normal|review"
    }}
  ]
}}

如果没有值得记的内容，返回 {{"candidates":[]}}——这是**合法输出**，不是失败。

chunks:
{items_text}
"""


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
    """晚间做梦 · 端到端

    设计：所有可调参数集中在构造函数；所有静默失败都走 logger 上报；
    所有依赖的 callable 在调用前显式判 None 给清楚的错误信息。
    """

    def __init__(
        self,
        proposer: Optional[Callable[[list[Chunk]], list[dict[str, Any]]]] = None,
        write_candidate: Optional[Callable[[Candidate], Optional[int]]] = None,
        write_safe_relation: Optional[Callable[[int, int, str, float, str], None]] = None,
        queue_review_relation: Optional[Callable[[int, int, str, str], None]] = None,
        find_neighbors: Optional[Callable[[int, int], list[int]]] = None,
        find_semantic_duplicates: Optional[Callable[[Candidate], list[int]]] = None,
        e_axis_dispatcher: Optional[Any] = None,
        importance_threshold: int = 7,
        max_promote: int = 10,
        relation_top_k: int = 5,
        logger: Optional["logging.Logger"] = None,
    ):
        """
        Args:
            proposer: chunks → 候选 dict 列表。不传走 deterministic_proposer
            write_candidate: 候选 → 落库，返回写入 id 或 None（重复时）
            write_safe_relation: (id_a, id_b, type, strength, reason) → 写关系
            queue_review_relation: (id_a, id_b, type, reason) → 入审计
            find_neighbors: (new_id, top_k) → 候选邻居 id（用于关系扩展）
            find_semantic_duplicates: (candidate) → 库里相似度高于阈值的现有记忆 id 列表。
                典型实现：调 vector_pgvector.find_duplicates(candidate 内容, 0.92)。
                返回非空 = 已有同义记忆，跳过晋升（防 same_topic 阈值过低洪水：
                历史教训是 0.7 阈值放进来 7242 条同义条）
            e_axis_dispatcher: 可选 EAxisDispatcher 实例。写入每条候选后会自动按
                trigger 规则判断是否调 E scorer + 写回 valence/arousal/tension。
                不传则 E 字段全留 NULL（与 0.2.0 行为兼容）
            importance_threshold: 闸门阈值，低于这个不晋升
            max_promote: 单次最多晋升几条。超过的进 rejected[reason='exceeds_max_promote']，
                         不再静默 drop——长期跑会丢数据是 bug，是 feature 也要 log
            relation_top_k: 每个新记忆扩 top-K 邻居建关系
            logger: 自带 logger。不传走 logging.getLogger("lmc5.night_dream")
        """
        import logging
        for name, fn in (
            ("proposer", proposer),
            ("write_candidate", write_candidate),
            ("write_safe_relation", write_safe_relation),
            ("queue_review_relation", queue_review_relation),
            ("find_neighbors", find_neighbors),
            ("find_semantic_duplicates", find_semantic_duplicates),
        ):
            if fn is not None and not callable(fn):
                raise TypeError(
                    f"NightDream: {name} must be callable or None, "
                    f"got {type(fn).__name__}"
                )
        # e_axis_dispatcher 是对象不是 callable，duck-type 检查 maybe_score 方法
        if e_axis_dispatcher is not None and not hasattr(e_axis_dispatcher, "maybe_score"):
            raise TypeError(
                f"NightDream: e_axis_dispatcher must have a maybe_score(memory_id, candidate) "
                f"method, got {type(e_axis_dispatcher).__name__}"
            )
        self.proposer = proposer or deterministic_proposer
        self.write_candidate = write_candidate
        self.write_safe_relation = write_safe_relation
        self.queue_review_relation = queue_review_relation
        self.find_neighbors = find_neighbors
        self.find_semantic_duplicates = find_semantic_duplicates
        self.e_axis_dispatcher = e_axis_dispatcher
        self.importance_threshold = importance_threshold
        self.max_promote = max_promote
        self.relation_top_k = relation_top_k
        self.log = logger or logging.getLogger("lmc5.night_dream")

    def extract(self, chunks: list[Chunk]) -> tuple[list[Candidate], int]:
        """提取候选。返回 (candidates, proposer_errors_count)。
        proposer 报错不再吞掉——log + 返回错误计数，调用方能在 DreamResult 看到。
        """
        clean_chunks = [c for c in chunks if not is_noise(c.text)]
        if not clean_chunks:
            return [], 0
        try:
            raw = self.proposer(clean_chunks)
        except Exception as e:
            self.log.error("night_dream.extract: proposer raised %s: %s",
                           type(e).__name__, e, exc_info=True)
            return [], 1
        out: list[Candidate] = []
        skipped = 0
        for r in raw:
            if not isinstance(r, dict):
                skipped += 1
                continue
            cand = normalize_candidate(r)
            if cand:
                out.append(cand)
            else:
                skipped += 1
        if skipped:
            self.log.info("night_dream.extract: %d raw candidates skipped at normalize", skipped)
        return out, 0

    def gate(self, candidates: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
        """闸门链：阈值 → risk → 必须有 source → 批内去重 → max_promote 截断

        超过 max_promote 的候选不再静默 drop，进 rejected[reason='exceeds_max_promote']。
        长期跑里这条是关键——丢数据要可见。
        """
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
            if len(promoted) >= self.max_promote:
                rejected.append((cand, "exceeds_max_promote"))
                continue
            promoted.append(cand)
        if any(r == "exceeds_max_promote" for _, r in rejected):
            n_dropped = sum(1 for _, r in rejected if r == "exceeds_max_promote")
            self.log.warning(
                "night_dream.gate: %d candidates dropped at max_promote=%d boundary "
                "(raise max_promote or lower importance_threshold if this happens often)",
                n_dropped, self.max_promote,
            )
        return promoted, rejected

    def build_relations(
        self,
        promoted_pairs: list[tuple[int, Candidate]],
    ) -> tuple[int, int]:
        """新记忆扩 top-K 邻居 → 按 candidate.relation_hints 决定关系类型

        relation_hints 用法：
        - 第一个落在 SAFE_RELATION_TYPES 的 hint → 主关系类型（写 safe 边）
        - 任何落在 REVIEW_RELATION_TYPES 的 hint → 入审计队列（不直接写）
        - 没有任何合规 hint → fallback 到 same_topic
        """
        if not promoted_pairs:
            return (0, 0)
        if self.find_neighbors is None:
            self.log.info("night_dream.build_relations: find_neighbors not configured; skipping")
            return (0, 0)

        safe_count = 0
        review_count = 0
        seen_pairs: set[tuple[int, int, str]] = set()

        for cid, cand in promoted_pairs:
            safe_hints = [h for h in cand.relation_hints if h in SAFE_RELATION_TYPES]
            review_hints = [h for h in cand.relation_hints if h in REVIEW_RELATION_TYPES]
            main_type = safe_hints[0] if safe_hints else "same_topic"

            try:
                neighbors = self.find_neighbors(cid, self.relation_top_k) or []
            except Exception as e:
                self.log.warning("night_dream.build_relations: find_neighbors(%d) failed: %s",
                                 cid, e)
                continue

            for nid in neighbors:
                if nid == cid:
                    continue
                a, b = min(int(cid), int(nid)), max(int(cid), int(nid))
                reason = f"dream:hints={','.join(cand.relation_hints) or 'none'}"

                # safe 边
                safe_key = (a, b, main_type)
                if safe_key not in seen_pairs and self.write_safe_relation is not None:
                    try:
                        self.write_safe_relation(a, b, main_type, 0.5, reason)
                        seen_pairs.add(safe_key)
                        safe_count += 1
                    except Exception as e:
                        self.log.warning("night_dream.build_relations: write_safe_relation(%d,%d,%s) failed: %s",
                                         a, b, main_type, e)

                # review 边（如果 hints 里有 review 类型）
                for rtype in review_hints:
                    review_key = (a, b, rtype)
                    if review_key in seen_pairs or self.queue_review_relation is None:
                        continue
                    try:
                        self.queue_review_relation(a, b, rtype, f"dream:hint:{rtype}")
                        seen_pairs.add(review_key)
                        review_count += 1
                    except Exception as e:
                        self.log.warning("night_dream.build_relations: queue_review(%d,%d,%s) failed: %s",
                                         a, b, rtype, e)
        return safe_count, review_count

    def run(self, chunks: list[Chunk], apply: bool = False) -> DreamResult:
        candidates, proposer_errors = self.extract(chunks)
        promoted, rejected = self.gate(candidates)
        written_ids: list[int] = []
        written_pairs: list[tuple[int, Candidate]] = []
        safe_n = review_n = 0

        if apply:
            if self.write_candidate is None:
                self.log.error("night_dream.run: apply=True but write_candidate is None; "
                               "nothing will be written")
            else:
                semantic_dedup_skipped = 0
                for cand in promoted:
                    # 语义去重：写入前先用向量相似度查库；命中阈值就跳过
                    # （防止跨夜同义条目洪水——批内去重只管 (type, title) 签名）
                    if self.find_semantic_duplicates is not None:
                        try:
                            dup_ids = self.find_semantic_duplicates(cand) or []
                        except Exception as e:
                            self.log.warning(
                                "night_dream.run: find_semantic_duplicates failed for '%s': %s "
                                "(skipping dedup check, will write)",
                                cand.title[:40], e,
                            )
                            dup_ids = []
                        if dup_ids:
                            rejected.append((cand, f"semantic_dup:{dup_ids[0]}"))
                            semantic_dedup_skipped += 1
                            self.log.info(
                                "night_dream.run: semantic dedup skipped '%s' "
                                "-> existing id=%s",
                                cand.title[:40], dup_ids[0],
                            )
                            continue
                    try:
                        wid = self.write_candidate(cand)
                    except Exception as e:
                        self.log.error("night_dream.run: write_candidate failed for '%s': %s",
                                       cand.title[:40], e)
                        continue
                    if wid is not None:
                        written_ids.append(int(wid))
                        written_pairs.append((int(wid), cand))
                        # E 轴自动评分：dispatcher 内部按 trigger 规则决定是否调 scorer。
                        # 异常永不阻塞主流程——E 分挂了，记忆还在。
                        if self.e_axis_dispatcher is not None:
                            try:
                                self.e_axis_dispatcher.maybe_score(int(wid), cand)
                            except Exception as e:
                                self.log.warning(
                                    "night_dream.run: e_axis dispatcher raised for #%s: %s",
                                    wid, e,
                                )
                if semantic_dedup_skipped:
                    self.log.info("night_dream.run: %d candidates skipped by semantic dedup",
                                  semantic_dedup_skipped)
                if written_pairs:
                    safe_n, review_n = self.build_relations(written_pairs)
        return DreamResult(
            chunks_used=len(chunks),
            candidates=candidates,
            promoted=promoted,
            rejected=rejected,
            written_ids=written_ids,
            safe_relations_written=safe_n,
            review_relations_queued=review_n,
        )
