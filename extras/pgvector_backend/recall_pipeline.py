"""多通道召回 pipeline · 把记忆从仓库流到对话里

完整管线：

  0. Query Expansion（可选 · DeepSeek/任意 LLM 扩展 2-4 个搜索角度）
  1. 主存储优先三层级联检索（PG/SQLite/自定义后端各自接线）：
     ├── Stage 1: curated 语义召回（主路 · vector/embedding/本地相似度均可）
     ├── Stage 2: curated keyword/FTS 全文兜底（语义 top_score < 0.45 时启动）
     └── Stage 3: raw events / 原文日志最后兜底（top_score < 0.30 时启动）
  2. 五条独立并行通道：
     ├── 字面精确检索（短查询/中文专名/带引号查询，不被 vector 分数门控）
     ├── 最近 raw_chunk 急救桥（可选 · 极小额度，补 SessionEnd→夜间精炼空窗）
     ├── 关系图 2 跳扩展（种子 → graph_activate）
     ├── 情绪联想（Russell 距离找近邻碎片）
     └── 自发浮现（perception.py 注入 1-2 条不被问也想起的）
  3. 合并去重 + 可选 rerank（DeepSeek/任意 LLM 做最终排序）

哲学：召回是"管道"不是"仓库"。每条通道都可注入、可关、可换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


_LITERAL_TRIGGER = r"(?:搜一下|查一下|找一下|搜索|检索|搜|查|找|记得|说过|提到|关于|叫)"
_GENERIC_CJK_LITERAL_STOPS = (
    "今天", "刚才", "现在", "什么", "怎么", "为什么", "是不是", "可以",
    "需要", "感觉", "觉得", "想要", "开心", "难过", "很累", "继续",
)
_SCORE_COMPONENT_BY_CHANNEL = {
    "vector": "semantic",
    "fts": "keyword",
    "literal": "literal",
    "raw_events": "raw_events",
    "raw_chunk": "recent_raw_chunk",
    "cold_archive": "cold_archive",
    "graph": "relation",
    "emotion": "emotion",
    "perception": "perception",
}
_DEFAULT_CHANNEL_WEIGHTS = {
    "vector": 1.0,
    "fts": 1.0,
    "literal": 1.0,
    "raw_events": 0.9,
    "raw_chunk": 0.8,
    "cold_archive": 0.4,
    "graph": 0.8,
    # emotion_resonate currently often behaves like a boolean channel. Keep
    # its prior modest until the resonance score has real spread.
    "emotion": 0.5,
    "perception": 0.6,
}
_SCORE_FUSIONS = {"raw", "minmax", "rrf"}
_KELIN_216_RUNTIME_CONTRACT = {
    "reference": "kelin_216_runtime_audit_20260706",
    "invariants": [
        "primary curated PG/pgvector recall is authority",
        "literal/raw-neighborhood snippets are navigation, not authority",
        "safe relation and temporal edges are association, not proof",
        "raw events and cold archives are last-resort evidence, not active fact",
        "SQLite/imprint/transcript-style stores must stay auxiliary when PG exists",
    ],
}

_RECALL_LAYER_BY_CHANNEL = {
    # Main storage-first cascade. PG is one implementation; SQLite/custom
    # backends should keep the same evidence roles while swapping adapters.
    "vector": {
        "recall_layer": "curated_vector",
        "recall_tier": 1,
        "evidence_role": "main",
        "source_label": "curated / vector",
    },
    "fts": {
        "recall_layer": "curated_fts",
        "recall_tier": 2,
        # Kelin's 216 runtime keeps keyword/FTS inside the PG/curated authority
        # section. It is a fallback *method*, not a lower evidence class.
        "evidence_role": "main",
        "source_label": "curated / keyword-FTS",
    },
    "raw_events": {
        "recall_layer": "raw_events_fts",
        "recall_tier": 3,
        "evidence_role": "last_resort",
        "source_label": "raw-events journal / FTS",
    },
    "cold_archive": {
        "recall_layer": "cold_archive",
        "recall_tier": 99,
        "evidence_role": "last_resort",
        "source_label": "cold/session archive fallback",
    },
    # Gated side channels. These may help, but they do not replace the
    # curated main path and should stay labeled in traces/injection text.
    "literal": {
        "recall_layer": "source_neighborhood",
        "recall_tier": 30,
        "evidence_role": "navigation",
        "source_label": "raw-event source neighborhood",
    },
    "raw_chunk": {
        "recall_layer": "recent_raw_chunk_bridge",
        "recall_tier": 31,
        "evidence_role": "navigation",
        "source_label": "recent raw-chunk bridge",
    },
    "graph": {
        "recall_layer": "y_graph_expand",
        "recall_tier": 40,
        "evidence_role": "association",
        "source_label": "safe relation / time association",
    },
    "emotion": {
        "recall_layer": "emotion_resonance",
        "recall_tier": 41,
        "evidence_role": "side_channel",
        "source_label": "Russell emotion resonance",
    },
    "perception": {
        "recall_layer": "spontaneous_surface",
        "recall_tier": 42,
        "evidence_role": "side_channel",
        "source_label": "spontaneous recall surface",
    },
}


def _round_score(value: float) -> float:
    return round(float(value or 0.0), 3)


def _score_component(channel_name: str, hit: "RecallHit") -> str:
    return _SCORE_COMPONENT_BY_CHANNEL.get(
        hit.channel,
        _SCORE_COMPONENT_BY_CHANNEL.get(channel_name, channel_name),
    )


def _annotate_score_breakdown(channel_name: str, hit: "RecallHit") -> None:
    breakdown = hit.metadata.get("score_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = {}
        hit.metadata["score_breakdown"] = breakdown
    component = _score_component(channel_name, hit)
    breakdown.setdefault(component, _round_score(hit.score))


def _annotate_recall_layer(channel_name: str, hit: "RecallHit") -> None:
    layer = _RECALL_LAYER_BY_CHANNEL.get(hit.channel) or _RECALL_LAYER_BY_CHANNEL.get(channel_name)
    if not layer:
        return
    for key, value in layer.items():
        hit.metadata.setdefault(key, value)


def _merge_score_breakdown(target: "RecallHit", source: "RecallHit") -> None:
    target_breakdown = target.metadata.setdefault("score_breakdown", {})
    if not isinstance(target_breakdown, dict):
        target_breakdown = {}
        target.metadata["score_breakdown"] = target_breakdown
    source_breakdown = source.metadata.get("score_breakdown", {})
    if not isinstance(source_breakdown, dict):
        return
    for key, value in source_breakdown.items():
        try:
            score = _round_score(float(value))
        except (TypeError, ValueError):
            continue
        try:
            existing_score = _round_score(float(target_breakdown.get(key, 0.0)))
        except (TypeError, ValueError):
            existing_score = 0.0
        target_breakdown[key] = max(existing_score, score)


def should_run_literal_search(query: str, max_chars: int = 80) -> bool:
    """Whether an exact/literal channel is likely worth running."""
    import re

    text = (query or "").strip()
    if not text or len(text) > max_chars:
        return False
    if re.search(r"[「『“\"'`]([^」』”\"'`]{2,80})[」』”\"'`]", text):
        return True
    if re.search(_LITERAL_TRIGGER + r"[\u4e00-\u9fffA-Za-z0-9_-]{2,40}", text):
        return True
    if re.search(r"[A-Za-z][A-Za-z0-9_-]{2,40}", text):
        return True
    compact = re.sub(r"[\s：:，,。.!！?？、；;“”‘’'\"`「」『』（）()【】\[\]]+", "", text)
    if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", compact):
        return not any(stop in compact for stop in _GENERIC_CJK_LITERAL_STOPS)
    return False


def literal_query_terms(query: str, max_terms: int = 16) -> list[str]:
    """Extract literal terms worth checking with exact/ILIKE search.

    This is intentionally heuristic. It is meant to rescue short CJK terms,
    proper nouns, codenames, and quoted phrases that embeddings often blur.
    """
    import re

    text = (query or "").strip()
    if not text:
        return []

    seen: set[str] = set()
    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip(" \t\r\n：:，,。.!！?？、；;“”‘’'\"`「」『』（）()[]【】")
        if len(term) < 2 or term in seen:
            return
        seen.add(term)
        terms.append(term)

    # Explicit quotes are the strongest signal.
    for m in re.finditer(r"[「『“\"'`]([^」』”\"'`]{2,80})[」』”\"'`]", text):
        add(m.group(1))

    # Common Chinese retrieval prompts: "你搜蘸水菜？", "查一下邦德".
    for m in re.finditer(_LITERAL_TRIGGER + r"([\u4e00-\u9fffA-Za-z0-9_-]{2,40})", text):
        add(m.group(1))

    def strip_query_prefix(seq: str) -> str:
        prefixes = (
            "你帮我", "帮我", "能不能", "可不可以", "你能", "我们", "刚才",
            "上次", "上一段", "上个", "这个", "那个", "你", "我",
            "搜一下", "查一下", "找一下", "搜索", "检索", "搜", "查", "找",
            "记得", "说过", "提到", "关于",
        )
        suffixes = ("吗", "呢", "啊", "呀", "么", "吧", "不")
        changed = True
        while changed:
            changed = False
            for p in prefixes:
                if seq.startswith(p) and len(seq) - len(p) >= 2:
                    seq = seq[len(p):]
                    changed = True
            for s in suffixes:
                if seq.endswith(s) and len(seq) - len(s) >= 2:
                    seq = seq[:-len(s)]
                    changed = True
        return seq

    for seq in re.findall(r"[\u4e00-\u9fff]{2,24}", text):
        cleaned = strip_query_prefix(seq)
        add(cleaned)
        # If the prompt glued command words to the noun, keep a small set of
        # longer ngrams. "你搜蘸水菜" should still produce "蘸水菜".
        if len(seq) <= 12:
            for n in range(min(8, len(seq)), 1, -1):
                for i in range(0, len(seq) - n + 1):
                    add(seq[i:i + n])
                    if len(terms) >= max_terms:
                        return terms

    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,40}", text):
        add(token)
        if len(terms) >= max_terms:
            break

    return terms[:max_terms]


@dataclass
class RecallHit:
    """统一的命中结构。各通道结果归一到这个类。"""
    source_id: int
    title: str
    content: str
    score: float                # 通道内得分（0-1 归一化）
    channel: str                # 哪条通道送来的：vector / fts / literal / raw_events / graph / emotion / perception
    metadata: dict = field(default_factory=dict)


@dataclass
class RecallResult:
    """召回总结。injection_text 是直接可贴进 system prompt 的字符串。"""
    hits: list[RecallHit]
    channels_used: list[str]
    injection_text: str
    channel_counts: dict[str, int] = field(default_factory=dict)
    trace: dict = field(default_factory=dict)
    layers: dict[str, Any] = field(default_factory=dict)


class RecallPipeline:
    """多通道召回 · 端到端

    每条通道是一个 callable，不传就跳过。这样调用方按需开关，
    既能 5 路全开做"满血注入"，也能只开向量做最小召回。
    """

    def __init__(
        self,
        vector_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        fts_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        graph_expand: Optional[Callable[[list[int], int], list[RecallHit]]] = None,
        emotion_resonate: Optional[Callable[[str, int], list[RecallHit]]] = None,
        spontaneous: Optional[Callable[[int], list[RecallHit]]] = None,
        raw_events_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        literal_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        recent_raw_chunk_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        cold_archive_search: Optional[Callable[[str, int], list[RecallHit]]] = None,
        rerank: Optional[Callable[[str, list[RecallHit], int], list[RecallHit]]] = None,
        query_expand: Optional[Callable[[str], list[str]]] = None,
        vector_top_k: int = 8,
        fts_top_k: int = 5,
        raw_events_top_k: int = 5,
        literal_top_k: int = 3,
        literal_query_max_chars: int = 80,
        recent_raw_chunk_top_k: int = 1,
        cold_archive_top_k: int = 3,
        graph_hops: int = 2,
        graph_max_expand: int = 10,
        emotion_top_k: int = 2,
        perception_top_k: int = 1,
        fts_floor: float = 0.45,
        raw_events_floor: float = 0.30,
        final_top_k: int = 10,
        injection_budget_chars: int = 4000,
        fusion: str = "rrf",
        channel_weights: Optional[dict[str, float]] = None,
        rrf_k: int = 60,
        minmax_neutral_score: float = 0.5,
        output_mode: str = "flat",
        source_neighborhood_budget_chars: int = 600,
        fallback_archive_budget_chars: int = 360,
    ):
        """
        完整管线：Query Expansion → 三层检索 + 兜底 → 合并去重 → OB 评分 → 精排

            Stage 0: query_expand （可选 · LLM 扩展 2-4 个搜索角度，覆盖同义词/
                                    相关概念/情绪词。不传则只用原始 query）
            Stage 1: vector （语义主路 · pgvector ANN。若传了 query_expand，
                              每个扩展 query 串行搜索，结果合并去重取最高分）
            Stage 2: fts    （兜底 1 · 当 vector top_score < fts_floor 时启动；
                              查 curated_memories 的 tsvector）
            Stage 3: raw_events （兜底 2 · 当 vector top_score < raw_events_floor 时
                                   启动；查 lmc5_raw_events 原始对话日志的 tsvector。
                                   原始事件比 curated 多一个量级，专门救 vector + curated
                                   都不行的"陌生关键词"场景，例如刚配的新代号、新人名）
            Independent: literal_search （短查询、中文专名、带引号查询的字面命中；
                                          不被 vector top_score 门控）
            Independent: recent_raw_chunk_search （可选急救桥；极小 top_k，补
                                                   SessionEnd 到夜间精炼之间的空窗）
            Last resort: cold_archive_search （可选冷归档；只有主召回/FTS/raw/literal
                                               全空时才开箱）

        其他通道（literal / raw_chunk / graph / emotion / spontaneous）独立并行，
        不在 fallback 链上。

        Args:
            vector_search: (query, top_k) → vector hits
            fts_search: (query, top_k) → FTS hits over curated memories
            raw_events_search: (query, top_k) → FTS hits over raw events journal
            literal_search: (query, top_k) → exact/literal hits over raw events
                            or another literal index; runs independently for
                            short literal-looking queries
            recent_raw_chunk_search: (query, top_k) → temporary recent-session
                                     chunk hits; keep top_k tiny
            cold_archive_search: (query, top_k) → cold/session archive hits.
                                 It is never authority and only runs when the
                                 warmer layers found nothing.
            graph_expand: (seed_ids, hops) → 关系图扩展 hits
            emotion_resonate: (query, top_k) → Russell 情绪联想 hits
            spontaneous: (top_k) → 自发浮现 hits（不查询，按概率冒出）
            rerank: (query, hits, top_k) → 重排（可接 DeepSeek/任意 LLM）
            query_expand: (query) → [expanded_query_1, ...] （可选 · LLM 查询扩展。
                          不传则只用原始 query。推荐接 DeepSeek 等便宜模型）
            fts_floor: 向量召回最高分低于此值时走 FTS 兜底
            raw_events_floor: 向量召回最高分低于此值时再启动 raw events 兜底
                              （比 fts_floor 更严——只在真正捞不到时才查原始事件）
            literal_query_max_chars: 超过这个长度不跑 literal_search，避免长 prompt
                                     对 raw_events 做无意义 ILIKE
            injection_budget_chars: 最终拼到 system prompt 的字符上限
            fusion: 通道融合策略。raw=旧行为（直接比原始分）；rrf=Reciprocal
                    Rank Fusion，默认推荐；minmax=每条通道内 min-max 到 [0,1]
                    后乘权重，保留为可选对照。
            channel_weights: 通道先验权重，覆盖默认值。
            rrf_k: RRF 平滑常数，默认 60。
            minmax_neutral_score: minmax 遇到单条命中/全同分时的中性分。
            output_mode: flat=旧输出；layered=额外生成按证据角色分开的召回结构和分层注入文本。
            source_neighborhood_budget_chars: layered 模式下原文邻域层的字符预算。
            fallback_archive_budget_chars: layered 模式下兜底档案层的字符预算。
        """
        for name, fn in (
            ("vector_search", vector_search),
            ("fts_search", fts_search),
            ("raw_events_search", raw_events_search),
            ("literal_search", literal_search),
            ("recent_raw_chunk_search", recent_raw_chunk_search),
            ("cold_archive_search", cold_archive_search),
            ("graph_expand", graph_expand),
            ("emotion_resonate", emotion_resonate),
            ("spontaneous", spontaneous),
            ("rerank", rerank),
            ("query_expand", query_expand),
        ):
            if fn is not None and not callable(fn):
                raise TypeError(
                    f"RecallPipeline: {name} must be callable or None, "
                    f"got {type(fn).__name__}"
                )
        self.vector_search = vector_search
        self.fts_search = fts_search
        self.raw_events_search = raw_events_search
        self.literal_search = literal_search
        self.recent_raw_chunk_search = recent_raw_chunk_search
        self.cold_archive_search = cold_archive_search
        self.query_expand = query_expand
        self.graph_expand = graph_expand
        self.emotion_resonate = emotion_resonate
        self.spontaneous = spontaneous
        self.rerank = rerank

        self.vector_top_k = vector_top_k
        self.fts_top_k = fts_top_k
        self.raw_events_top_k = raw_events_top_k
        self.literal_top_k = literal_top_k
        self.literal_query_max_chars = literal_query_max_chars
        self.recent_raw_chunk_top_k = recent_raw_chunk_top_k
        self.cold_archive_top_k = cold_archive_top_k
        self.graph_hops = graph_hops
        self.graph_max_expand = graph_max_expand
        self.emotion_top_k = emotion_top_k
        self.perception_top_k = perception_top_k
        self.fts_floor = fts_floor
        self.raw_events_floor = raw_events_floor
        self.final_top_k = final_top_k
        self.injection_budget_chars = injection_budget_chars
        fusion = str(fusion or "rrf").lower()
        if fusion not in _SCORE_FUSIONS:
            raise ValueError(
                f"RecallPipeline: fusion must be one of {sorted(_SCORE_FUSIONS)}, "
                f"got {fusion!r}"
            )
        self.fusion = fusion
        self.channel_weights = dict(_DEFAULT_CHANNEL_WEIGHTS)
        if channel_weights:
            for name, weight in channel_weights.items():
                self.channel_weights[str(name)] = float(weight)
        self.rrf_k = int(rrf_k)
        self.minmax_neutral_score = float(minmax_neutral_score)
        output_mode = str(output_mode or "flat").lower()
        if output_mode not in {"flat", "layered"}:
            raise ValueError("RecallPipeline: output_mode must be 'flat' or 'layered'")
        self.output_mode = output_mode
        self.source_neighborhood_budget_chars = int(source_neighborhood_budget_chars)
        self.fallback_archive_budget_chars = int(fallback_archive_budget_chars)

    def _safe_call(self, name: str, fn: Callable, *args) -> list[RecallHit]:
        """每条通道都包异常——一条断了不影响其他通道"""
        import logging
        log = logging.getLogger("lmc5.recall_pipeline")
        try:
            result = fn(*args) or []
            hits = [h for h in result if isinstance(h, RecallHit)]
            for hit in hits:
                _annotate_score_breakdown(name, hit)
                _annotate_recall_layer(name, hit)
            return hits
        except Exception as e:
            log.warning("recall channel '%s' failed: %s", name, e)
            return []

    def _dedup_key(self, hit: RecallHit) -> tuple[str, int]:
        """Return a stable dedup key.

        Curated-memory channels intentionally share ids. Raw events and recent
        raw chunks live in different tables/owner namespaces, so their integer
        ids must not collide with curated memory ids.
        """
        namespace = hit.metadata.get("namespace")
        if namespace:
            return (str(namespace), int(hit.source_id))
        if hit.channel in {"raw_events", "literal"}:
            return ("raw_events", int(hit.source_id))
        if hit.channel == "raw_chunk":
            return ("raw_chunk", int(hit.source_id))
        return ("curated", int(hit.source_id))

    def _apply_score_fusion(
        self,
        channels: list[tuple[str, list[RecallHit]]],
    ) -> list[tuple[str, list[RecallHit]]]:
        """Put channel scores onto a comparable scale before cross-channel merge.

        raw keeps the old behavior. minmax compares relative confidence within
        each channel, then applies a channel prior. rrf ignores absolute scores
        and fuses by within-channel rank. Raw channel scores stay in
        score_breakdown; fusion diagnostics are added alongside them.
        """
        if self.fusion == "raw":
            return channels

        fused_channels: list[tuple[str, list[RecallHit]]] = []
        for channel_name, hits in channels:
            if not hits:
                fused_channels.append((channel_name, hits))
                continue
            weight = self.channel_weights.get(channel_name, 1.0)
            ranked = sorted(hits, key=lambda h: h.score, reverse=True)
            if self.fusion == "minmax":
                scores = [float(h.score or 0.0) for h in ranked]
                lo = min(scores)
                hi = max(scores)
                span = hi - lo
                for rank, hit in enumerate(ranked, start=1):
                    raw = float(hit.score or 0.0)
                    norm = (
                        self.minmax_neutral_score
                        if span == 0
                        else (raw - lo) / span
                    )
                    weighted = norm * weight
                    component = _score_component(channel_name, hit)
                    breakdown = hit.metadata.setdefault("score_breakdown", {})
                    if isinstance(breakdown, dict):
                        breakdown[f"{component}_rank"] = rank
                        breakdown[f"{component}_normalized"] = _round_score(norm)
                        breakdown[f"{component}_weighted"] = _round_score(weighted)
                    hit.metadata["score_fusion"] = "minmax"
                    hit.score = weighted
            elif self.fusion == "rrf":
                for rank, hit in enumerate(ranked, start=1):
                    rrf = 1.0 / (self.rrf_k + rank)
                    weighted = rrf * weight
                    component = _score_component(channel_name, hit)
                    breakdown = hit.metadata.setdefault("score_breakdown", {})
                    if isinstance(breakdown, dict):
                        breakdown[f"{component}_rank"] = rank
                        breakdown[f"{component}_rrf"] = _round_score(rrf)
                        breakdown[f"{component}_weighted_rrf"] = _round_score(weighted)
                    hit.metadata["score_fusion"] = "rrf"
                    hit.score = weighted
            fused_channels.append((channel_name, ranked))
        return fused_channels

    def _merge_dedup(self, channels: list[tuple[str, list[RecallHit]]]) -> list[RecallHit]:
        """同一 namespace+source_id 命中时合并：raw 取最高分，融合分累加。"""
        merged: dict[tuple[str, int], RecallHit] = {}
        for channel_name, hits in channels:
            for h in hits:
                key = self._dedup_key(h)
                if key in merged:
                    existing = merged[key]
                    if self.fusion == "raw":
                        if h.score > existing.score:
                            existing.score = h.score
                    else:
                        # Fusion scores are additive evidence. A memory found
                        # independently by vector+graph should be rewarded, not
                        # reduced back to max(single-channel).
                        existing.score += h.score
                    existing.metadata.setdefault("channels", set()).add(h.channel)
                    existing.metadata["channels"].add(channel_name)
                    _merge_score_breakdown(existing, h)
                else:
                    h.metadata.setdefault("channels", set()).add(h.channel)
                    merged[key] = h
        # 把 set 转成 sorted list 便于序列化
        for h in merged.values():
            if "channels" in h.metadata and isinstance(h.metadata["channels"], set):
                h.metadata["channels"] = sorted(h.metadata["channels"])
        return list(merged.values())

    def _build_injection_text(self, hits: list[RecallHit]) -> str:
        """拼一段可以直接注入 system prompt 的字符串。控制总长度。"""
        if not hits:
            return ""
        lines = ["[Recalled context]"]
        used = len(lines[0])
        for h in hits:
            channel_tags = h.metadata.get("channels", [h.channel])
            tag_str = ",".join(channel_tags) if channel_tags else h.channel
            layer = h.metadata.get("recall_layer") or h.channel
            role = h.metadata.get("evidence_role") or "evidence"
            line = f"- [{layer} {role}; {tag_str} score={h.score:.2f}] {h.title}: {h.content[:200]}"
            if used + len(line) + 1 > self.injection_budget_chars:
                lines.append("... (truncated by injection_budget_chars)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def _hit_to_layer_item(self, hit: RecallHit, *, content_limit: int) -> dict[str, Any]:
        return {
            "rank": hit.metadata.get("rank"),
            "source_id": hit.source_id,
            "title": hit.title[:80],
            "content": (hit.content or "")[:content_limit],
            "score": _round_score(hit.score),
            "channel": hit.channel,
            "channels": hit.metadata.get("channels", [hit.channel]),
            "recall_layer": hit.metadata.get("recall_layer") or hit.channel,
            "recall_tier": hit.metadata.get("recall_tier"),
            "evidence_role": hit.metadata.get("evidence_role"),
            "source_label": hit.metadata.get("source_label"),
            "score_breakdown": hit.metadata.get("score_breakdown", {}),
        }

    def _build_layered_output(self, hits: list[RecallHit]) -> dict[str, Any]:
        """Build the optional four-layer recall output.

        Flat output stays the default for backwards compatibility. Layered mode
        keeps authority, navigation, association, and fallback evidence visibly
        separate. Storage backends are swappable; the layer contract is about
        evidence role, not whether the rows came from PG, SQLite, or a custom
        adapter.
        """
        layers: dict[str, Any] = {
            "mode": "layered",
            "reference_contract": dict(_KELIN_216_RUNTIME_CONTRACT),
            "rules": [
                "layers must not impersonate each other",
                "source_neighborhood is short navigation, not authority",
                "source_neighborhood text budget must not exceed main_recall text budget",
                "fallback_archive is last-resort evidence, not authority",
            ],
            "main_recall": {
                "label": "authority",
                "description": "Primary curated vector/keyword recall and curated side surfaces.",
                "hits": [],
            },
            "source_neighborhood": {
                "label": "navigation",
                "description": "Short literal/raw-chunk navigation hints around matched evidence.",
                "budget_chars": self.source_neighborhood_budget_chars,
                "hits": [],
            },
            "graph_expansion": {
                "label": "association",
                "description": "Y-graph 1-2 hop relation expansion.",
                "hits": [],
            },
            "fallback_archive": {
                "label": "last_resort",
                "description": "Raw-events / cold-session archive fallback, kept out of authority ranking.",
                "budget_chars": self.fallback_archive_budget_chars,
                "hits": [],
            },
        }

        main_hits: list[RecallHit] = []
        neighborhood_hits: list[RecallHit] = []
        graph_hits: list[RecallHit] = []
        fallback_hits: list[RecallHit] = []
        for hit in hits:
            namespace = str(hit.metadata.get("namespace", "")).lower()
            evidence_role = str(hit.metadata.get("evidence_role", "")).lower()
            recall_layer = str(hit.metadata.get("recall_layer", "")).lower()
            if (
                evidence_role == "last_resort"
                or hit.channel == "raw_events"
                or recall_layer in {"raw_events_fts", "cold_archive", "session_archive"}
                or "cold" in namespace
                or "archive" in namespace
            ):
                fallback_hits.append(hit)
            elif hit.channel == "graph" or evidence_role == "association":
                graph_hits.append(hit)
            elif hit.channel in {"literal", "raw_chunk"} or evidence_role == "navigation":
                neighborhood_hits.append(hit)
            else:
                main_hits.append(hit)

        main_budget = sum(min(len(h.content or ""), 220) for h in main_hits)
        if main_hits:
            neighborhood_budget = min(
                self.source_neighborhood_budget_chars,
                max(80, main_budget),
            )
        else:
            neighborhood_budget = min(self.source_neighborhood_budget_chars, 240)
        layers["source_neighborhood"]["budget_chars"] = neighborhood_budget

        layers["main_recall"]["hits"] = [
            self._hit_to_layer_item(hit, content_limit=220)
            for hit in main_hits
        ]
        layers["graph_expansion"]["hits"] = [
            self._hit_to_layer_item(hit, content_limit=180)
            for hit in graph_hits
        ]

        used = 0
        neighborhood_items: list[dict[str, Any]] = []
        for hit in neighborhood_hits:
            remaining = neighborhood_budget - used
            if remaining <= 0:
                break
            limit = min(120, remaining)
            item = self._hit_to_layer_item(hit, content_limit=limit)
            used += len(item["content"])
            neighborhood_items.append(item)
        layers["source_neighborhood"]["hits"] = neighborhood_items
        layers["source_neighborhood"]["used_chars"] = used

        fallback_budget = min(
            self.fallback_archive_budget_chars,
            max(80, main_budget or 240),
        )
        layers["fallback_archive"]["budget_chars"] = fallback_budget
        used = 0
        fallback_items: list[dict[str, Any]] = []
        for hit in fallback_hits:
            remaining = fallback_budget - used
            if remaining <= 0:
                break
            limit = min(140, remaining)
            item = self._hit_to_layer_item(hit, content_limit=limit)
            used += len(item["content"])
            fallback_items.append(item)
        layers["fallback_archive"]["hits"] = fallback_items
        layers["fallback_archive"]["used_chars"] = used
        return layers

    def _build_layered_injection_text(self, layers: dict[str, Any]) -> str:
        lines = ["[Layered recalled context]"]
        sections = [
            ("main_recall", "主召回 / authority"),
            ("source_neighborhood", "原文邻域 / navigation"),
            ("graph_expansion", "图扩展 / association"),
            ("fallback_archive", "兜底档案 / fallback"),
        ]
        used = len(lines[0])
        for key, title in sections:
            layer = layers.get(key) or {}
            hits = layer.get("hits") or []
            if not hits:
                continue
            header = f"\n## {title}"
            if used + len(header) > self.injection_budget_chars:
                break
            lines.append(header)
            used += len(header)
            for item in hits:
                line = (
                    f"- [{item.get('recall_layer')} {item.get('evidence_role')} "
                    f"score={item.get('score'):.2f}] {item.get('title')}: {item.get('content')}"
                )
                if used + len(line) + 1 > self.injection_budget_chars:
                    lines.append("... (truncated by injection_budget_chars)")
                    return "\n".join(lines)
                lines.append(line)
                used += len(line) + 1
        return "\n".join(lines)

    def _build_trace(
        self,
        query: str,
        queries: list[str],
        channels_used: list[str],
        channel_counts: dict[str, int],
        hits: list[RecallHit],
        cascade_trace: dict[str, Any],
    ) -> dict:
        """Build a compact explain trace without copying recalled content."""
        trace_hits = []
        layers_used: list[str] = []
        for rank, hit in enumerate(hits, start=1):
            breakdown = hit.metadata.get("score_breakdown", {})
            if not isinstance(breakdown, dict):
                breakdown = {}
            layer = str(hit.metadata.get("recall_layer") or hit.channel)
            if layer not in layers_used:
                layers_used.append(layer)
            trace_hits.append({
                "rank": rank,
                "namespace": self._dedup_key(hit)[0],
                "source_id": hit.source_id,
                "title": hit.title[:80],
                "score": _round_score(hit.score),
                "score_breakdown": breakdown,
                "channels": hit.metadata.get("channels", [hit.channel]),
                "recall_layer": layer,
                "recall_tier": hit.metadata.get("recall_tier"),
                "evidence_role": hit.metadata.get("evidence_role"),
                "source_label": hit.metadata.get("source_label"),
            })
        return {
            "query_preview": str(query or "")[:160],
            "expanded_queries": [str(q)[:160] for q in queries],
            "channels_used": channels_used,
            "channel_counts": channel_counts,
            "layers_used": layers_used,
            "priority": "curated_vector -> curated_fts -> raw_events_fts; side channels stay labeled",
            "reference_contract": dict(_KELIN_216_RUNTIME_CONTRACT),
            "cascade": cascade_trace,
            "hits": trace_hits,
        }

    def recall(
        self,
        query: str,
        seed_ids: Optional[list[int]] = None,
    ) -> RecallResult:
        """主入口。给一段用户消息，返回 RecallResult。

        seed_ids 可选——首次召回会从 vector hits 自动算 seed 给 graph_expand 用，
        但调用方可以显式传（例如沿用上一轮的 hits）。
        """
        import logging
        log = logging.getLogger("lmc5.recall_pipeline")
        channel_results: list[tuple[str, list[RecallHit]]] = []
        channels_used: list[str] = []
        cascade_trace: dict[str, Any] = {
            "mode": "primary_first",
            "stages": [
                "curated_vector",
                "curated_fts",
                "raw_events_fts",
            ],
            "fts_floor": self.fts_floor,
            "raw_events_floor": self.raw_events_floor,
            "top_vector_score": 0.0,
            "fts_checked": False,
            "raw_events_checked": False,
            "literal_checked": False,
            "recent_raw_chunk_checked": False,
            "cold_archive_checked": False,
            "cold_archive_policy": "only_when_warmer_layers_empty",
        }

        # 0. Query Expansion（可选 · LLM 扩展多角度搜索词）
        queries = [query]
        if self.query_expand is not None:
            try:
                expanded = self.query_expand(query)
                if expanded and isinstance(expanded, list):
                    seen = {query}
                    for q in expanded:
                        q = str(q).strip()
                        if q and q not in seen:
                            seen.add(q)
                            queries.append(q)
                    queries = queries[:5]
                    if len(queries) > 1:
                        log.info("recall: query_expand produced %d queries", len(queries))
            except Exception as e:
                log.warning("recall: query_expand failed, using original query: %s", e)

        # 1. 向量召回（主路 · 每个扩展 query 串行搜索，合并去重取最高分）
        vector_hits: list[RecallHit] = []
        if self.vector_search is not None:
            vec_merged: dict[int, RecallHit] = {}
            for q in queries:
                hits = self._safe_call("vector", self.vector_search,
                                       q, self.vector_top_k)
                for h in hits:
                    if h.source_id not in vec_merged or h.score > vec_merged[h.source_id].score:
                        vec_merged[h.source_id] = h
            vector_hits = sorted(vec_merged.values(), key=lambda h: h.score, reverse=True)
            vector_hits = vector_hits[:self.vector_top_k]
            if vector_hits:
                channel_results.append(("vector", vector_hits))
                channels_used.append("vector")

        # 1b. 字面精确检索 · 不被 vector top_score 门控。
        #     只对短查询/中文专名/带引号查询启用，避免长 prompt 扫 raw_events。
        literal_hits: list[RecallHit] = []
        if (
            self.literal_search is not None
            and should_run_literal_search(query, self.literal_query_max_chars)
        ):
            cascade_trace["literal_checked"] = True
            hits = self._safe_call("literal", self.literal_search,
                                   query, self.literal_top_k)
            literal_hits = sorted(hits, key=lambda h: h.score, reverse=True)
            literal_hits = literal_hits[:self.literal_top_k]
            if literal_hits:
                channel_results.append(("literal", literal_hits))
                channels_used.append("literal")
                log.info("recall: literal search added %d hits", len(literal_hits))

        # 2. FTS 兜底 1 · curated_memories（向量分都低时才走，避免空回）
        top_vec_score = max((h.score for h in vector_hits), default=0.0)
        fts_hits: list[RecallHit] = []
        cascade_trace["top_vector_score"] = _round_score(top_vec_score)
        if self.fts_search is not None and top_vec_score < self.fts_floor:
            cascade_trace["fts_checked"] = True
            fts_merged: dict[int, RecallHit] = {}
            for q in queries:
                hits = self._safe_call("fts", self.fts_search, q, self.fts_top_k)
                for h in hits:
                    if h.source_id not in fts_merged or h.score > fts_merged[h.source_id].score:
                        fts_merged[h.source_id] = h
            fts_hits = sorted(fts_merged.values(), key=lambda h: h.score, reverse=True)
            fts_hits = fts_hits[:self.fts_top_k]
            if fts_hits:
                channel_results.append(("fts", fts_hits))
                channels_used.append("fts")
                log.info("recall: vector top_score=%.2f < %.2f, FTS fallback added %d hits",
                         top_vec_score, self.fts_floor, len(fts_hits))

        # 2b. FTS 兜底 2 · raw events journal（更严苛的阈值——只有真正捞不到时才查
        #     原始对话日志，因为这层数据量大、噪声多）
        raw_hits: list[RecallHit] = []
        if self.raw_events_search is not None and top_vec_score < self.raw_events_floor:
            cascade_trace["raw_events_checked"] = True
            raw_merged: dict[int, RecallHit] = {}
            for q in queries:
                hits = self._safe_call("raw_events", self.raw_events_search,
                                       q, self.raw_events_top_k)
                for h in hits:
                    if h.source_id not in raw_merged or h.score > raw_merged[h.source_id].score:
                        raw_merged[h.source_id] = h
            raw_hits = sorted(raw_merged.values(), key=lambda h: h.score, reverse=True)
            raw_hits = raw_hits[:self.raw_events_top_k]
            if raw_hits:
                channel_results.append(("raw_events", raw_hits))
                channels_used.append("raw_events")
                log.info("recall: vector top_score=%.2f < %.2f, raw_events fallback added %d hits",
                         top_vec_score, self.raw_events_floor, len(raw_hits))

        # 2c. 最近 session raw_chunk 急救桥 · 可选、极小额度。
        #     这不是长期记忆，只补 SessionEnd → 夜间 hippocampus 之间的短空窗。
        if self.recent_raw_chunk_search is not None:
            cascade_trace["recent_raw_chunk_checked"] = True
            chunk_hits = self._safe_call("raw_chunk", self.recent_raw_chunk_search,
                                         query, self.recent_raw_chunk_top_k)
            chunk_hits = sorted(chunk_hits, key=lambda h: h.score, reverse=True)
            chunk_hits = chunk_hits[:self.recent_raw_chunk_top_k]
            if chunk_hits:
                channel_results.append(("raw_chunk", chunk_hits))
                channels_used.append("raw_chunk")
                log.info("recall: recent raw_chunk bridge added %d hits", len(chunk_hits))

        # 2d. 冷归档 · Kelin 216 对齐：前面几层全无命中时才开箱。
        #     这层是 last-resort evidence，不能混进主召回权威层。
        if (
            self.cold_archive_search is not None
            and not vector_hits
            and not fts_hits
            and not raw_hits
            and not literal_hits
        ):
            cascade_trace["cold_archive_checked"] = True
            cold_hits = self._safe_call("cold_archive", self.cold_archive_search,
                                        query, self.cold_archive_top_k)
            cold_hits = sorted(cold_hits, key=lambda h: h.score, reverse=True)
            cold_hits = cold_hits[:self.cold_archive_top_k]
            if cold_hits:
                channel_results.append(("cold_archive", cold_hits))
                channels_used.append("cold_archive")
                log.info("recall: cold archive fallback added %d hits", len(cold_hits))

        # 3. 关系图 2 跳扩展（用 vector hits 当种子）
        if self.graph_expand is not None:
            if seed_ids is None:
                # Kelin 216 aligns relation/time association to the warm
                # authoritative layer. If vector is empty but curated FTS found
                # a current memory, it can still seed the safe graph. Raw/cold
                # evidence never seeds association.
                curated_seed_hits = vector_hits or fts_hits
                seed_ids = [h.source_id for h in curated_seed_hits[:3]]
            if seed_ids:
                graph_hits = self._safe_call("graph", self.graph_expand,
                                             seed_ids, self.graph_hops)
                if graph_hits:
                    channel_results.append(("graph", graph_hits[:self.graph_max_expand]))
                    channels_used.append("graph")

        # 4. 情绪联想
        if self.emotion_resonate is not None:
            emo_hits = self._safe_call("emotion", self.emotion_resonate,
                                       query, self.emotion_top_k)
            if emo_hits:
                channel_results.append(("emotion", emo_hits))
                channels_used.append("emotion")

        # 5. 自发浮现
        if self.spontaneous is not None:
            perc_hits = self._safe_call("perception", self.spontaneous,
                                        self.perception_top_k)
            if perc_hits:
                channel_results.append(("perception", perc_hits))
                channels_used.append("perception")

        # 融合 + 合并去重
        channel_results = self._apply_score_fusion(channel_results)
        merged = self._merge_dedup(channel_results)
        channel_counts = {name: len(hits) for name, hits in channel_results}

        # 6. rerank（可选）
        if self.rerank is not None and merged:
            try:
                merged = self.rerank(query, merged, self.final_top_k)
            except Exception as e:
                log.warning("recall: rerank failed, falling back to score sort: %s", e)
                merged.sort(key=lambda h: h.score, reverse=True)
                merged = merged[:self.final_top_k]
        else:
            merged.sort(key=lambda h: h.score, reverse=True)
            merged = merged[:self.final_top_k]

        for rank, hit in enumerate(merged, start=1):
            breakdown = hit.metadata.setdefault("score_breakdown", {})
            if isinstance(breakdown, dict):
                breakdown["final"] = _round_score(hit.score)
            hit.metadata["injected"] = True
            hit.metadata["rank"] = rank

        layers = self._build_layered_output(merged) if self.output_mode == "layered" else {}
        injection_text = (
            self._build_layered_injection_text(layers)
            if self.output_mode == "layered"
            else self._build_injection_text(merged)
        )
        cascade_trace["channels_used"] = list(channels_used)
        cascade_trace["output_mode"] = self.output_mode
        trace = self._build_trace(query, queries, channels_used, channel_counts, merged, cascade_trace)
        return RecallResult(
            hits=merged,
            channels_used=channels_used,
            injection_text=injection_text,
            channel_counts=channel_counts,
            trace=trace,
            layers=layers,
        )


# === 通道适配器 helper（把现有模块包装成 callable）===

def vector_search_adapter(
    store,
    query_embedder: Callable[[str], list[float]],
    owner_type: str = "curated",
):
    """把 PgvectorStore 包成 vector_search callable。"""
    def call(query: str, top_k: int) -> list[RecallHit]:
        vec = query_embedder(query)
        hits = store.search_vectors(query_vec=vec, owner_type=owner_type, top_k=top_k)
        return [
            RecallHit(
                source_id=h.owner_id,
                title="",
                content=h.text_preview,
                score=h.similarity,
                channel="vector",
                metadata={"namespace": h.owner_type},
            )
            for h in hits
        ]
    return call


def fts_search_adapter(conn, table: str = "lmc5_curated_memories"):
    """PostgreSQL tsvector 全文检索适配器。需要表上有 content_tsv 列 + GIN 索引。

    注意：调用方负责提供已建好 content_tsv 触发器的表；schema.sql 里没默认建
    （tsvector 的语言配置因部署而异——'simple' 兼容多语种但不分词中文，'jieba'
    需要安装 zhparser 等扩展）。
    """
    def call(query: str, top_k: int) -> list[RecallHit]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, title, content, "
                f"  ts_rank(content_tsv, plainto_tsquery('simple', %s)) AS rank "
                f"FROM {table} "
                f"WHERE version_status='current' "
                f"  AND content_tsv @@ plainto_tsquery('simple', %s) "
                f"ORDER BY rank DESC LIMIT %s",
                (query, query, top_k),
            )
            rows = cur.fetchall()
        # FTS rank 归一化到 [0, 1]：rank/15 是常见经验值
        return [
            RecallHit(
                source_id=int(r[0]),
                title=r[1] or "",
                content=(r[2] or "")[:300],
                score=min(1.0, float(r[3]) / 15.0),
                channel="fts",
            )
            for r in rows
        ]
    return call


def raw_events_search_adapter(
    conn,
    table: str = "lmc5_raw_events",
    rank_normalizer: float = 12.0,
    recent_days: int = 90,
):
    """三层检索的兜底层 · 查 raw events journal 的 tsvector。

    设计取舍：
      - **比 curated FTS 更宽**：raw events 比 curated 多一个量级，所以即使
        vector + curated 都没命中，原始对话日志里可能仍然有关键词出现过
      - **限近 N 天**：raw events 增长无限，不卡时间窗会越查越慢
      - **rank 归一化**：默认除 12.0（比 curated FTS 的 15 稍宽，因为原始
        对话文本短匹配多）

    需要 lmc5_raw_events 表上有 content_tsv 列 + GIN 索引（schema.sql 提供）。
    """
    def call(query: str, top_k: int) -> list[RecallHit]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, role, content, "
                f"  ts_rank(content_tsv, plainto_tsquery('simple', %s)) AS rank, "
                f"  created_at, session_id "
                f"FROM {table} "
                f"WHERE content_tsv @@ plainto_tsquery('simple', %s) "
                f"  AND created_at >= NOW() - (%s || ' days')::interval "
                f"ORDER BY rank DESC, created_at DESC LIMIT %s",
                (query, query, str(recent_days), top_k),
            )
            rows = cur.fetchall()
        return [
            RecallHit(
                source_id=int(r[0]),
                title=f"[raw {r[1] or '?'}] {str(r[4])[:10]}",
                content=(r[2] or "")[:400],
                score=min(1.0, float(r[3]) / rank_normalizer),
                channel="raw_events",
                metadata={"namespace": "raw_events", "session_id": r[5], "role": r[1]},
            )
            for r in rows
        ]
    return call


def literal_raw_events_search_adapter(
    conn,
    table: str = "lmc5_raw_events",
    recent_days: int = 30,
    score: float = 0.55,
    max_terms: int = 16,
    max_content_chars: int = 240,
    include_neighbors: bool = True,
    neighbor_radius: int = 1,
):
    """Independent exact/literal search over raw events as source-neighborhood.

    Unlike raw_events_search_adapter, this is not a vector-score fallback. It is
    meant for short CJK terms, proper nouns, codenames, and quoted phrases that
    should get a chance even when vector search returned a weak semantic hit.

    Kelin 216 note: the literal hit is treated as a *navigation layer*. When
    possible we include a tiny same-session +/- neighbor window so the caller
    sees the original wording around the hit without promoting it to authority.
    """
    def call(query: str, top_k: int) -> list[RecallHit]:
        terms = literal_query_terms(query, max_terms=max_terms)
        if not terms:
            return []
        patterns = [f"%{term}%" for term in terms]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, role, content, created_at, session_id "
                f"FROM {table} "
                f"WHERE content ILIKE ANY(%s) "
                f"  AND created_at >= NOW() - (%s || ' days')::interval "
                f"ORDER BY created_at DESC LIMIT %s",
                (patterns, str(recent_days), top_k),
            )
            rows = cur.fetchall()

        neighbor_map: dict[int, str] = {}
        if include_neighbors and rows:
            with conn.cursor() as cur:
                for r in rows:
                    hit_id = int(r[0])
                    session_id = r[4]
                    if not session_id:
                        continue
                    cur.execute(
                        f"SELECT id, role, content, created_at "
                        f"FROM {table} "
                        f"WHERE session_id = %s "
                        f"  AND id BETWEEN %s AND %s "
                        f"ORDER BY id ASC",
                        (session_id, hit_id - int(neighbor_radius), hit_id + int(neighbor_radius)),
                    )
                    parts = []
                    for nid, role, content, created_at in cur.fetchall():
                        marker = "*" if int(nid) == hit_id else "-"
                        snippet = (content or "").replace("\n", " ")[:120]
                        parts.append(f"{marker} {role or '?'} {str(created_at)[:19]}: {snippet}")
                    if parts:
                        neighbor_map[hit_id] = "\n".join(parts)
        return [
            RecallHit(
                source_id=int(r[0]),
                title=f"[source neighborhood {r[1] or '?'}] {str(r[3])[:10]}",
                content=(neighbor_map.get(int(r[0])) or (r[2] or ""))[:max_content_chars],
                score=score,
                channel="literal",
                metadata={
                    "namespace": "raw_events",
                    "session_id": r[4],
                    "role": r[1],
                    "literal_terms": terms[:5],
                    "neighbor_radius": int(neighbor_radius) if include_neighbors else 0,
                },
            )
            for r in rows
        ]
    return call


def cold_archive_search_adapter(
    conn,
    table: str = "lmc5_cold_storage",
    score: float = 0.35,
    max_terms: int = 12,
    max_content_chars: int = 220,
):
    """Last-resort cold archive adapter.

    This mirrors Kelin 216's "front layers all failed, then open the cold box"
    rule. It intentionally uses a low fixed score and marks hits as
    last_resort/cold_archive so they can be inspected but never mistaken for an
    active curated fact.
    """
    def call(query: str, top_k: int) -> list[RecallHit]:
        terms = literal_query_terms(query, max_terms=max_terms) or [
            t for t in str(query or "").split() if len(t) >= 2
        ][:max_terms]
        if not terms:
            return []
        patterns = [f"%{term}%" for term in terms]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, title, content, reason, archived_at "
                f"FROM {table} "
                f"WHERE coalesce(content, '') ILIKE ANY(%s) "
                f"   OR coalesce(title, '') ILIKE ANY(%s) "
                f"ORDER BY archived_at DESC NULLS LAST LIMIT %s",
                (patterns, patterns, top_k),
            )
            rows = cur.fetchall()
        return [
            RecallHit(
                source_id=int(r[0]),
                title=f"[cold archive] {r[1] or ''}".strip(),
                content=(r[2] or "")[:max_content_chars],
                score=score,
                channel="cold_archive",
                metadata={
                    "namespace": "cold_archive",
                    "reason": r[3],
                    "archived_at": str(r[4]) if r[4] is not None else None,
                    "literal_terms": terms[:5],
                },
            )
            for r in rows
        ]
    return call


def raw_chunk_vector_search_adapter(
    store,
    query_embedder: Callable[[str], list[float]],
    owner_type: str = "raw_chunk",
    score_cap: float = 0.50,
    max_content_chars: int = 200,
):
    """Optional recent-session raw_chunk bridge.

    Callers may write temporary vectors with owner_type='raw_chunk' at
    SessionEnd, then delete them after hippocampus/consolidation digests the
    session. This adapter keeps the bridge small by capping score and content.
    """
    def call(query: str, top_k: int) -> list[RecallHit]:
        vec = query_embedder(query)
        hits = store.search_vectors(query_vec=vec, owner_type=owner_type, top_k=top_k)
        return [
            RecallHit(
                source_id=h.owner_id,
                title="[recent raw chunk]",
                content=(h.text_preview or "")[:max_content_chars],
                score=min(score_cap, h.similarity),
                channel="raw_chunk",
                metadata={"namespace": owner_type, "owner_type": h.owner_type},
            )
            for h in hits
        ]
    return call


def graph_expand_adapter(
    conn,
    table_curated: str = "lmc5_curated_memories",
    table_relations: str = "lmc5_memory_relations",
    hop1_min_strength: float = 0.4,
    hop2_min_strength: float = 0.7,
    safe_relation_types: tuple = (
        "same_event", "same_topic", "temporal_sequence",
        "emotional_link", "derived_from", "in_thread",
        "same_person", "in_episode", "instance_of",
    ),
    exclude_sources: tuple = ("thread", "concept"),
):
    """Y 轴关系图双向 2 跳扩展适配器。

    种子 → hop1 邻居（strength > hop1_min_strength）→ hop2 邻居（strength > hop2_min_strength）
    游走规则：
      - 双向：source → target UNION target → source
      - 只走活边（valid_until IS NULL）和活端点（version_status = 'current'）
      - 只走安全关系类型（review 类如 contradiction/cause_effect/supports 不自动扩展）
      - 绕开枢纽（thread/concept 度数高，会爆）
      - 自环不走

    返回 RecallHit 列表，score = 关系强度。
    """
    def call(seed_ids: list, hops: int) -> list[RecallHit]:
        if not seed_ids:
            return []
        max_hops = min(int(hops or 2), 2)
        safe_types_array = list(safe_relation_types)
        exclude_array = list(exclude_sources)

        def _hop_sql(min_strength: float) -> str:
            return f"""
            SELECT tid, MAX(strength) AS strength FROM (
                SELECT mr.target_id AS tid, mr.strength FROM {table_relations} mr
                JOIN {table_curated} cm ON cm.id = mr.target_id
                WHERE mr.source_id = ANY(%s)
                  AND mr.strength > %s
                  AND mr.valid_until IS NULL
                  AND mr.target_id != mr.source_id
                  AND mr.relation_type = ANY(%s)
                  AND cm.version_status = 'current'
                  AND cm.source != ALL(%s)
                UNION ALL
                SELECT mr.source_id AS tid, mr.strength FROM {table_relations} mr
                JOIN {table_curated} cm ON cm.id = mr.source_id
                WHERE mr.target_id = ANY(%s)
                  AND mr.strength > %s
                  AND mr.valid_until IS NULL
                  AND mr.source_id != mr.target_id
                  AND mr.relation_type = ANY(%s)
                  AND cm.version_status = 'current'
                  AND cm.source != ALL(%s)
            ) t GROUP BY tid ORDER BY strength DESC
            """

        seeds = [int(s) for s in seed_ids]
        visited = set(seeds)
        expanded: list[tuple[int, float]] = []

        with conn.cursor() as cur:
            # hop 1
            cur.execute(_hop_sql(hop1_min_strength), (
                seeds, hop1_min_strength, safe_types_array, exclude_array,
                seeds, hop1_min_strength, safe_types_array, exclude_array,
            ))
            hop1 = cur.fetchall()
            hop1_ids = []
            for tid, strength in hop1:
                if tid not in visited:
                    visited.add(tid)
                    hop1_ids.append(int(tid))
                    expanded.append((int(tid), float(strength)))

            # hop 2 (only via strong edges from hop1 anchors)
            if max_hops >= 2 and hop1_ids:
                cur.execute(_hop_sql(hop2_min_strength), (
                    hop1_ids, hop2_min_strength, safe_types_array, exclude_array,
                    hop1_ids, hop2_min_strength, safe_types_array, exclude_array,
                ))
                for tid, strength in cur.fetchall():
                    if tid not in visited:
                        visited.add(tid)
                        expanded.append((int(tid), float(strength)))

            if not expanded:
                return []

            # hydrate
            ids = [eid for eid, _ in expanded]
            cur.execute(
                f"SELECT id, title, content FROM {table_curated} "
                f"WHERE id = ANY(%s) AND version_status = 'current'",
                (ids,),
            )
            detail = {int(r[0]): (r[1] or "", (r[2] or "")[:300]) for r in cur.fetchall()}

        return [
            RecallHit(
                source_id=eid,
                title=detail.get(eid, ("", ""))[0],
                content=detail.get(eid, ("", ""))[1],
                score=strength,
                channel="graph",
            )
            for eid, strength in expanded
            if eid in detail
        ]
    return call


def emotion_resonate_adapter(
    conn,
    table_curated: str = "lmc5_curated_memories",
    user_emotion_detector: Optional[Callable[[str], Optional[tuple[float, float]]]] = None,
    eligible_categories: tuple = (
        "fragments", "heartbeat", "diary", "relationship_moment",
    ),
    min_resonance: float = 0.3,
):
    """E 轴 Russell 情绪联想适配器。

    流程：
      1. 用 user_emotion_detector 把 query → (valence, arousal) 坐标
         不传则走默认双语关键词检测
      2. 从 eligible_categories 类目里拉候选（resolved=false, weight>1.0,
         valence/arousal 非空）
      3. ob_recall.resonance_score 计算每条的共鸣分
      4. 按共鸣分排序，过 min_resonance 阈值，返回 top_k

    传 detector=None 时用 hooks/user_prompt_submit.detect_user_emotion 的关键词版兜底。
    生产用建议接 LLM 出 emotion coord（更稳）。
    """
    from . import ob_recall

    def _default_detector(text: str) -> Optional[tuple[float, float]]:
        # 复用 e_axis_trigger 的关键词字典做粗判
        from .e_axis_trigger import EMOTION_TRIGGER_KEYWORDS  # noqa
        if not text:
            return None
        text_l = text.lower()
        # 简单四象限规则
        if any(w in text for w in ("生气", "烦", "气死", "崩溃")) or "furious" in text_l:
            return (0.15, 0.75)
        if any(w in text for w in ("累", "难过", "失落", "算了", "委屈")) or "exhausted" in text_l:
            return (0.2, 0.25)
        if any(w in text for w in ("开心", "高兴", "兴奋")) or "excited" in text_l:
            return (0.85, 0.65)
        if any(w in text for w in ("温暖", "感动", "想你", "谢谢")) or "miss you" in text_l:
            return (0.75, 0.4)
        return None

    detector = user_emotion_detector or _default_detector

    def call(query: str, top_k: int) -> list[RecallHit]:
        coord = detector(query)
        if coord is None:
            return []
        cat_array = list(eligible_categories)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, title, content, weight, hit_count, arousal, valence, "
                f"       resolved, protected, created_at, last_hit, category, source "
                f"FROM {table_curated} "
                f"WHERE version_status = 'current' "
                f"  AND category = ANY(%s) "
                f"  AND (resolved IS NULL OR resolved = false) "
                f"  AND valence IS NOT NULL AND arousal IS NOT NULL "
                f"  AND (valence != 0 OR arousal != 0.5) "
                f"  AND weight >= 1.0 "
                f"ORDER BY weight DESC LIMIT 200",
                (cat_array,),
            )
            rows = cur.fetchall()

        scored: list[tuple[float, RecallHit]] = []
        for r in rows:
            mem = {
                "id": r[0], "weight": r[3] or 1.0, "hit_count": r[4] or 0,
                "arousal": r[5] or 0.3, "valence": r[6] or 0.5,
                "resolved": r[7], "protected": r[8],
                "created_at": r[9], "last_hit": r[10],
                "category": r[11], "source": r[12],
            }
            res = ob_recall.resonance_score(coord, mem)
            if res < min_resonance:
                continue
            scored.append((res, RecallHit(
                source_id=int(r[0]),
                title=r[1] or "",
                content=(r[2] or "")[:300],
                score=min(1.0, res),
                channel="emotion",
                metadata={"emotion_coord": coord, "resonance": round(res, 3)},
            )))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [h for _, h in scored[:top_k]]
    return call


def query_expand_adapter(
    llm_call: Callable[[str, int], Optional[str]],
    max_queries: int = 4,
):
    """Query Expansion 适配器 · 用 LLM 把用户消息扩展成多角度搜索词。

    llm_call 签名：(prompt: str, max_tokens: int) -> Optional[str]
    推荐接 DeepSeek V4 Pro 等便宜模型（一次调用 <200 tokens）。

    示例：
        from extras.pgvector_backend.recall_pipeline import query_expand_adapter

        def my_llm(prompt, max_tokens):
            return deepseek_client.chat(prompt, max_tokens=max_tokens)

        pipeline = RecallPipeline(
            query_expand=query_expand_adapter(my_llm, max_queries=4),
            vector_search=...,
        )
    """
    def call(query: str) -> list[str]:
        truncated = query[:2000]
        prompt = (
            "你是搜索查询扩展器。根据用户消息生成2-3个不同角度的搜索关键词组合。\n"
            "要求：每行一个查询，包含同义词/相关概念/情绪词。不要编号不要解释。\n\n"
            f"用户消息: {truncated}"
        )
        content = llm_call(prompt, 200)
        if not content:
            return [query]
        queries = [query]
        seen = {query}
        for line in content.split("\n"):
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                queries.append(line)
            if len(queries) >= max_queries:
                break
        return queries
    return call
