"""Hook · UserPromptSubmit · 每轮召回 + 情绪匹配

Claude Code 的 UserPromptSubmit hook 在每条用户消息触发一次。
作用：拿到这条消息 → 跑多通道召回 → 把命中拼成 additionalContext 注入。

这是"管道"层的核心：记忆从仓库流到对话里就靠这一步。

参考实现：接 Claude Code hook 协议（stdin event JSON / stdout = additional context）。
其他 agent runtime 改 main() 里的 IO 即可，recall + injection_text 的逻辑是通用的。

集成（settings.json 节选）:
    {
      "hooks": {
        "UserPromptSubmit": [{
          "type": "command",
          "command": "python -m extras.pgvector_backend.hooks.user_prompt_submit"
        }]
      }
    }
"""
from __future__ import annotations

import json
import os
import sys


# === Trivial 过滤（嗯/好的/继续这种不触发完整召回）===

TRIVIAL_PATTERNS = (
    "嗯", "嗯嗯", "好", "好的", "好吧", "继续", "ok", "OK", "Ok",
    "收到", "明白", "哈哈", "笑", "对", "是的", "?", "？", "."
)


def is_trivial(message: str) -> bool:
    s = (message or "").strip().lower()
    if not s or len(s) <= 2:
        return True
    return s in [p.lower() for p in TRIVIAL_PATTERNS]


# === 提取 prompt 内容（适配不同 hook 协议）===

def extract_prompt(event: dict) -> str:
    """Claude Code 的 hook event 里 prompt 在 'prompt' 或 'user_message' 或 'message' 字段。
    fall through 把整段 stringify 当 prompt——保守兜底。
    """
    for key in ("prompt", "user_message", "message", "content"):
        v = event.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(event, ensure_ascii=False)[:2000]


# === 拼接情绪上下文（Russell 联想）===

def detect_user_emotion(text: str) -> tuple[float, float] | None:
    """轻量情绪检测：返回 (valence, arousal) 坐标或 None。

    生产环境推荐用一个 LLM scorer 替换这层；这里给一个无依赖的关键词版本兜底。
    """
    if not text:
        return None
    text_l = text.lower()
    # 高 arousal 负面
    if any(w in text for w in ("生气", "烦", "气死", "崩溃")):
        return (0.15, 0.75)
    # 低 arousal 负面
    if any(w in text for w in ("累", "难过", "失落", "算了", "不想", "委屈")):
        return (0.2, 0.25)
    # 高 arousal 正面
    if any(w in text for w in ("开心", "高兴", "兴奋", "爱你")):
        return (0.85, 0.65)
    # 低 arousal 正面
    if any(w in text for w in ("温暖", "感动", "想你", "谢谢")):
        return (0.75, 0.4)
    return None


# === Hook 入口 ============================================================


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def recall_fusion_settings_from_env() -> dict:
    """Read optional recall score-fusion knobs from environment.

    LMC5_RECALL_FUSION accepts raw/minmax/rrf. The default is rrf after
    real-trace A/B showed better top5 composition than minmax. RecallPipeline
    validates the value so deployment mistakes fail visibly during hook build
    rather than silently falling back to a surprising ranking mode.
    """
    return {
        "fusion": os.environ.get("LMC5_RECALL_FUSION", "rrf"),
        "rrf_k": _env_int("LMC5_RECALL_RRF_K", 60),
        # flat keeps legacy consumers stable. Set layered only when the caller
        # knows how to display/use the separated authority/navigation/graph
        # sections.
        "output_mode": os.environ.get("LMC5_RECALL_OUTPUT", "flat"),
    }

def build_pipeline_from_env():
    """从环境变量构造 RecallPipeline。

    自动按存在的环境变量装配通道（PG 优先）：
      - GEMINI/VOYAGE/OPENAI key → 主召回接 PgvectorStore + embedder
      - 永远开启 PG curated FTS / raw-events FTS 兜底
      - legacy SQLite / 冷仓不在主排名里；要接也应作为带标签的最后兜底
      - 可选 LMC5_COLD_ARCHIVE_FALLBACK=1 接冷仓；只有暖层全空才开箱
      - DEEPSEEK/OPENAI key 或 VOYAGE rerank → rerank 通道
      - 永远读 perception cache 作为自发浮现通道
      - graph_expand 默认 None（需要部署方按自家 schema 写 SQL，参考 docs/HOOKS_AND_RECALL.md）
      - emotion_resonate 默认 None（需要部署方提供候选池 SQL）
    """
    import logging
    log = logging.getLogger("lmc5.hooks.user_prompt_submit")

    from .. import recall_pipeline as rp_module
    from .. import perception as perception_module
    from .. import vector_pgvector
    from .. import embedders as emb_module
    from .. import rerankers as rerank_module
    import psycopg2

    dsn = os.environ["LMC5_PG_DSN"]
    pg = psycopg2.connect(dsn)

    # 向量通道——有 embedder 才接，没有就明确 log
    embedder = emb_module.get_embedder()
    vector_search = None
    store = None
    if embedder is not None:
        try:
            store = vector_pgvector.PgvectorStore(dsn=dsn, embedder=embedder)
            vector_search = rp_module.vector_search_adapter(store, embedder)
            log.info("vector channel: enabled (embedder=%s)", type(embedder).__name__)
        except Exception as e:
            log.warning("vector channel: PgvectorStore init failed, disabling: %s", e)
    else:
        log.warning(
            "vector channel: DISABLED — no GEMINI/VOYAGE/OPENAI key found "
            "and no local sentence-transformers fallback. Set one in .env "
            "(see extras/pgvector_backend/.env.example)"
        )

    # rerank——有 key 就接
    rerank = rerank_module.get_reranker()
    if rerank is not None:
        log.info("rerank channel: enabled")
    else:
        log.info("rerank channel: disabled (no DEEPSEEK/OPENAI/VOYAGE key); "
                 "falling back to score sort")

    # 自发浮现——读 cache
    cache_path = __import__("pathlib").Path(
        os.environ.get("LMC5_PERCEPTION_CACHE", "/tmp/lmc5_perception.json")
    )

    def spontaneous(k: int) -> list:
        return [
            rp_module.RecallHit(
                source_id=int(p["source_id"]),
                title=p.get("title", ""),
                content=p.get("content", ""),
                score=0.5,
                channel="perception",
                metadata={"selected_via": p.get("selected_via", "")},
            )
            for p in perception_module.load_perception_cache(cache_path)[:k]
        ]

    enable_literal = os.environ.get("LMC5_LITERAL_RAW_EVENTS", "1").strip().lower()
    literal_search = None
    if enable_literal not in {"0", "false", "off", "no"}:
        try:
            literal_days = int(os.environ.get("LMC5_LITERAL_RAW_EVENTS_DAYS", "30"))
        except ValueError:
            literal_days = 30
        literal_search = rp_module.literal_raw_events_search_adapter(
            pg, recent_days=literal_days
        )

    enable_raw_chunk = os.environ.get("LMC5_RAW_CHUNK_BRIDGE", "0").strip().lower()
    recent_raw_chunk_search = None
    if enable_raw_chunk in {"1", "true", "on", "yes"} and store is not None and embedder is not None:
        recent_raw_chunk_search = rp_module.raw_chunk_vector_search_adapter(
            store, embedder
        )

    enable_cold_archive = os.environ.get("LMC5_COLD_ARCHIVE_FALLBACK", "0").strip().lower()
    cold_archive_search = None
    if enable_cold_archive in {"1", "true", "on", "yes"}:
        cold_archive_search = rp_module.cold_archive_search_adapter(pg)

    fusion_settings = recall_fusion_settings_from_env()

    return rp_module.RecallPipeline(
        vector_search=vector_search,
        fts_search=rp_module.fts_search_adapter(pg),
        raw_events_search=rp_module.raw_events_search_adapter(pg),
        literal_search=literal_search,
        recent_raw_chunk_search=recent_raw_chunk_search,
        cold_archive_search=cold_archive_search,
        graph_expand=rp_module.graph_expand_adapter(pg),
        emotion_resonate=rp_module.emotion_resonate_adapter(pg),
        spontaneous=spontaneous,
        rerank=rerank,
        **fusion_settings,
    )


def main() -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("lmc5.hooks.user_prompt_submit")

    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        event = {}

    prompt = extract_prompt(event)
    if is_trivial(prompt):
        # 短回复跳过完整召回——直接退出，不注入
        return 0

    try:
        pipeline = build_pipeline_from_env()
    except Exception as e:
        log.error("pipeline build failed: %s", e)
        return 0

    try:
        result = pipeline.recall(prompt)
    except Exception as e:
        log.error("recall failed: %s", e)
        return 0

    # 情绪坐标可以附在 metadata 给 prompt layer 用
    emo = detect_user_emotion(prompt)
    if emo:
        sys.stdout.write(
            f"[User emotion coord] valence={emo[0]:.2f} arousal={emo[1]:.2f}\n\n"
        )
    sys.stdout.write(result.injection_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
