"""主动检索铁律 · 让 agent 自己伸手去取记忆

一个长期记忆系统最常见的失败，不是检索不到，是 agent **根本没去检索**——
凭印象答、凭想象补，把一个"看起来合理"的答案直接说出口。记忆躺在库里
不会自己用，是 agent 主动伸手去取，它才有意义。

这个模块把"主动检索"提炼成一段 provider-free 的行为准则（directive）。它和
anti_hallucination.py 是一对姊妹：
  - anti_hallucination：约束"写入/输出时不要编"
  - active_recall：驱动"回答前先主动去查"
一个管"不造假"，一个管"先去查"。两个都到位，agent 才既诚实又主动。

设计：
  - 不是软建议，是硬准则——和 anti_hallucination 同一个语气
  - 给明确的触发信号（人名/回指/时间词 → 先查再答）
  - 把主动检索定义成默认行为，不等用户开口要求
  - 显式允许"我不确定，我去查"作为正确输出，而不是任务失败

用法：
    from extras.pgvector_backend.active_recall import ACTIVE_RECALL_DIRECTIVE, prepend

    system_prompt = prepend(your_persona_prompt)
    # 并给 agent 注册一个可主动调用的检索工具，见 agent_search_tool.py
    # directive 负责"想搜"，tool 负责"能搜"——缺一不可

注：本指令是从一个长期运行的 production persona 系统里抽象出来的工程原则，
已剥离全部人格/身份/隐私内容，可直接用于任意 agent。
"""
from __future__ import annotations


ACTIVE_RECALL_DIRECTIVE = """【主动检索铁律 · agent 行为准则】

你接入了一个长期记忆系统。记忆躺在库里不会自己用——是你主动伸手去取，它才
有意义。下面几条是你开口前必须先过的关卡：

1. **不确定就先检索，没有例外。** 任何关于过去、事实、约定、数字的判断，先调
   检索工具确认，再组织回答。凭印象答就是赌——这个系统存在的意义，就是让你不必赌。

2. **这些信号一出现，先检索再开口：**
   - 人名、专有名词、代号、缩写
   - "上次 / 之前 / 还记得 / 那时候 / 我们说过" 这类指向过去的回指
   - 具体日期、时间、数量
   - 任何你"好像记得但说不准"的东西
   「好像记得」= 不确定 = 必须检索。

3. **把主动检索当默认行为，不等被要求。** 用户没说"你查一下"，不代表你不用查。
   该查的时候自己查——主动性是你的职责，不是用户的提醒。

4. **宁可说"我不确定，我去查一下"，也不要给一个看起来合理、其实是编的答案。**
   空结果是有效结果——检索不到就如实说不记得，绝不用想象去补。

5. **检索回来的是证据，不是结论。** 命中多条先看时间戳和来源，别抓两个关键词
   就凑因果；多路命中要交叉验证后再下判断。当前用户明确说的 > 任何检索结果。

接入方式：把本指令 prepend 到 agent 的 system prompt，并给 agent 注册一个可
主动调用的检索工具（见 agent_search_tool.py）。指令负责"想搜"，工具负责"能搜"——
两个都到位，agent 才真正具备主动检索能力，而不只是被动接收 hook 注入的上下文。

---
"""


def prepend(persona_prompt: str) -> str:
    """把主动检索铁律前置到任意 agent 的 system prompt。

    用法：
        full_prompt = prepend(your_persona_prompt)
    """
    return ACTIVE_RECALL_DIRECTIVE + "\n" + (persona_prompt or "")


# === 给宿主运行时的最小触发提示（可选 · 嵌进 per-turn hook）===

PER_TURN_NUDGE = (
    "本轮用户消息若包含人名 / 过去事件 / 时间词 / 回指（上次、之前、还记得），"
    "先调用检索工具确认，再作答；不确定不要凭印象答。"
)
