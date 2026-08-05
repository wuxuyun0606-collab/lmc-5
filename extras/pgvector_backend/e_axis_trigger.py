"""E 轴建议层（非写入层）· 找出可供主 AI 审阅的体验候选

主 AI 拥有 E 轴首写权和首排权。这一层只帮助 housekeeper/scorer
发现值得交给主 AI 审阅的候选，不是 E 轴写入链路。

这个文件只允许产生 proposal：
  1. `should_propose_e_axis(candidate)` — 判断单条候选是否值得建议给主 AI
  2. `EAxisProposalDispatcher` — 把建议写入 proposal queue，不写 E 主记录
  3. `backfill_e_axis(...)` — 保留为硬失败兼容入口，防止旧部署继续自动补写 E

设计：
  - trigger 规则可覆盖：默认按 type + 关键词 + relation_hints 判断，
    部署方可注入自家 gate 函数
  - 关键词字典中英双语，覆盖情绪/关系性/张力/物理反应四类
  - 主 AI 亲自写 E 内容并给出初始优先级；housekeeper 没有首写权
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .e_axis_scorer import EAxisScore, EAxisScorer


# === 触发规则 ===========================================================

# 这些 type 一律提交 proposal 候选（仍需主 AI 决定是否写 E）
ALWAYS_TRIGGER_TYPES = frozenset({
    "relationship_moment",
    "risk_boundary",
    "preference",
})

# 这些 type 默认不提 proposal（事实/工程决策的情绪信号通常是噪声）
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


def should_propose_e_axis(candidate: Any) -> bool:
    """判断单条候选是否值得提交 E proposal。

    规则顺序（短路）：
      1. type 在 ALWAYS_TRIGGER_TYPES → 一律提 proposal
      2. type 在 NEVER_TRIGGER_TYPES 且无情绪关键词 → 不提
      3. content/title 命中情绪关键词 → 提
      4. relation_hints 含 emotional_link → 提
      5. 默认不提（保守）

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


# === Dispatcher · candidate → proposal queue ===========================

class EAxisProposalDispatcher:
    """把"判断 → 建议 → proposal queue"串成一条链。

    输出没有 E 轴权威性。主 AI 必须阅读原始经历，亲自写 E 内容并设置
    `e_initial_priority`；proposal 不能直接 UPDATE curated memories。
    """

    def __init__(
        self,
        scorer: "EAxisScorer",
        submit_proposal: Callable[[int, "EAxisScore"], None],
        gate: Optional[Callable[[Any], bool]] = None,
        logger=None,
    ):
        """
        Args:
            scorer: 一个已经构造好的 EAxisScorer 实例
            submit_proposal: (memory_id, score) → None。只写 proposal queue
            gate: candidate → bool 触发判断。不传走 should_propose_e_axis
            logger: 自带 logger；不传走 logging.getLogger("lmc5.e_axis_trigger")
        """
        import logging
        if scorer is None:
            raise TypeError("EAxisProposalDispatcher: scorer is required")
        if not callable(submit_proposal):
            raise TypeError(
                "EAxisProposalDispatcher: submit_proposal must be callable, got "
                f"{type(submit_proposal).__name__}"
            )
        if gate is not None and not callable(gate):
            raise TypeError(
                f"EAxisProposalDispatcher: gate must be callable or None, got {type(gate).__name__}"
            )
        self.scorer = scorer
        self.submit_proposal = submit_proposal
        self.gate = gate or should_propose_e_axis
        self.log = logger or logging.getLogger("lmc5.e_axis_trigger")

    def maybe_propose(self, memory_id: int, candidate: Any) -> Optional["EAxisScore"]:
        """评估并视情况提交非权威 proposal。返回 proposal 或 None。"""
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
            self.submit_proposal(memory_id, score)
        except Exception as e:
            self.log.error("submit_proposal failed for #%d: %s", memory_id, e)
            return None

        return score


# === 禁止自动补写 E ======================================================

def backfill_e_axis(*args, **kwargs) -> dict:
    """Removed: automated agents may not backfill authoritative E content."""
    raise RuntimeError(
        "automatic E-axis backfill is disabled; the primary agent must author "
        "E content and set its initial priority"
    )


# === SQL helper（参考实现，部署方按需用）===

DEFAULT_LOAD_PROPOSAL_CANDIDATES_SQL = """
SELECT id, title, content
FROM lmc5_curated_memories m
WHERE m.version_status = 'current'
  AND m.created_at >= NOW() - INTERVAL '24 hours'
  AND m.category IN ('relationship_moment', 'fragments', 'heartbeat',
                     'diary', 'preference', 'risk_boundary')
  AND NOT EXISTS (
      SELECT 1 FROM lmc5_e_axis_proposals p
      WHERE p.memory_id = m.id AND p.status = 'pending'
  )
ORDER BY m.created_at DESC
LIMIT %s
"""

DEFAULT_SUBMIT_PROPOSAL_SQL = """
INSERT INTO lmc5_e_axis_proposals (
    memory_id, valence, arousal, tension, response_tendency, growth_delta,
    confidence, proposer, rubric_version
) VALUES (
    %(id)s, %(valence)s, %(arousal)s, %(tension)s, %(response_tendency)s,
    %(growth_delta)s, %(confidence)s, %(scorer)s, %(rubric_version)s
)
"""
