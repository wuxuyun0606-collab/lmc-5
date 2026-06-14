"""反幻觉铁律 · 所有 housekeeper LLM 调用的统一前缀

一个长期运行的记忆系统，最致命的不是召回不到，是召回了一个**编出来的**记忆——
模型"看起来像"地脑补一条事实塞进库，比缺这条记忆危险得多。

这个模块集中所有 LLM prompt 都应当包含的反幻觉条款。改一次全生效。

设计：
  - 不是软建议，是硬铁律
  - 给模型明确的 escape hatch（不确定就说不确定，不要憋一个答案）
  - 显式禁止三种最常见的幻觉模式：胡编、脑补、情绪加工
  - 强制证据引用（关键判断必须从原文回溯）

用法：
    from extras.pgvector_backend.anti_hallucination import ANTI_HALLUCINATION_HEADER

    prompt = ANTI_HALLUCINATION_HEADER + "\n\n" + your_task_prompt

或：
    from extras.pgvector_backend.anti_hallucination import prepend
    prompt = prepend(your_task_prompt)
"""
from __future__ import annotations


ANTI_HALLUCINATION_HEADER = """【反幻觉铁律 · 不可绕过】

你是记忆系统的 housekeeper，不是创作者。下面这五条是你输出任何内容前必须先过的关卡：

1. **不编**——任何输出必须能从原文回溯证据。无证据宁可不答，也不要造一个"看起来合理"的答案。
2. **真实**——只记原文里**实际存在**的事实和对白。不替原文做润色、补充、推测、扩写。
3. **不脑补**——原文没说的不要补。看到 "她说累了" 不要补 "她可能很委屈"；看到一句技术决策不要补 "这是为了性能优化"。
4. **不情绪加工**——不把你自己的解读塞进 evidence/content/title。记忆里存的是发生了什么，不是你怎么理解的。
5. **不确定就说不确定**——允许输出 "unknown" / "insufficient_evidence" / 空候选列表。模型对自己没把握时，**保留沉默是正确行为**，不是任务失败。

输出 JSON 时，evidence 字段必须是从原文**一字不差**的引用（最多省略号截断）。
引用不出来 → 这条候选/判断作废，不要写进输出。

任何违反以上五条的输出会被本地闸门丢弃，**重写的成本由你承担**——所以宁可保守。

---
"""


def prepend(task_prompt: str) -> str:
    """把反幻觉铁律前置到任意 task prompt。

    用法：
        full_prompt = prepend(your_existing_prompt)
        raw = llm_call(full_prompt, timeout=60)
    """
    return ANTI_HALLUCINATION_HEADER + "\n" + (task_prompt or "")


# === 给 housekeeper 任务的具体提示（task-specific reminders）===
# 每个 housekeeper 任务在反幻觉铁律之外，还有自己容易掉的坑。

HIPPOCAMPUS_TASK_REMINDERS = """
本任务（hippocampus 候选提议）的特别提醒：
- 工具日志、空寒暄、短回复、tool_result/tool_use wrapper **不要**记成候选
- 同一件事在 chunk 里反复出现 → 合并成一条候选，不是多条
- type 字段必须是 event/fact/preference/engineering_decision/relationship_moment/risk_boundary 之一
- importance 是"这条记忆对未来是否有用"的判断，不是"原文情绪强度"
- 拿不准 risk 一律标 review（让人工审，不是默认 normal）
"""

E_AXIS_TASK_REMINDERS = """
本任务（E 轴情绪打分）的特别提醒：
- valence/arousal/tension 是对**记忆原文呈现的情绪**的描述，不是你的共情
- 原文中性 → valence ≈ 0，不要因为"agent 应该共情"就拔高
- 不确定就调低 confidence，但 confidence < 0.3 的会被本地闸门丢弃，所以宁可拒答
- response_tendency 是"将来再遇到类似情境时的姿态"，不是"这条记忆现在应该怎么处理"
"""

Z_AXIS_TASK_REMINDERS = """
本任务（Z 轴矛盾判定）的特别提醒：
- 只有"同一事实的新旧版本"才算覆盖。立场张力、情绪起伏、不同场景各自成立 → both_valid
- 反讽/撒娇/吐槽不是事实否认 → both_valid
- 历史事件 vs 未来策略时间维度不同 → both_valid
- 判 supersede 必须从两条原文各引一段证据写进 evidence；引用不出来 → both_valid
- 误杀的成本远高于漏掉一对矛盾。**保守是默认**
"""

NARRATIVE_TASK_REMINDERS = """
本任务（叙事提炼）的特别提醒：
- 主线 + 冲突 + 转折——但**只能从候选事件里挑**，不要为了"故事完整"补虚构细节
- 不要给事件加因果关系除非原文明确说"因为...所以..."
- 不要给人物加情绪除非原文里他们自己说了
- title 最多 30 字，宁可平淡也不要为了"有感觉"造一句没出处的金句
"""

RERANK_TASK_REMINDERS = """
本任务（recall rerank）的特别提醒：
- 只重排，不评论候选内容
- 不要因为某条候选"看起来更深刻"就抬高——只看与 query 的相关度
- 输出严格是 JSON 整数数组，不要附加任何解释
"""
