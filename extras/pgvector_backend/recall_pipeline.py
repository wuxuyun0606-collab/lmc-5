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
        rerank: Optional[Callable[[str, list[RecallHit], int], list[RecallHit]]] = None,
        vector_top_k: int = 8,
        fts_top_k: int = 5,
        graph_hops: int = 2,
        graph_max_expand: int = 10,
        emotion_top_k: int = 2,
        perception_top_k: int = 1,
        fts_floor: float = 0.45,
        final_top_k: int = 10,
        injection_budget_chars: int = 4000,
    ):
        """
        Args:
            vector_search: (query, top_k) → vector hits
            fts_search: (query, top_k) → FTS hits（向量分都低于 fts_floor 时才会调）
            graph_expand: (seed_ids, hops) → 关系图扩展 hits
            emotion_resonate: (query, top_k) → Russell 情绪联想 hits
            spontaneous: (top_k) → 自发浮现 hits（不查询，按概率冒出）
            rerank: (query, hits, top_k) → 重排（可接 DeepSeek/任意 LLM）
            fts_floor: 向量召回最高分低于此值时才走 FTS 兜底
            injection_budget_chars: 最终拼到 system prompt 的字符上限
        """
        self.vector_search = vector_search
        self.fts_search = fts_search
        self.graph_expand = graph_expand
        self.emotion_resonate = emotion_resonate
        self.spontaneous = spontaneous
        self.rerank = rerank

        self.vector_top_k = vector_top_k
        self.fts_top_k = fts_top_k
        self.graph_hops = graph_hops
        self.graph_max_expand = graph_max_expand
        self.emotion_top_k = emotion_top_k
        self.perception_top_k = perception_top_k
        self.fts_floor = fts_floor
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

        # 2. FTS 兜底（向量分都低时才走，避免空回）
        if self.fts_search is not None:
            top_vec_score = max((h.score for h in vector_hits), default=0.0)
            if top_vec_score < self.fts_floor:
                fts_hits = self._safe_call("fts", self.fts_search,
                                           query, self.fts_top_k)
                if fts_hits:
                    channel_results.append(("fts", fts_hits))
                    channels_used.append("fts")
                    log.info("recall: vector top_score=%.2f < %.2f, FTS fallback added %d hits",
                             top_vec_score, self.fts_floor, len(fts_hits))

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
    """PostgreSQL tsvector 全文检索适配器。需要表上有 content_tsv 列 + GIN 索引。"""
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
