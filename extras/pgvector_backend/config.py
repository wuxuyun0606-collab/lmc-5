"""集中所有可调参数 · 一个文件改完全套生效

各模块原本写在默认参数里的"魔法数字"——0.92 / batch=8 / top_k=5 / importance>=7
之类的——全部集中到这里。理由：长期跑的部署里，调参不是改源码，是改配置。

用法：
    from extras.pgvector_backend.config import LMC5Config

    cfg = LMC5Config()                 # 用默认值
    cfg.dedup_similarity = 0.95        # 单参数覆盖
    cfg = LMC5Config(dream_batch_size=4, dream_max_retries=5)  # 构造时覆盖

    # 把 cfg 注入到下游模块
    store = PgvectorStore(dsn=..., config=cfg)
    dream = NightDream(..., config=cfg)
    scorer = EAxisScorer(..., config=cfg)

也可以从环境变量加载：
    cfg = LMC5Config.from_env()        # 读 LMC5_DEDUP_SIM / LMC5_DREAM_BATCH ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields


@dataclass
class LMC5Config:
    """所有可调参数。范围和默认值的解释见每个字段的注释。"""

    # === 向量层 ===
    embed_dim: int = 3072                  # gemini-embedding-2 默认 3072d；voyage 系列 1024d
    embed_model_name: str = "gemini-embedding-2"
    embed_normalize_l2: bool = True        # 写入前 L2 归一化（cosine ≡ dot product）
    ivfflat_lists: int = 100               # pgvector ivfflat 索引参数；corpus<10k 可降到 50

    # === 检索 ===
    search_top_k: int = 8                  # 默认召回条数
    search_min_similarity: float = 0.0     # 召回过滤下限；0 = 不过滤

    # === 去重 / 浓缩 ===
    dedup_similarity: float = 0.92         # 相似度 > 此阈值才判重复；放宽看 0.88，严格看 0.95
    dedup_limit: int = 10                  # 单次找重复返回的最大数

    # === 做梦 / Hippocampus ===
    dream_batch_size: int = 8              # 单次喂 proposer 的 chunk 数；模型上下文小就降
    dream_importance_threshold: int = 7    # 闸门：importance >= 才晋升；候选少就降到 5
    dream_max_promote: int = 10            # 单次晋升上限；防止一次性写爆库
    dream_relation_top_k: int = 5          # 新记忆扩 top-K 邻居建关系
    dream_noise_min_chars: int = 80        # 小于这个字数判噪音直接 drop

    # === LLM 调用容错（housekeeper / proposer / scorer 共用）===
    llm_timeout_s: int = 60                # 单次调用超时
    llm_max_retries: int = 3               # 失败重试上限（含首次）
    llm_retry_backoff_s: float = 2.0       # 指数退避基数：第 N 次失败 → 等 backoff^N 秒
    llm_retry_on: tuple = field(default_factory=lambda: (
        "http_timeout", "http_error", "empty_response", "no_json_in_text",
    ))                                      # 这些失败类型走重试；schema_fail / range_fail 不重试

    # === E 轴 ===
    e_axis_shadow_days: int = 30           # E 轴评分上线后不参与排序的影子期
    e_axis_sleep_between_calls_s: float = 0.3  # 防限流

    # === 时间衰减 / OB 评分 ===
    decay_floor: float = 0.3               # 衰减地板；不衰减到 0
    decay_half_life_default: int = 45      # 未在 category 表里命中时的默认半衰期（天）
    time_ripple_hours: int = 48            # 命中后 ±N 小时的邻居获得 boost
    time_ripple_boost: float = 0.3         # 每次涟漪给的 boost 增量
    time_ripple_cap: float = 3.0           # 单条记忆 boost 封顶

    # === 叙事时间线 ===
    timeline_weekly_top_n: int = 8         # 每周挑几条种子做 narrative
    timeline_monthly_top_n: int = 20       # 每月挑几条
    timeline_weight_floor: float = 1.5     # 种子筛选下限

    # === Z 轴 / contradiction 审计 ===
    z_judge_sleep_s: float = 0.5           # 每次 contradiction 判断后睡多久（限流）
    z_content_max_chars: int = 500         # 喂给判官的单条 content 截断

    @classmethod
    def from_env(cls, prefix: str = "LMC5_") -> "LMC5Config":
        """从环境变量加载。变量名 = prefix + 字段名大写。
        类型按 dataclass 字段自动转换；解析失败的字段保留默认值。
        """
        kwargs: dict = {}
        for f in fields(cls):
            env_key = f"{prefix}{f.name.upper()}"
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            try:
                if f.type is int or f.type == "int":
                    kwargs[f.name] = int(raw)
                elif f.type is float or f.type == "float":
                    kwargs[f.name] = float(raw)
                elif f.type is bool or f.type == "bool":
                    kwargs[f.name] = raw.strip().lower() in ("1", "true", "yes", "on")
                else:
                    kwargs[f.name] = raw
            except (TypeError, ValueError):
                continue
        return cls(**kwargs)


# === 共享 retry 装饰器（给 LLM call 用）===

def retry_llm_call(config: LMC5Config):
    """简单的指数退避重试装饰器。

    用在任意 (prompt, ...) -> str 形态的 LLM 调用上。
    fail_category 由调用方在 raise 时通过自定义 Exception 传递（见下）。
    """
    import functools
    import time

    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            last_exc = None
            for attempt in range(max(1, config.llm_max_retries)):
                try:
                    return fn(*args, **kwargs)
                except RetryableLLMError as e:
                    last_exc = e
                    if attempt + 1 >= config.llm_max_retries:
                        break
                    wait = config.llm_retry_backoff_s ** (attempt + 1)
                    time.sleep(wait)
                except Exception:
                    # 非可重试错误（schema_fail / range_fail / 其他）直接外抛
                    raise
            # 所有重试用完，外抛最后一次错误
            if last_exc:
                raise last_exc
        return wrapped
    return deco


class RetryableLLMError(Exception):
    """标记一次 LLM 调用失败但应该重试。

    调用方按需 raise，例如 http_timeout / empty_response / json parse 失败。
    schema_fail / range_fail 等"模型给出了结构错误的回答"不应该重试——
    重试只会浪费 token。
    """
    def __init__(self, category: str, detail: str = ""):
        self.category = category
        self.detail = detail
        super().__init__(f"{category}: {detail}")
