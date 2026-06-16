"""Agent 主动检索工具 · 让 agent 能自己伸手翻记忆

hook 自动注入解决的是"每轮被动推一些上下文"；这个模块解决的是
"agent 在思考过程中发现需要查某个人名/某件旧事/某个约定，主动发起精准检索"。

两件事缺一不可：
  - active_recall.py 的 directive 驱动 agent "想搜"
  - 本模块的 tool 让 agent "能搜"

设计原则：
  - Provider-free：store / embedder / reranker 全走 Callable 注入，不绑死任何后端
  - 两个粒度：keyword（精准关键词 FTS）和 recall（深度多路检索）
  - 输出格式对齐 RecallHit，上层 hook 能直接消费
  - 可注册为 MCP tool、function-calling tool、或 CLI subcommand

用法：
    from lmc5_addons.agent_search_tool import build_tools, register_on_engine

    # 方式 1：拿 tool schema + handler dict（适配任意 tool-calling 框架）
    tools = build_tools(store=my_store)

    # 方式 2：直接挂到 RecallEngine 的 spontaneous 通道
    register_on_engine(engine, store=my_store)

注：本模块从一个 production MCP memory server 抽象而来，已剥离全部人格/身份
内容，可用于任意 agent。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Tool schemas — agent 看到的接口描述
# ---------------------------------------------------------------------------

SEARCH_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memory_search",
    "description": (
        "Search long-term memory by keyword. Use when you encounter names, "
        "past events, time references, or anything you're unsure about. "
        "Returns matching memories with full content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Search keyword (name, term, date, etc.)",
            },
            "category": {
                "type": "string",
                "description": "Optional: limit to a category (e.g. 'note', 'event', 'fact')",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5)",
                "default": 5,
            },
        },
        "required": ["keyword"],
    },
}

RECALL_TOOL_SCHEMA: dict[str, Any] = {
    "name": "memory_recall",
    "description": (
        "Deep multi-channel recall: vector semantic search + FTS keyword match "
        "+ relation graph expansion, merged and re-ranked. Use for nuanced "
        "queries where a simple keyword search might miss context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query describing what you want to recall",
            },
            "limit": {
                "type": "integer",
                "description": "Max results after re-ranking (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# Tool handlers — 接到调用后实际做的事
# ---------------------------------------------------------------------------

def _handle_search(
    store_search: Callable[..., list[dict]],
    params: dict[str, Any],
) -> str:
    keyword = params.get("keyword", "")
    if not keyword.strip():
        return json.dumps({"error": "keyword is required"}, ensure_ascii=False)
    limit = params.get("limit", 5)
    category = params.get("category")
    kwargs: dict[str, Any] = {"query": keyword, "limit": limit}
    if category:
        kwargs["channel"] = category
    results = store_search(**kwargs)
    return json.dumps(results, ensure_ascii=False, default=str)


def _handle_recall(
    store_recall: Callable[..., list[dict]],
    params: dict[str, Any],
) -> str:
    query = params.get("query", "")
    if not query.strip():
        return json.dumps({"error": "query is required"}, ensure_ascii=False)
    limit = params.get("limit", 5)
    results = store_recall(query=query, limit=limit, redact=True)
    return json.dumps(results, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_tools(
    store: Any = None,
    *,
    search_fn: Optional[Callable[..., list[dict]]] = None,
    recall_fn: Optional[Callable[..., list[dict]]] = None,
) -> list[dict[str, Any]]:
    """Build tool definitions with bound handlers.

    Pass either a MemoryStore instance (uses store.search_events / store.recall)
    or explicit callables.

    Returns a list of dicts, each with 'schema' and 'handler' keys:
        [
            {"schema": SEARCH_TOOL_SCHEMA, "handler": callable},
            {"schema": RECALL_TOOL_SCHEMA, "handler": callable},
        ]
    """
    if search_fn is None:
        if store is None:
            raise ValueError("provide either store or search_fn")
        search_fn = store.search_events
    if recall_fn is None:
        if store is None:
            raise ValueError("provide either store or recall_fn")
        recall_fn = store.recall

    _sfn = search_fn
    _rfn = recall_fn

    return [
        {
            "schema": SEARCH_TOOL_SCHEMA,
            "handler": lambda params: _handle_search(_sfn, params),
        },
        {
            "schema": RECALL_TOOL_SCHEMA,
            "handler": lambda params: _handle_recall(_rfn, params),
        },
    ]


def register_on_engine(
    engine: Any,
    store: Any = None,
    *,
    search_fn: Optional[Callable[..., list[dict]]] = None,
    recall_fn: Optional[Callable[..., list[dict]]] = None,
) -> None:
    """Convenience: attach search tools to a RecallEngine instance.

    This patches engine.tool_schemas and engine.tool_handlers so the
    engine's host can expose them to the agent alongside the existing
    recall pipeline.
    """
    tools = build_tools(store=store, search_fn=search_fn, recall_fn=recall_fn)
    if not hasattr(engine, "tool_schemas"):
        engine.tool_schemas = []
    if not hasattr(engine, "tool_handlers"):
        engine.tool_handlers = {}
    for t in tools:
        engine.tool_schemas.append(t["schema"])
        engine.tool_handlers[t["schema"]["name"]] = t["handler"]
