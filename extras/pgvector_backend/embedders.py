"""Embedder adapters · Gemini / Voyage / OpenAI / local fallback

PR 之前 user_prompt_submit.py 里 vector 通道写 `vector_search=None`——
真要 hook 跑起来向量那路是空的。这个文件把"几家主流 embedding API"包成
可直接注入的 `text -> list[float]` callable。

哲学保持：provider-free 是默认。这里只是 *adapters*，提供常见接法的样板。
不传任何 API key → 返回 None；调用方判断后再走 stub 或 fallback。

用法：
    from extras.pgvector_backend.embedders import get_embedder

    embedder = get_embedder()   # 按环境变量自动选第一个能用的
    vec = embedder("hello")     # → list[float] or None

或显式：
    embedder = gemini_embedder(model="gemini-embedding-2", dim=3072)
"""
from __future__ import annotations

import os
from typing import Callable, Optional

# 推荐 timeout —— 单条 embedding 1-2s 就该回，超过这个量级要重试不是傻等
DEFAULT_TIMEOUT_S = 15


# === Gemini ===============================================================

def gemini_embedder(
    api_key: Optional[str] = None,
    model: str = "gemini-embedding-2",
    dim: int = 3072,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> Optional[Callable[[str], list[float]]]:
    """Google Gemini embedding API 适配器。

    返回 (text) -> list[float] 的 callable，或 None（key 缺失/不可用）。
    Matryoshka：可以截断 dim 到 1536/768/256 以省存储。
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        import requests
    except ImportError:
        return None

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:embedContent?key={key}"
    )

    def call(text: str) -> Optional[list[float]]:
        try:
            resp = requests.post(
                endpoint,
                json={
                    "content": {"parts": [{"text": text[:8000]}]},
                    "outputDimensionality": dim,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["embedding"]["values"]
        except Exception:
            return None
    return call


# === Voyage AI ============================================================

def voyage_embedder(
    api_key: Optional[str] = None,
    model: str = "voyage-3-large",
    input_type: str = "document",      # "document" / "query"
    timeout: int = DEFAULT_TIMEOUT_S,
) -> Optional[Callable[[str], list[float]]]:
    """Voyage AI embedding 适配器。1024d，文/英/代码检索都强。"""
    key = api_key or os.environ.get("VOYAGE_API_KEY")
    if not key:
        return None
    try:
        import requests
    except ImportError:
        return None

    endpoint = "https://api.voyageai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(text: str) -> Optional[list[float]]:
        try:
            resp = requests.post(
                endpoint,
                headers=headers,
                json={
                    "input": [text[:8000]],
                    "model": model,
                    "input_type": input_type,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except Exception:
            return None
    return call


# === OpenAI ==============================================================

def openai_embedder(
    api_key: Optional[str] = None,
    model: str = "text-embedding-3-large",
    dim: int = 3072,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> Optional[Callable[[str], list[float]]]:
    """OpenAI text-embedding-3 适配器。3072d / 可截断到 1024 / 256。"""
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:
        import requests
    except ImportError:
        return None

    endpoint = "https://api.openai.com/v1/embeddings"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def call(text: str) -> Optional[list[float]]:
        try:
            payload = {"input": text[:8000], "model": model}
            if dim and dim != 3072:
                payload["dimensions"] = dim
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except Exception:
            return None
    return call


# === 本地 BGE-M3 / sentence-transformers ================================

def local_st_embedder(
    model_name: str = "BAAI/bge-m3",
    device: str = "cpu",
) -> Optional[Callable[[str], list[float]]]:
    """本地 sentence-transformers 适配器。完全 offline。

    需要：pip install sentence-transformers
    首次调用会下载模型。模型驻留进程内存。
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None

    try:
        model = SentenceTransformer(model_name, device=device)
    except Exception:
        return None

    def call(text: str) -> Optional[list[float]]:
        try:
            vec = model.encode([text[:8000]], normalize_embeddings=True)
            return vec[0].tolist()
        except Exception:
            return None
    return call


# === 自动选择 ===========================================================

def get_embedder() -> Optional[Callable[[str], list[float]]]:
    """按环境变量顺序找一个可用 embedder。

    顺序：GEMINI_API_KEY → VOYAGE_API_KEY → OPENAI_API_KEY → 本地 BGE-M3 fallback
    全部失败返回 None；调用方应当 log 一行警告，向量通道走 stub。
    """
    for factory in (gemini_embedder, voyage_embedder, openai_embedder, local_st_embedder):
        emb = factory()
        if emb is not None:
            return emb
    return None
