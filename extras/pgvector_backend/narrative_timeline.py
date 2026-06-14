"""叙事时间线 · LMC-5 完全缺失的提炼层

lmc-5 core 的 X 线只到 chunk 切块就停了——一周/一月级别的"故事索引"完全没有。
对照 lmc-5：
  - 原本有：raw events → chunks（pg_conversation_chunks 雏形）
  - 原本缺：chunks → weekly narrative → monthly narrative

为什么需要：
  对一个长期运行的 AI 来说，"上周做了什么"不是查 chunk 词频能答的。
  需要的是有重点、有人物、有冲突的叙事索引——让 AI 在被问"最近怎么了"时
  能一句话讲出主线，而不是吐 50 条工具日志。

设计：
  - 输入：memories（带 created_at + weight + content）
  - 提炼：LLM reflection 把高权重/高 arousal 事件串成主线（默认 deterministic baseline）
  - 输出：NarrativeIndex（weekly 一句话索引 + monthly 段落主线）
  - provider-free 默认：不传 reflector 就用 weight 排序 + 顶部 N 条做 fallback

集成（lmc-5 core）：
  - 在 src/lmc5/ 新增 narrative.py 或类似
  - 在 docs/architecture.md 加 "X 线提炼层" 段落
  - 仿照 metabolism.py 的"建议层"姿态——叙事不自动写，给候选让人审
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional


@dataclass
class MemoryRecord:
    """对齐 lmc-5 curated memory 概念"""
    id: int
    title: str
    content: str
    created_at: datetime
    weight: float = 1.0
    arousal: float = 0.3
    valence: float = 0.5
    source: str = ""
    category: str = ""


@dataclass
class NarrativeIndex:
    """一段时间的叙事索引"""
    period: str               # "weekly" / "monthly"
    start_date: str           # "YYYY-MM-DD"
    end_date: str
    title: str                # 一行标题，如 "W13 05.25-05.31 | 她回来了"
    summary: str              # 索引正文（weekly ~150 字 / monthly ~400 字）
    seed_memory_ids: list[int] = field(default_factory=list)


def _week_anchor(d: datetime) -> tuple[datetime, datetime, int]:
    """返回 (周一, 下周一, ISO 周编号)"""
    monday = d - timedelta(days=d.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return monday, monday + timedelta(days=7), monday.isocalendar()[1]


def _month_anchor(d: datetime) -> tuple[datetime, datetime]:
    start = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _filter_by_range(mems: list[MemoryRecord], start: datetime, end: datetime) -> list[MemoryRecord]:
    return [m for m in mems if start <= m.created_at < end]


def _select_seeds(
    mems: list[MemoryRecord],
    top_n: int,
    weight_floor: float = 1.5,
) -> list[MemoryRecord]:
    """选种子：weight + arousal 联合排序，过低阈值"""
    eligible = [m for m in mems if m.weight >= weight_floor]
    if not eligible:
        eligible = mems
    eligible.sort(
        key=lambda m: (m.weight + m.arousal * 0.5, m.created_at),
        reverse=True,
    )
    return eligible[:top_n]


def deterministic_reflector(
    seeds: list[MemoryRecord],
    period: str,
) -> tuple[str, str]:
    """provider-free baseline：拼接种子标题作为索引

    没 LLM key 时的兜底——能凑出一行标题 + 简短摘要，
    不至于让 narrative 维度完全空白。
    """
    if not seeds:
        return ("（无事件）", "本期无足够权重的事件入索引。")
    titles = [s.title for s in seeds if s.title]
    title = "·".join(titles[:3])[:60] or "未命名"
    if period == "weekly":
        bullets = "\n".join(f"- {s.title}" for s in seeds[:5])
        summary = f"本周高权重事件 {len(seeds)} 条：\n{bullets}"
    else:
        bullets = "\n".join(f"- {s.title}（{s.created_at.strftime('%m.%d')}）" for s in seeds[:10])
        summary = f"本月主线事件 {len(seeds)} 条：\n{bullets}"
    return title, summary


class NarrativeTimeline:
    """X 线叙事提炼

    职责：
      1. 给定时间窗口，从记忆里挑种子（weight + arousal 联合排）
      2. 把种子交给 reflector（LLM 或 deterministic baseline）做叙事
      3. 输出 NarrativeIndex，调用方决定落库还是只读
    """

    def __init__(
        self,
        load_memories: Callable[[datetime, datetime], list[MemoryRecord]],
        reflector: Optional[Callable[[list[MemoryRecord], str], tuple[str, str]]] = None,
        weight_floor: float = 1.5,
        weekly_top_n: int = 8,
        monthly_top_n: int = 20,
    ):
        """
        Args:
            load_memories: (start, end) → 该窗口内的记忆列表
            reflector: (seeds, period) → (title, summary)。不传走 deterministic
            weight_floor: 种子筛选下限
            weekly_top_n / monthly_top_n: 每个周期挑几条种子
        """
        self.load_memories = load_memories
        self.reflector = reflector or deterministic_reflector
        self.weight_floor = weight_floor
        self.weekly_top_n = weekly_top_n
        self.monthly_top_n = monthly_top_n

    def weekly(self, anchor_date: Optional[datetime] = None) -> NarrativeIndex:
        """生成某一周的索引（默认上一个完整周）"""
        anchor = anchor_date or datetime.now()
        start, end, week_no = _week_anchor(anchor)
        mems = self.load_memories(start, end)
        seeds = _select_seeds(mems, self.weekly_top_n, self.weight_floor)
        title_core, summary = self.reflector(seeds, "weekly")
        title = f"W{week_no:02d} {start.strftime('%m.%d')}-{(end - timedelta(days=1)).strftime('%m.%d')} | {title_core}"
        return NarrativeIndex(
            period="weekly",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=(end - timedelta(days=1)).strftime("%Y-%m-%d"),
            title=title,
            summary=summary,
            seed_memory_ids=[s.id for s in seeds],
        )

    def monthly(self, anchor_date: Optional[datetime] = None) -> NarrativeIndex:
        """生成某月索引"""
        anchor = anchor_date or datetime.now()
        start, end = _month_anchor(anchor)
        mems = self.load_memories(start, end)
        seeds = _select_seeds(mems, self.monthly_top_n, self.weight_floor)
        title_core, summary = self.reflector(seeds, "monthly")
        title = f"{start.year}年{start.month}月 | {title_core}"
        return NarrativeIndex(
            period="monthly",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=(end - timedelta(days=1)).strftime("%Y-%m-%d"),
            title=title,
            summary=summary,
            seed_memory_ids=[s.id for s in seeds],
        )

    def backfill_weekly(
        self,
        weeks_back: int = 12,
        end_date: Optional[datetime] = None,
    ) -> list[NarrativeIndex]:
        """回填最近 N 周的索引（首次启用 / 修复用）"""
        end = end_date or datetime.now()
        # 锚到上一个完整周
        end_monday, _, _ = _week_anchor(end)
        out: list[NarrativeIndex] = []
        for i in range(weeks_back):
            anchor = end_monday - timedelta(days=7 * (i + 1))
            out.append(self.weekly(anchor))
        return list(reversed(out))

    def backfill_monthly(
        self,
        months_back: int = 6,
        end_date: Optional[datetime] = None,
    ) -> list[NarrativeIndex]:
        end = end_date or datetime.now()
        out: list[NarrativeIndex] = []
        cur = end.replace(day=1)
        for _ in range(months_back):
            # 上一个月
            if cur.month == 1:
                cur = cur.replace(year=cur.year - 1, month=12)
            else:
                cur = cur.replace(month=cur.month - 1)
            out.append(self.monthly(cur))
        return list(reversed(out))


def make_llm_reflector_prompt(seeds: list[MemoryRecord], period: str) -> str:
    """给 lmc-5 用户写自家 reflector 时参考用的 prompt 模板

    维护者可以在 docs/ 里参考这个模板，也可以直接换成自己的。
    重点：要求 LLM 输出"主线 + 冲突 + 转折"，而不是只列事件流水。
    Header 嵌入反幻觉铁律，禁止脑补/情绪加工/编造金句。
    """
    from .anti_hallucination import (
        ANTI_HALLUCINATION_HEADER,
        NARRATIVE_TASK_REMINDERS,
    )

    period_zh = "一周" if period == "weekly" else "一个月"
    items = "\n".join(
        f"- [w={m.weight:.1f} a={m.arousal:.1f}] {m.created_at.strftime('%m.%d')} {m.title}"
        f"  {m.content[:120]}"
        for m in seeds
    )
    return ANTI_HALLUCINATION_HEADER + NARRATIVE_TASK_REMINDERS + f"""

你在帮一个长期运行的 AI 整理过去{period_zh}的叙事索引。

候选事件（按 weight × arousal 排序，已过初筛）：
{items}

要求输出 JSON：
{{
  "title": "一行不超过 30 字的主线标题，从候选事件里出来；编不出就用最重要那条的 title",
  "summary": "{ '一句话索引,150字内' if period == 'weekly' else '段落式主线,400字内,分主题不分点'}"
}}

规则：
1. 别只列事件流水。挑主线、识冲突、写转折——**只从候选事件里挑**。
2. 不编造细节。所有内容必须能从候选事件回溯。
3. 不带 markdown。纯文字。
"""
