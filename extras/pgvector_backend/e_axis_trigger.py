"""E 轴触发层 · 决定"什么记忆该被打 E 分"

production impl 之前漏了一层：`e_axis_scorer.py` 只解决"怎么打分"，
但**谁来决定"这段记忆要不要打 E"** 这层完全空白——night_dream 写入路径根本
不调 EAxisScorer，所有 E 字段都得调用方手动喂。

这个文件补上 trigger 层：
  1. `should_score_e_axis(candidate)` — 判断单条候选是否值得调 scorer
  2. `EAxisDispatcher` — 把判断 + 评分 + 写回串成一条链，给 night_dream 用
  3. `backfill_e_axis(...)` — 夜间批量补漏分（写入但 E 字段缺失的记忆）

设计：
  - trigger 规则可覆盖：默认按 type + 关键词 + relation_hints 判断，
    部署方可注入自家 gate 函数
  - 关键词字典中英双语，覆盖情绪/关系性/张力/物理反应四类
  - dispatcher 异常永不阻塞写入主流程——E 分挂了，记忆本身还在
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .e_axis_scorer import EAxisScore, EAxisScorer


# === 触发规则 ===========================================================

# 这些 type 一律打分（关系性和风险性记忆不打 E 等于丢一半信息）
ALWAYS_TRIGGER_TYPES = frozenset({
    "relationship_moment",
    "risk_boundary",
    "preference",
})

# 这些 type 默认不打（事实/工程决策的情绪信号通常是噪声）
NEVER_TRIGGER_TYPES = frozenset({
    "fact",
    "engineering_decision",
})

# 双语情绪关键词——命中任一即触发。粗筛用，宁多不少
EMOTION_TRIGGER_KEYWORDS = (
    # 中文 · 强情绪
    "崩溃", "气死", "委屈", "心痛", "感动", "爱你", "想你",
    "兴奋", "焦虑", "害怕", "纠结", "绝望", "好难", "受不了",
    "心如灰死", "算了", "放弃", "不想",
    # 中文 · 关系性
    "我们", "答应", "承诺", "再也不", "永远", "永不",
    # 中文 · 张力
    "矛盾", "冲突", "为难", "撕扯",
    # 中文 · 物理反应（亲密向）
    "亲", "抱", "吻", "靠着", "蹭", "哭",
    # English · strong
    "love you", "miss you", "panic", "anxious", "furious",
    "exhausted", "betrayed", "heartbreak", "devastated",
    "promise", "swear", "never again", "always",
    # English · relational
    "we agreed", "you promised", "between us",
    # English · tension
    "conflict", "argued", "fight",
    # English · physical
    "kiss", "hug", "hold me", "cry",
)


def should_score_e_axis(candidate: Any) -> bool:
    """判断单条候选是否值得调 E scorer。

    规则顺序（短路）：
      1. type 在 ALWAYS_TRIGGER_TYPES → 一律打
      2. type 在 NEVER_TRIGGER_TYPES 且无情绪关键词 → 不打
      3. content/title 命中情绪关键词 → 打
      4. relation_hints 含 emotional_link → 打
      5. 默认不打（保守）

    candidate 需要 .type / .title / .content / .relation_hints 字段
    """
    ctype = getattr(candidate, "type", "") or ""

    if ctype in ALWAYS_TRIGGER_TYPES:
        return True

    text = f"{getattr(candidate, 'title', '')}\n{getattr(candidate, 'content', '')}".lower()
    hit_keyword = any(kw.lower() in text for kw in EMOTION_TRIGGER_KEYWORDS)

    if ctype in NEVER_TRIGGER_TYPES and not hit_keyword:
        return False

    if hit_keyword:
        return True

    hints = getattr(candidate, "relation_hints", None) or []
    if "emotional_link" in hints:
        return True

    return False


# === Dispatcher · candidate → score → 写回 =============================

class EAxisDispatcher:
    """把"判断 → 评分 → 写回"串成一条链。

    night_dream.run() 写入每条 candidate 后调 `maybe_score(memory_id, cand)`，
    这个类负责：
      - 用 gate 决定要不要打分
      - 调 EAxisScorer.score()
      - 调 attach_score(memory_id, EAxisScore) 写回 DB

    所有异常被捕获并 log——E 分挂了，记忆本身还在。
    """

    def __init__(
        self,
        scorer: "EAxisScorer",
        attach_score: Callable[[int, "EAxisScore"], None],
        gate: Optional[Callable[[Any], bool]] = None,
        logger=None,
    ):
        """
        Args:
            scorer: 一个已经构造好的 EAxisScorer 实例
            attach_score: (memory_id, score) → None。把 E 字段写回 DB 的 callback
            gate: candidate → bool 触发判断。不传走 should_score_e_axis
            logger: 自带 logger；不传走 logging.getLogger("lmc5.e_axis_trigger")
        """
        import logging
        if scorer is None:
            raise TypeError("EAxisDispatcher: scorer is required")
        if not callable(attach_score):
            raise TypeError(
                f"EAxisDispatcher: attach_score must be callable, got {type(attach_score).__name__}"
            )
        if gate is not None and not callable(gate):
            raise TypeError(
                f"EAxisDispatcher: gate must be callable or None, got {type(gate).__name__}"
            )
        self.scorer = scorer
        self.attach_score = attach_score
        self.gate = gate or should_score_e_axis
        self.log = logger or logging.getLogger("lmc5.e_axis_trigger")

    def maybe_score(self, memory_id: int, candidate: Any) -> Optional["EAxisScore"]:
        """评估并视情况打分。返回 score 或 None。"""
        try:
            if not self.gate(candidate):
                return None
        except Exception as e:
            self.log.warning("E gate failed for #%d: %s", memory_id, e)
            return None

        title = getattr(candidate, "title", "") or ""
        content = getattr(candidate, "content", "") or ""
        try:
            score = self.scorer.score(title, content, record_id=memory_id)
        except Exception as e:
            self.log.warning("EAxisScorer.score raised for #%d: %s", memory_id, e)
            return None

        if score is None:
            # scorer 自己已经记了 fail_log；这里不重复
            return None

        try:
            self.attach_score(memory_id, score)
        except Exception as e:
            self.log.error("attach_score failed for #%d: %s", memory_id, e)
            return None

        return score


# === 夜间批量补漏分 =====================================================

def backfill_e_axis(
    load_missing: Callable[[int], list],
    scorer: "EAxisScorer",
    attach_score: Callable[[int, "EAxisScore"], None],
    max_batch: int = 50,
    sleep_between_s: float = 0.3,
) -> dict:
    """扫近 24h 写入但 E 字段缺失的记忆，补打分。

    与 dispatcher 路径的差别：backfill 是补漏，**不**再过 gate——
    走到这里说明记忆已经入库，只是当时没打分。是否打交给 load_missing
    SQL 决定（部署方可以加 WHERE category IN (...) 之类的过滤）。

    Args:
        load_missing: (limit) → list of (memory_id, title, content)
        scorer: EAxisScorer 实例
        attach_score: (memory_id, score) → None
        max_batch: 单次最多处理几条
        sleep_between_s: 防限流

    Returns: {"scored": int, "skipped": int, "failed": int}
    """
    import time

    rows = load_missing(max_batch) or []
    scored = 0
    skipped = 0
    failed = 0

    for row in rows:
        try:
            memory_id, title, content = int(row[0]), row[1] or "", row[2] or ""
        except (TypeError, ValueError, IndexError):
            skipped += 1
            continue

        try:
            score = scorer.score(title, content, record_id=memory_id)
        except Exception:
            failed += 1
            continue

        if score is None:
            skipped += 1
            continue

        try:
            attach_score(memory_id, score)
            scored += 1
        except Exception:
            failed += 1

        if sleep_between_s > 0:
            time.sleep(sleep_between_s)

    return {"scored": scored, "skipped": skipped, "failed": failed,
            "examined": len(rows)}


# === SQL helper（参考实现，部署方按需用）===

DEFAULT_LOAD_MISSING_SQL = """
SELECT id, title, content
FROM lmc5_curated_memories
WHERE version_status = 'current'
  AND valence_v3 IS NULL                      -- 没打过 E 分
  AND created_at >= NOW() - INTERVAL '24 hours'
  AND category IN ('relationship_moment', 'fragments', 'heartbeat',
                   'diary', 'preference', 'risk_boundary')
ORDER BY created_at DESC
LIMIT %s
"""

DEFAULT_ATTACH_SCORE_SQL = """
UPDATE lmc5_curated_memories
SET valence_v3 = %(valence)s,
    arousal_v3 = %(arousal)s,
    tension = %(tension)s,
    response_tendency = %(response_tendency)s,
    growth_delta = %(growth_delta)s,
    emotion_confidence = %(confidence)s,
    emotion_scorer = %(scorer)s,
    emotion_rubric_version = %(rubric_version)s
WHERE id = %(id)s
"""
