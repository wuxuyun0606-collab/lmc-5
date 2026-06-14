"""pgvector 后端 · LMC-5 store.py 向量层替换

对应 lmc-5 core 的 src/lmc5/store.py。
原版那版用 SQLite + vector_json TEXT + Python cosine 全表扫，几千条就拖。
这一版改成 PostgreSQL + pgvector halfvec + ivfflat 索引，生产级 ANN。

设计目标：
- 保留 lmc-5 的对外接口签名（search_vectors / write_vector / delete_vector）
- LLM embedding 调用走 Callable 注入（默认 None；调用方传自己的 embedder）
- L2 归一化 + cosine 距离（halfvec 节省一半内存）
- 维度灵活（默认 3072d gemini-embedding-2，可改）

集成方式：
    from lmc5_addons.vector_pgvector import PgvectorStore
    store = PgvectorStore(dsn="postgresql://...", embedder=my_embed_fn)
    store.write_vector(owner_type="memory", owner_id=42, text="...")
    hits = store.search_vectors(query_text="...", top_k=8)

替换 lmc-5 的 store.search_vectors 时，把这个类的实例注入即可。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

try:
    import psycopg2
    import psycopg2.extras
except ImportError as e:
    raise ImportError(
        "需要 psycopg2-binary。pip install psycopg2-binary "
        "（若坚持 SQLite，请用 lmc-5 原版的 store.py）"
    ) from e


DEFAULT_DIM = 3072
DEFAULT_MODEL = "gemini-embedding-2"


@dataclass
class VectorHit:
    """对齐 lmc-5 的返回结构"""
    owner_type: str
    owner_id: int
    similarity: float
    text_preview: str
    model: str
    dimension: int


def l2_normalize(vec: Sequence[float]) -> list[float]:
    """L2 归一化 — cosine 距离用归一化向量等价于点积，pgvector 优化路径"""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return list(vec)
    return [x / norm for x in vec]


def vec_to_literal(vec: Sequence[float]) -> str:
    """转 pgvector/halfvec 字面量格式：'[1.0,2.0,...]'"""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


class PgvectorStore:
    """pgvector + halfvec 向量层

    Schema 兼容 lmc-5 的 vectors 表概念：
      (owner_type, owner_id) → (text_preview, embedding halfvec, model, dim)
      唯一键 (owner_type, owner_id, model, dimension) 防同 owner 重复写
    """

    def __init__(
        self,
        dsn: str,
        embedder: Optional[Callable[[str], list[float]]] = None,
        table: str = "lmc5_vectors",
        dim: int = DEFAULT_DIM,
        model_name: str = DEFAULT_MODEL,
        normalize: bool = True,
    ):
        """
        Args:
            dsn: PostgreSQL DSN，如 'postgresql://user:pass@localhost:5432/db'
            embedder: 文本→向量的回调。lmc-5 哲学要求 provider-free，
                      不传则 write/search 必须显式给 vec= 参数
            table: 表名
            dim: 向量维度（与 embedder 输出对齐）
            model_name: 模型标识，用于多模型并存
            normalize: 是否在写入/查询前 L2 归一化
        """
        if embedder is not None and not callable(embedder):
            raise TypeError(
                f"PgvectorStore: embedder must be callable or None, "
                f"got {type(embedder).__name__}"
            )
        self.dsn = dsn
        self.embedder = embedder
        self.table = table
        self.dim = dim
        self.model_name = model_name
        self.normalize = normalize
        self._ensure_schema()

    def _conn(self):
        return psycopg2.connect(self.dsn)

    def _ensure_schema(self) -> None:
        """首次调用时建表 + halfvec 扩展 + ivfflat 索引"""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id BIGSERIAL PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id BIGINT NOT NULL,
                    text_preview TEXT,
                    embedding halfvec({self.dim}),
                    model_name TEXT NOT NULL DEFAULT '{self.model_name}',
                    dimension INTEGER NOT NULL DEFAULT {self.dim},
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (owner_type, owner_id, model_name, dimension)
                )
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self.table}_ivfflat
                ON {self.table}
                USING ivfflat (embedding halfvec_cosine_ops)
                WITH (lists = 100)
                """
            )
            conn.commit()

    def _embed(self, text: str, vec: Optional[Sequence[float]]) -> list[float]:
        if vec is not None:
            v = list(vec)
        elif self.embedder is not None:
            v = list(self.embedder(text))
        else:
            raise ValueError(
                "embedder 未配置且未传 vec=。要么实例化时给 embedder，"
                "要么每次调用显式传向量"
            )
        if len(v) != self.dim:
            raise ValueError(f"向量维度 {len(v)} ≠ 配置 {self.dim}")
        if self.normalize:
            v = l2_normalize(v)
        return v

    def write_vector(
        self,
        owner_type: str,
        owner_id: int,
        text: str = "",
        vec: Optional[Sequence[float]] = None,
    ) -> None:
        """写或覆盖一条向量。

        owner_type/owner_id 是 lmc-5 的概念：把向量挂到任意 owner 上，
        通常 owner_type='memory'/'event'/'chunk'，owner_id=对应表的 id
        """
        v = self._embed(text, vec)
        preview = (text or "")[:300]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.table}
                  (owner_type, owner_id, text_preview, embedding, model_name, dimension)
                VALUES (%s, %s, %s, %s::halfvec, %s, %s)
                ON CONFLICT (owner_type, owner_id, model_name, dimension)
                DO UPDATE SET
                  text_preview = EXCLUDED.text_preview,
                  embedding = EXCLUDED.embedding,
                  created_at = NOW()
                """,
                (owner_type, owner_id, preview, vec_to_literal(v),
                 self.model_name, self.dim),
            )
            conn.commit()

    def delete_vector(self, owner_type: str, owner_id: int) -> int:
        """按 owner 删除（同 owner 的所有 model/dim 版本一起删）"""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {self.table} WHERE owner_type=%s AND owner_id=%s",
                (owner_type, owner_id),
            )
            n = cur.rowcount
            conn.commit()
            return n

    def search_vectors(
        self,
        query_text: str = "",
        query_vec: Optional[Sequence[float]] = None,
        owner_type: Optional[str] = None,
        top_k: int = 8,
        min_similarity: float = 0.0,
    ) -> list[VectorHit]:
        """ANN 检索 top-K

        与 lmc-5 原版的不同：
        - 走 pgvector 的 ivfflat 索引，不再 Python 算分
        - similarity = 1 - cosine_distance，已归一化等价于点积
        - 支持 owner_type 过滤（lmc-5 原版也有，这里语义一致）
        """
        v = self._embed(query_text, query_vec)
        vec_lit = vec_to_literal(v)

        sql = (
            f"SELECT owner_type, owner_id, text_preview, model_name, dimension, "
            f"       1 - (embedding <=> %s::halfvec) AS sim "
            f"FROM {self.table} "
            f"WHERE dimension = %s AND model_name = %s "
        )
        params: list = [vec_lit, self.dim, self.model_name]
        if owner_type:
            sql += "AND owner_type = %s "
            params.append(owner_type)
        sql += "ORDER BY embedding <=> %s::halfvec LIMIT %s"
        params.extend([vec_lit, top_k])

        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        hits: list[VectorHit] = []
        for r in rows:
            sim = float(r["sim"])
            if sim < min_similarity:
                continue
            hits.append(VectorHit(
                owner_type=r["owner_type"],
                owner_id=int(r["owner_id"]),
                similarity=sim,
                text_preview=r["text_preview"] or "",
                model=r["model_name"],
                dimension=int(r["dimension"]),
            ))
        return hits

    def find_duplicates(
        self,
        text: str = "",
        vec: Optional[Sequence[float]] = None,
        threshold: float = 0.92,
        limit: int = 10,
    ) -> list[VectorHit]:
        """去重场景：找 similarity > threshold 的近邻

        用于 hippocampus 写入前判重，避免同义记忆反复入库。
        """
        return [h for h in self.search_vectors(
            query_text=text, query_vec=vec, top_k=limit
        ) if h.similarity > threshold]


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("LMC5_PG_DSN", ""))
    ap.add_argument("--init", action="store_true", help="只建表退出")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("set LMC5_PG_DSN env or pass --dsn")
    store = PgvectorStore(dsn=args.dsn)
    if args.init:
        print(f"schema ready: table={store.table} dim={store.dim}")
