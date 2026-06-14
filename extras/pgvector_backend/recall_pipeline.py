"""多通道召回 pipeline · 把记忆从仓库流到对话里

PR 之前的状态：只有 vector_pgvector.search_vectors 一条通道。
这个文件把召回升级成"多通道并行 + 合并 + 可选 rerank"：

  1. 语义召回（pgvector halfvec ANN）
  2. FTS 兜底（向量分都低时挂 FTS，避免空回）
  3. 关系图 2 跳扩展（种子 → graph_activate）
  4. 情绪联想（Russell 距离找近邻碎片）
  5. 自发浮现（perception.py 注入 1-2 条不被问也想起的）
  6. 可选 rerank（DeepSeek/任意 LLM 做最终排序）

哲学：召回是"管道"不是"仓库"。每条通道都可注入、可关、可换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class RecallHit:
    """统一的命中结构。各通道结果归一到这个类。"""
    source_id: int
    title: str
    content: str
    score: float                # 通道内得分（0-1 归一化）
    channel: str                # 哪条通道送来的：vector / fts / graph / emotion / perception
    metadata: dict = field(default_factory=dict)


@dataclass
class RecallResult:
    """召回总结。injection_text 是直接可贴进 system prompt 的字符串。"""
    hits: list[RecallHit]
    channels_used: list[str]
    injection_text: str
    channel_counts: dict[str, int] = field(default_factory=dict)


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
        rerank: Optional[Callable[[str, list[RecallHit], int], list[RecallHit]]] = None,
        vector_top_k: int = 8,
        fts_top_k: int = 5,
        raw_events_top_k: int = 5,
        graph_hops: int = 2,
        graph_max_expand: int = 10,
        emotion_top_k: int = 2,
        perception_top_k: int = 1,
        fts_floor: float = 0.45,
        raw_events_floor: float = 0.30,
        final_top_k: int = 10,
        injection_budget_chars: int = 4000,
    ):
        """
        三层检索 + 兜底机制：

            Stage 1: vector （语义主路 · pgvector ANN）
            Stage 2: fts    （兜底 1 · 当 vector top_score < fts_floor 时启动；
                              查 curated_memories 的 tsvector）
            Stage 3: raw_events （兜底 2 · 当 vector top_score < raw_events_floor 时
                                   启动；查 lmc5_raw_events 原始对话日志的 tsvector。
                                   原始事件比 curated 多一个量级，专门救 vector + curated
                                   都不行的"陌生关键词"场景，例如刚配的新代号、新人名）

        其他通道（graph / emotion / spontaneous）独立并行，不在 fallback 链上。

        Args:
            vector_search: (query, top_k) → vector hits
            fts_search: (query, top_k) → FTS hits over curated memories
            raw_events_search: (query, top_k) → FTS hits over raw events journal
            graph_expand: (seed_ids, hops) → 关系图扩展 hits
            emotion_resonate: (query, top_k) → Russell 情绪联想 hits
            spontaneous: (top_k) → 自发浮现 hits（不查询，按概率冒出）
            rerank: (query, hits, top_k) → 重排（可接 DeepSeek/任意 LLM）
            fts_floor: 向量召回最高分低于此值时走 FTS 兜底
            raw_events_floor: 向量召回最高分低于此值时再启动 raw events 兜底
                              （比 fts_floor 更严——只在真正捞不到时才查原始事件）
            injection_budget_chars: 最终拼到 system prompt 的字符上限
        """
        for name, fn in (
            ("vector_search", vector_search),
            ("fts_search", fts_search),
            ("raw_events_search", raw_events_search),
            ("graph_expand", graph_expand),
            ("emotion_resonate", emotion_resonate),
            ("spontaneous", spontaneous),
            ("rerank", rerank),
        ):
            if fn is not None and not callable(fn):
                raise TypeError(
                    f"RecallPipeline: {name} must be callable or None, "
                    f"got {type(fn).__name__}"
                )
        self.vector_search = vector_search
        self.fts_search = fts_search
        self.raw_events_search = raw_events_search
        self.graph_expand = graph_expand
        self.emotion_resonate = emotion_resonate
        self.spontaneous = spontaneous
        self.rerank = rerank

        self.vector_top_k = vector_top_k
        self.fts_top_k = fts_top_k
        self.raw_events_top_k = raw_events_top_k
        self.graph_hops = graph_hops
        self.graph_max_expand = graph_max_expand
        self.emotion_top_k = emotion_top_k
        self.perception_top_k = perception_top_k
        self.fts_floor = fts_floor
        self.raw_events_floor = raw_events_floor
        self.final_top_k = final_top_k
        self.injection_budget_chars = injection_budget_chars

    def _safe_call(self, name: str, fn: Callable, *args) -> list[RecallHit]:
        """每条通道都包异常——一条断了不影响其他通道"""
        import logging
        log = logging.getLogger("lmc5.recall_pipeline")
        try:
            result = fn(*args) or []
            return [h for h in result if isinstance(h, RecallHit)]
        except Exception as e:
            log.warning("recall channel '%s' failed: %s", name, e)
            return []

    def _merge_dedup(self, channels: list[tuple[str, list[RecallHit]]]) -> list[RecallHit]:
        """同一 source_id 在多通道命中时合并：保留最高分 + 标注命中的所有通道"""
        merged: dict[int, RecallHit] = {}
        for channel_name, hits in channels:
            for h in hits:
                if h.source_id in merged:
                    existing = merged[h.source_id]
                    if h.score > existing.score:
                        existing.score = h.score
                    existing.metadata.setdefault("channels", set()).add(h.channel)
                    existing.metadata["channels"].add(channel_name)
                else:
                    h.metadata.setdefault("channels", set()).add(h.channel)
                    merged[h.source_id] = h
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
            line = f"- [{tag_str} score={h.score:.2f}] {h.title}: {h.content[:200]}"
            if used + len(line) + 1 > self.injection_budget_chars:
                lines.append("... (truncated by injection_budget_chars)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

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

        # 1. 向量召回（主路）
        vector_hits: list[RecallHit] = []
        if self.vector_search is not None:
            vector_hits = self._safe_call("vector", self.vector_search,
                                          query, self.vector_top_k)
            if vector_hits:
                channel_results.append(("vector", vector_hits))
                channels_used.append("vector")

        # 2. FTS 兜底 1 · curated_memories（向量分都低时才走，避免空回）
        top_vec_score = max((h.score for h in vector_hits), default=0.0)
        if self.fts_search is not None and top_vec_score < self.fts_floor:
            fts_hits = self._safe_call("fts", self.fts_search,
                                       query, self.fts_top_k)
            if fts_hits:
                channel_results.append(("fts", fts_hits))
                channels_used.append("fts")
                log.info("recall: vector top_score=%.2f < %.2f, FTS fallback added %d hits",
                         top_vec_score, self.fts_floor, len(fts_hits))

        # 2b. FTS 兜底 2 · raw events journal（更严苛的阈值——只有真正捞不到时才查
        #     原始对话日志，因为这层数据量大、噪声多）
        if self.raw_events_search is not None and top_vec_score < self.raw_events_floor:
            raw_hits = self._safe_call("raw_events", self.raw_events_search,
                                       query, self.raw_events_top_k)
            if raw_hits:
                channel_results.append(("raw_events", raw_hits))
                channels_used.append("raw_events")
                log.info("recall: vector top_score=%.2f < %.2f, raw_events fallback added %d hits",
                         top_vec_score, self.raw_events_floor, len(raw_hits))

        # 3. 关系图 2 跳扩展（用 vector hits 当种子）
        if self.graph_expand is not None:
            if seed_ids is None:
                seed_ids = [h.source_id for h in vector_hits[:3]]
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

        # 合并去重
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

        injection_text = self._build_injection_text(merged)
        return RecallResult(
            hits=merged,
            channels_used=channels_used,
            injection_text=injection_text,
            channel_counts=channel_counts,
        )


# === 通道适配器 helper（把现有模块包装成 callable）===

def vector_search_adapter(store, query_embedder: Callable[[str], list[float]]):
    """把 PgvectorStore 包成 vector_search callable。"""
    def call(query: str, top_k: int) -> list[RecallHit]:
        vec = query_embedder(query)
        hits = store.search_vectors(query_vec=vec, owner_type="curated", top_k=top_k)
        return [
            RecallHit(
                source_id=h.owner_id,
                title="",
                content=h.text_preview,
                score=h.similarity,
                channel="vector",
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
                metadata={"session_id": r[5], "role": r[1]},
            )
            for r in rows
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
