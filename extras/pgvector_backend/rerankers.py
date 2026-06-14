"""Rerank adapters · DeepSeek / OpenAI / Anthropic / Voyage rerank API

recall_pipeline 的 rerank 通道是可选——调用方塞一个
`(query, hits, top_k) -> hits` 的 callable 进去即可。

这个文件提供常见后端的现成适配器，让 alpha 部署不用从头写一个 LLM rerank。

哲学保持：provider-free 是默认。这里只是 *adapters*，没有 import-time 副作用。
所有调用都包了异常，rerank 失败时 RecallPipeline 自动回退到按 score 排序。
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .recall_pipeline import RecallHit


# === LLM-based rerank（让模型读完候选给一个序）===

def llm_listwise_reranker(
    api_url: str,
    api_key: str,
    model: str,
    timeout: int = 30,
) -> Callable[[str, list, int], list]:
    """通用 LLM listwise rerank —— 把候选列表一次性喂给模型，
    要求输出按相关性排序的 source_id 列表。

    适合 deepseek-chat / gpt-4o-mini / claude-haiku 这种便宜模型，
    单次几百 token，比逐对打分快得多。
    """
    try:
        import requests
    except ImportError:
        def stub(query: str, hits: list, top_k: int) -> list:
            return hits[:top_k]
        return stub

    def rerank(query: str, hits: list, top_k: int) -> list:
        if not hits:
            return []
        # 喂给模型的候选清单
        items = []
        for i, h in enumerate(hits):
            items.append(f"[{i}] id={h.source_id} score={h.score:.2f} "
                         f"title={(h.title or '')[:50]} :: {h.content[:200]}")
        from .anti_hallucination import (
            ANTI_HALLUCINATION_HEADER,
            RERANK_TASK_REMINDERS,
        )
        prompt = (
            ANTI_HALLUCINATION_HEADER
            + RERANK_TASK_REMINDERS
            + "\n你在为一个 AI agent 给记忆召回结果做重排。\n"
            + f"用户消息: {query[:500]}\n\n"
            + "候选:\n" + "\n".join(items) + "\n\n"
            + f"返回最相关的前 {top_k} 条 index，纯 JSON 整数数组，"
            + f"例如 [3, 0, 7]。不要附加解释，不要修改候选内容，"
            + f"不要因为某条'看起来更深刻'就抬高——只看与用户消息的相关度。"
        )
        try:
            resp = requests.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 200,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r"\[[^\]]*\]", content)
            if not m:
                return hits[:top_k]
            indices = json.loads(m.group(0))
            seen = set()
            out = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(hits) and idx not in seen:
                    seen.add(idx)
                    out.append(hits[idx])
                    if len(out) >= top_k:
                        break
            # 不足 top_k 时补剩下未被选中的（按原分排序）
            if len(out) < top_k:
                rest = sorted(
                    (h for i, h in enumerate(hits) if i not in seen),
                    key=lambda h: h.score, reverse=True,
                )
                out.extend(rest[:top_k - len(out)])
            return out
        except Exception:
            # rerank 失败回退到原排序——RecallPipeline 也会再兜一次
            return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
    return rerank


def deepseek_reranker(
    api_key: Optional[str] = None,
    model: str = "deepseek-chat",
) -> Optional[Callable[[str, list, int], list]]:
    """DeepSeek listwise rerank。"""
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    return llm_listwise_reranker(
        api_url="https://api.deepseek.com/v1/chat/completions",
        api_key=key,
        model=model,
    )


def openai_reranker(
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> Optional[Callable[[str, list, int], list]]:
    """OpenAI listwise rerank。"""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    return llm_listwise_reranker(
        api_url="https://api.openai.com/v1/chat/completions",
        api_key=key,
        model=model,
    )


# === Voyage 专用 rerank API（不是 chat 模型，是专门的 rerank endpoint）====

def voyage_reranker(
    api_key: Optional[str] = None,
    model: str = "rerank-2",
    timeout: int = 30,
) -> Optional[Callable[[str, list, int], list]]:
    """Voyage AI rerank-2 适配器。专门的 reranking API，比让 LLM 排序更稳更快。"""
    key = api_key or os.environ.get("VOYAGE_API_KEY")
    if not key:
        return None
    try:
        import requests
    except ImportError:
        return None

    def rerank(query: str, hits: list, top_k: int) -> list:
        if not hits:
            return []
        docs = [f"{h.title}\n{h.content}"[:2000] for h in hits]
        try:
            resp = requests.post(
                "https://api.voyageai.com/v1/rerank",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"query": query, "documents": docs,
                      "model": model, "top_k": top_k},
                timeout=timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
            return [hits[r["index"]] for r in results
                    if isinstance(r.get("index"), int)
                    and 0 <= r["index"] < len(hits)]
        except Exception:
            return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
    return rerank


# === 自动选择 ===========================================================

def get_reranker() -> Optional[Callable[[str, list, int], list]]:
    """按环境变量顺序找可用 reranker。

    顺序：VOYAGE（专用 rerank API）→ DeepSeek → OpenAI
    全部失败返回 None；RecallPipeline 看到 None 自动按 score 排序。
    """
    for factory in (voyage_reranker, deepseek_reranker, openai_reranker):
        r = factory()
        if r is not None:
            return r
    return None
