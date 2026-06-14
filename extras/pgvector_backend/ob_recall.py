"""OB 评分 · LMC-5 排序层增强

对应 lmc-5 core：原版排序只有 importance + risk_level 两档，
没有时间衰减、没有情绪距离、没有时间涟漪、没有分类半衰期。

这一版补：
  1. OB 统一评分（Ombre Brain decay_engine 思路）
     time_weight × importance × activation^0.3 × decay × emotion_weight × resolved_factor
  2. 分类半衰期表（heartbeat/identity 永不衰减；conversation 14 天）
  3. 短期/长期分离（≤3 天时间主导，>3 天情感主导）
  4. Russell 圆环距离：情绪联想取最近邻
  5. 时间涟漪：检索命中后 ±48h 邻居获得临时 boost
  6. 衰减统一公式（写入端 + M 线代谢共用单一来源）

设计：
  - 纯算法层，无 IO。所有数据从 dict 拿，结果返回浮点
  - 时间涟漪/统计走 Callable 注入，调用方决定怎么持久化
  - 完整保留 lmc-5 的 provider-free 哲学
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable, Optional


# === 衰减参数 ===
OB_EMOTION_BASE = 1.0
OB_AROUSAL_BOOST = 0.8

# 分类半衰期（天）。float('inf') = 永不衰减
CATEGORY_HALF_LIVES = {
    "heartbeat": float("inf"),
    "identity": float("inf"),
    "core": 90,
    "fragments": 90,
    "important": 90,
    "reviews": 60,
    "diary": 60,
    "mailbox": 60,
    "knowledge": 30,
    "notebook": 30,
    "conversation": 14,
}
DEFAULT_HALF_LIFE = 45


def _get_half_life(memory: dict) -> float:
    cat = str(memory.get("category") or "").strip().lower()
    src = str(memory.get("source") or "").strip().lower()
    if cat in CATEGORY_HALF_LIVES:
        return CATEGORY_HALF_LIVES[cat]
    if src in CATEGORY_HALF_LIVES:
        return CATEGORY_HALF_LIVES[src]
    return DEFAULT_HALF_LIFE


def _parse_time(ts) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None) if ts.tzinfo else ts
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _ob_time_weight(days_since: float) -> float:
    """分段时间权重：0-1天=1.0；第2天线性降到 0.9；之后指数到地板 0.3"""
    if days_since <= 1.0:
        return 1.0
    if days_since <= 2.0:
        return 1.0 - 0.1 * (days_since - 1.0)
    raw = 0.9 * math.exp(-0.2197 * (days_since - 2.0))
    return max(0.3, raw)


def ob_score(memory: dict) -> float:
    """统一评分。

    memory 字段（都可选，缺省走默认）：
        weight, hit_count, created_at, last_hit, arousal,
        protected, resolved, digested, activation_boost, category, source

    返回浮点分数（protected 记忆固定 999.0）。
    """
    if not isinstance(memory, dict):
        return 0.0
    if memory.get("protected"):
        return 999.0

    try:
        weight = float(memory.get("weight") or 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    importance = max(1.0, min(10.0, weight * 3.3))

    try:
        activation = min(30, max(1, int(memory.get("hit_count") or 1)))
    except (TypeError, ValueError):
        activation = 1

    ref_time = None
    for field in ("last_hit", "created_at"):
        parsed = _parse_time(memory.get(field))
        if parsed and (ref_time is None or parsed > ref_time):
            ref_time = parsed
    days_since = 30.0 if ref_time is None else max(0.0, (datetime.now() - ref_time).total_seconds() / 86400)

    try:
        arousal = max(0.0, min(1.0, float(memory.get("arousal") or 0.3)))
    except (TypeError, ValueError):
        arousal = 0.3

    half_life = _get_half_life(memory)
    decay = 1.0 if half_life == float("inf") else math.exp(-math.log(2) * days_since / half_life)

    try:
        act_boost = float(memory.get("activation_boost") or 0)
    except (TypeError, ValueError):
        act_boost = 0.0
    boost_factor = 1.0 + min(act_boost, 3.0) * 0.15

    # 短期时间主导，长期情感主导
    if days_since <= 3.0:
        time_dominance = 1.0
        emotion_dominance = 0.6
    else:
        time_dominance = 0.5
        emotion_dominance = 1.0

    base = (
        importance
        * (activation ** 0.3)
        * decay
        * (OB_EMOTION_BASE + arousal * OB_AROUSAL_BOOST * emotion_dominance)
        * boost_factor
    )
    score = _ob_time_weight(days_since) * time_dominance * base

    if memory.get("resolved"):
        score *= 0.05
    if memory.get("digested"):
        score *= 0.3
    if arousal > 0.7 and not memory.get("resolved"):
        score *= 1.5

    return round(score, 4)


def compute_decayed_weight(
    base_weight: float,
    age_days: float,
    hit_count: int = 0,
    depth: Optional[int] = None,
    arousal: Optional[float] = None,
    resolved: bool = False,
) -> float:
    """统一衰减公式 · 写入端 + M 代谢共用单一来源

    base × time × activation × depth × arousal × resolved
    时间走艾宾浩斯 B 曲线（半衰期 ~10 天，floor 0.3）。
    """
    base = float(base_weight) if base_weight is not None else 1.0
    time_w = 1.0 if age_days <= 1 else max(0.3, math.exp(-0.069 * (age_days - 1)))
    hc = hit_count or 0
    activation = max(1.0, hc ** 0.3) if hc > 0 else 1.0
    depth_factor = {5: 1.5, 4: 1.3, 3: 1.0, 2: 0.8, 1: 0.6}.get(depth, 1.0)
    arousal_val = arousal if arousal is not None else 0.5
    arousal_factor = 1.0 + arousal_val * 0.5
    resolved_factor = 0.05 if resolved else 1.0
    return round(
        base * time_w * activation * depth_factor * arousal_factor * resolved_factor,
        2,
    )


# === Russell 圆环情绪距离 ===

def russell_distance(coord_a: tuple[float, float], coord_b: tuple[float, float]) -> float:
    """欧式距离 / sqrt(2)。返回 [0, 1]，越小越近"""
    dv = coord_a[0] - coord_b[0]
    da = coord_a[1] - coord_b[1]
    return math.sqrt(dv * dv + da * da) / math.sqrt(2)


def resonance_score(user_coord: tuple[float, float], memory: dict) -> float:
    """情绪共鸣：距离越近分越高 × OB 活力"""
    v = float(memory.get("valence") or 0.5)
    a = float(memory.get("arousal") or 0.3)
    dist = russell_distance(user_coord, (v, a))
    emotion_score = max(0.0, 1.0 - dist)
    vitality = ob_score(memory)
    return emotion_score * (1 + vitality / 10.0)


def find_resonant(
    user_coord: tuple[float, float],
    candidates: list[dict],
    limit: int = 2,
    min_score: float = 0.3,
) -> list[dict]:
    """从候选记忆里挑情绪最近的 top-N

    candidates 每条需含 valence/arousal；缺省走默认值。
    """
    scored = [(m, resonance_score(user_coord, m)) for m in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    out: list[dict] = []
    for m, score in scored[:limit * 3]:
        if score < min_score:
            continue
        out.append({**m, "_resonance": round(score, 3)})
        if len(out) >= limit:
            break
    return out


# === 时间涟漪 ===

def apply_time_ripple(
    hit_ids: list[int],
    fetch_time_range: Callable[[list[int]], Optional[tuple[datetime, datetime]]],
    boost_neighbors: Callable[[datetime, datetime, list[int], float, float], int],
    ripple_hours: int = 48,
    boost_amount: float = 0.3,
    boost_cap: float = 3.0,
) -> int:
    """命中后给 ±N 小时内的邻居加临时 activation_boost

    Args:
        hit_ids: 被检索命中的记忆 id
        fetch_time_range: hit_ids → (t_min, t_max) 或 None
        boost_neighbors: (start, end, exclude_ids, boost, cap) → 受影响行数
        ripple_hours: 涟漪范围
        boost_amount: 每次涟漪给的 boost 增量
        boost_cap: 单条记忆 boost 封顶
    """
    if not hit_ids:
        return 0
    rng = fetch_time_range(hit_ids)
    if not rng:
        return 0
    t_min, t_max = rng
    start = t_min - timedelta(hours=ripple_hours)
    end = t_max + timedelta(hours=ripple_hours)
    return boost_neighbors(start, end, hit_ids, boost_amount, boost_cap)
