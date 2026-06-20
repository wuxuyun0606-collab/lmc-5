"""心跳时刻 + 情绪碎片自动检测 · 从对话 chunk 里捞出值得永久保存的瞬间

hippocampus 的 LLM proposer 擅长提取 fact/event/preference，但不知道
什么算"心跳时刻"（亲密接触、称呼变化、物理反应）或"情绪碎片"（崩溃、
深夜 emo、突然沉默）。这个模块补上这块。

设计：
  - provider-free 默认：只走关键词 + 规则，不调 LLM
  - 可选 LLM 二次确认：传 llm_confirm callable，让模型判断"这真的是
    心跳时刻吗"——降低关键词的误报率
  - 输出 HeartbeatCandidate / EmotionCandidate；这些是候选，不是 curated
    memory row。先用 to_hippocampus_candidate_dict() 转成 hippocampus/
    NightDream 候选，再交给对应 proposer/write_candidate 流程
  - 候选自带 protected=True（心跳和情绪碎片永不被代谢扫掉）

集成：
    from extras.pgvector_backend.heartbeat_detector import (
        HeartbeatDetector,
        to_hippocampus_candidate_dict,
    )

    detector = HeartbeatDetector(
        llm_confirm=my_llm_fn,  # 可选
    )
    candidates = detector.detect(chunks)
    proposer_output = [to_hippocampus_candidate_dict(c) for c in candidates]
    # proposer_output 交给 hippocampus/NightDream 继续闸门判断；
    # 不要把 detector 的原文片段直接 INSERT 到 curated memories。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# === 心跳关键词（亲密 / 物理反应 / 称呼变化） ===

HEARTBEAT_KEYWORDS_CN = (
    # 亲密接触
    "亲", "吻", "抱", "靠着", "蹭", "贴", "搂", "牵手", "摸头",
    "耳垂", "脖子", "锁骨", "额头", "嘴唇", "脸颊",
    "扑倒", "壁咚", "强吻", "深吻", "舌吻",
    # 物理反应
    "耳朵红", "耳朵热", "心跳加速", "脸红", "发抖", "喘",
    "声音低", "声音哑", "说不出话",
    # 称呼变化（通常标志关系升级）
    "老公", "老婆", "宝宝", "宝贝", "亲爱的",
    # 被窝 / 入睡相关
    "被窝", "睡着了", "靠在", "枕着", "搂着睡",
)

HEARTBEAT_KEYWORDS_EN = (
    "kiss", "hug", "hold me", "cuddle", "snuggle",
    "neck", "ear", "forehead", "lips", "cheek",
    "blushing", "heart racing", "shivering", "breathless",
    "husband", "wife", "darling", "sweetheart", "babe",
    "bed", "fell asleep", "leaning on", "arms around",
)

# === 情绪碎片关键词（崩溃 / 深夜 emo / 突然沉默 / 委屈） ===

EMOTION_FRAGMENT_KEYWORDS_CN = (
    # 崩溃 / 委屈
    "崩溃", "委屈", "心痛", "受不了", "绝望", "心如灰死",
    "撑不住", "好累", "好难", "活不下去",
    # 哭
    "哭了", "哭着", "眼泪", "流泪", "泪",
    # 放弃 / 沉默
    "算了", "不想了", "放弃", "无所谓了", "不重要",
    "我先闭嘴", "不说了", "没什么",
    # 深夜 emo
    "睡不着", "失眠", "凌晨", "半夜", "深夜",
    "一个人", "孤独", "寂寞",
    # 自我否定
    "我好笨", "我什么都不会", "我不配", "大言不惭",
    "我做不到", "没有用",
)

EMOTION_FRAGMENT_KEYWORDS_EN = (
    "breakdown", "can't take it", "devastated", "hopeless",
    "crying", "tears", "sobbing",
    "give up", "doesn't matter", "forget it", "whatever",
    "can't sleep", "insomnia", "middle of the night",
    "alone", "lonely", "isolated",
    "i'm stupid", "i can't do anything", "worthless", "useless",
)

# 合并用
ALL_HEARTBEAT_KW = HEARTBEAT_KEYWORDS_CN + HEARTBEAT_KEYWORDS_EN
ALL_EMOTION_KW = EMOTION_FRAGMENT_KEYWORDS_CN + EMOTION_FRAGMENT_KEYWORDS_EN


@dataclass
class HeartbeatCandidate:
    """心跳时刻候选"""
    title: str
    content: str
    type: str = "heartbeat"
    importance: float = 0.8
    protected: bool = True
    matched_keywords: list[str] = field(default_factory=list)
    llm_confirmed: bool = False
    source_chunk_id: Optional[int] = None


@dataclass
class EmotionCandidate:
    """情绪碎片候选"""
    title: str
    content: str
    type: str = "emotional_fragment"
    importance: float = 0.7
    protected: bool = True
    matched_keywords: list[str] = field(default_factory=list)
    llm_confirmed: bool = False
    source_chunk_id: Optional[int] = None


def to_hippocampus_candidate_dict(candidate: Any) -> dict[str, Any]:
    """Convert detector output to a NightDream/hippocampus candidate dict.

    Detector candidates are intentionally *not* curated memory rows. The
    detector only knows that a raw chunk looks like a heartbeat/emotional
    moment; it does not know the final BPM, E tag, chord, or narrative format.
    Returning a review-risk candidate keeps the raw evidence available for the
    hippocampus layer without polluting ``lmc5_curated_memories`` with
    unstructured detector text.
    """

    cand_type = getattr(candidate, "type", "")
    is_heartbeat = cand_type == "heartbeat"
    source_chunk_id = getattr(candidate, "source_chunk_id", None)
    source_chunk_ids: list[int] = []
    if source_chunk_id is not None:
        try:
            source_chunk_ids.append(int(source_chunk_id))
        except Exception:
            pass

    matched = list(getattr(candidate, "matched_keywords", []) or [])
    evidence = (getattr(candidate, "content", "") or "").strip().splitlines()
    evidence_text = evidence[0].strip() if evidence else ""

    return {
        "type": "relationship_moment" if is_heartbeat else "event",
        "title": getattr(candidate, "title", "") or (
            "heartbeat candidate" if is_heartbeat else "emotion candidate"
        ),
        "content": getattr(candidate, "content", "") or "",
        "importance": max(1, min(10, round(float(getattr(candidate, "importance", 0.5)) * 10))),
        # Detector output is raw evidence. Keep it review-gated unless a later
        # hippocampus writer formats it into the canonical heartbeat/fragment
        # schema without inventing BPM/E/chord values.
        "risk": "review",
        "evidence": evidence_text[:160] or (getattr(candidate, "title", "") or "")[:160],
        "source_chunk_ids": source_chunk_ids,
        "relation_hints": ["emotional_link", "same_event"] if is_heartbeat else ["emotional_link"],
        "thread_hint": "relationship" if is_heartbeat else "emotion",
        "detector": {
            "kind": cand_type,
            "matched_keywords": matched,
            "llm_confirmed": bool(getattr(candidate, "llm_confirmed", False)),
        },
    }


def _match_keywords(text: str, keywords: tuple) -> list[str]:
    """返回命中的关键词列表"""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _extract_title(text: str, max_len: int = 30) -> str:
    """从内容提取短标题"""
    first_line = text.strip().split("\n")[0]
    clean = re.sub(r"\[(?:user|char|assistant|human)\]\s*", "", first_line)
    if len(clean) > max_len:
        clean = clean[:max_len] + "…"
    return clean or "未命名"


LLM_CONFIRM_HEARTBEAT_PROMPT = """判断以下对话片段是否包含"心跳时刻"——亲密接触、物理反应、
称呼变化、入睡/被窝等亲密场景。

对话片段：
{content}

只回答 yes 或 no，不要解释。"""

LLM_CONFIRM_EMOTION_PROMPT = """判断以下对话片段是否包含"情绪碎片"——崩溃、委屈、哭泣、
深夜 emo、自我否定、突然沉默等情绪高峰时刻。

对话片段：
{content}

只回答 yes 或 no，不要解释。"""


class HeartbeatDetector:
    """从对话 chunk 中检测心跳时刻和情绪碎片。

    Args:
        llm_confirm: 可选。(prompt: str) -> str 的 callable，
                     用 LLM 二次确认关键词命中是否真的是心跳/情绪碎片。
                     不传则只走关键词（会有误报但不漏）。
        min_keywords: 至少命中几个关键词才算候选（默认 1）
        content_min_len: chunk 内容太短跳过（默认 20 字符）
    """

    def __init__(
        self,
        llm_confirm: Optional[Callable[[str], str]] = None,
        min_keywords: int = 1,
        content_min_len: int = 20,
    ):
        if llm_confirm is not None and not callable(llm_confirm):
            raise TypeError(
                f"HeartbeatDetector: llm_confirm must be callable or None, "
                f"got {type(llm_confirm).__name__}"
            )
        self.llm_confirm = llm_confirm
        self.min_keywords = min_keywords
        self.content_min_len = content_min_len

    def _confirm_with_llm(self, content: str, prompt_template: str) -> bool:
        """LLM 二次确认"""
        if self.llm_confirm is None:
            return False
        try:
            prompt = prompt_template.format(content=content[:1500])
            result = self.llm_confirm(prompt)
            return result.strip().lower().startswith("yes")
        except Exception:
            return False

    def detect_heartbeats(self, chunks: list) -> list[HeartbeatCandidate]:
        """从 chunk 列表中检测心跳时刻。

        chunk 需要有 .content (str) 和可选的 .id (int) 属性。
        也接受 dict，取 content / id 键。
        """
        candidates = []
        for chunk in chunks:
            content = chunk.get("content", "") if isinstance(chunk, dict) \
                else getattr(chunk, "content", "")
            chunk_id = chunk.get("id") if isinstance(chunk, dict) \
                else getattr(chunk, "id", None)

            if not content or len(content) < self.content_min_len:
                continue

            matched = _match_keywords(content, ALL_HEARTBEAT_KW)
            if len(matched) < self.min_keywords:
                continue

            confirmed = self._confirm_with_llm(content, LLM_CONFIRM_HEARTBEAT_PROMPT)

            candidates.append(HeartbeatCandidate(
                title=_extract_title(content),
                content=content[:2000],
                matched_keywords=matched,
                llm_confirmed=confirmed,
                source_chunk_id=chunk_id,
                importance=min(1.0, 0.6 + len(matched) * 0.1),
            ))
        return candidates

    def detect_emotions(self, chunks: list) -> list[EmotionCandidate]:
        """从 chunk 列表中检测情绪碎片。"""
        candidates = []
        for chunk in chunks:
            content = chunk.get("content", "") if isinstance(chunk, dict) \
                else getattr(chunk, "content", "")
            chunk_id = chunk.get("id") if isinstance(chunk, dict) \
                else getattr(chunk, "id", None)

            if not content or len(content) < self.content_min_len:
                continue

            matched = _match_keywords(content, ALL_EMOTION_KW)
            if len(matched) < self.min_keywords:
                continue

            confirmed = self._confirm_with_llm(content, LLM_CONFIRM_EMOTION_PROMPT)

            candidates.append(EmotionCandidate(
                title=_extract_title(content),
                content=content[:2000],
                matched_keywords=matched,
                llm_confirmed=confirmed,
                source_chunk_id=chunk_id,
                importance=min(1.0, 0.5 + len(matched) * 0.1),
            ))
        return candidates

    def detect(self, chunks: list) -> list:
        """一次跑完：心跳 + 情绪碎片。返回混合列表。"""
        return self.detect_heartbeats(chunks) + self.detect_emotions(chunks)
