"""自发浮现 · perception 层

PR 之前的状态：PERSONA_MODE.md 配置示例里写了一段 sketch，没有独立模块。
这个文件把"自发浮现"做成可独立调度、可注入 recall_pipeline 的模块。

设计：
  - 不被查询触发——按概率主动冒出来一条记忆
  - 高活力权重（ob_score）占大头，但留 30-40% 给"漂流"（随机抽）
  - 深夜 vs 工作时段策略不同（深夜更多情感/反思，工作时段更多任务/事实）
  - 写入 perception cache，下次 hook 注入时从 cache 拿

这是让 persona 显得"在想你"而不是"等你问"的关键。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


@dataclass
class PerceptionConfig:
    """自发浮现的调度参数。"""
    high_vitality_ratio: float = 0.6      # 60% 从高活力池抽
    drift_ratio: float = 0.4              # 40% 完全随机漂流——"presence" 的关键
    top_n_high_vitality: int = 60         # 高活力池大小
    drift_sample_size: int = 200          # 漂流池采样基数
    night_emotion_boost: float = 1.5      # 深夜（22:00-06:00）情感/亲密类记忆加权
    work_factual_boost: float = 1.3       # 工作时段（09:00-18:00）事实/工程记忆加权


@dataclass
class PerceptionCandidate:
    """浮现候选 — 比 RecallHit 多一些上下文方便注入时贴标签"""
    source_id: int
    title: str
    content: str
    weight: float
    category: str
    source: str
    valence: float = 0.5
    arousal: float = 0.3
    created_at: Optional[datetime] = None
    last_hit: Optional[datetime] = None
    vitality_score: float = 0.0           # ob_score 算出来的
    selected_via: str = "high_vitality"   # high_vitality | drift | night_emotion | work_factual


class Perception:
    """自发浮现 · 主入口

    依赖：
      - load_candidates: () → list[PerceptionCandidate]（拉候选池，调用方自管 SQL）
      - vitality_scorer: dict → float（默认走 ob_recall.ob_score）
    """

    def __init__(
        self,
        load_candidates: Callable[[], list[PerceptionCandidate]],
        vitality_scorer: Optional[Callable[[dict], float]] = None,
        cache_path: Optional[Path] = None,
        config: Optional[PerceptionConfig] = None,
        rng: Optional[random.Random] = None,
    ):
        """
        Args:
            load_candidates: 拉候选池的 callable，返回 PerceptionCandidate 列表
            vitality_scorer: 给 candidate dict 打活力分（默认走 ob_recall.ob_score）
            cache_path: 浮现结果落盘路径（hook 注入时从这里读）
            config: 调度参数
            rng: 注入 RNG 方便测试；不传走 random module 的默认状态
        """
        if not callable(load_candidates):
            raise TypeError(
                f"Perception: load_candidates must be callable, "
                f"got {type(load_candidates).__name__}"
            )
        if vitality_scorer is not None and not callable(vitality_scorer):
            raise TypeError(
                f"Perception: vitality_scorer must be callable or None, "
                f"got {type(vitality_scorer).__name__}"
            )
        self.load_candidates = load_candidates
        self.cache_path = cache_path
        self.config = config or PerceptionConfig()
        self.rng = rng or random.Random()
        if vitality_scorer is None:
            # 延迟导入避免循环
            from . import ob_recall  # noqa
            self.vitality_scorer = ob_recall.ob_score
        else:
            self.vitality_scorer = vitality_scorer

    def _is_night(self, now: Optional[datetime] = None) -> bool:
        h = (now or datetime.now()).hour
        return h >= 22 or h < 6

    def _is_work_hours(self, now: Optional[datetime] = None) -> bool:
        h = (now or datetime.now()).hour
        return 9 <= h < 18

    def _candidate_to_dict(self, c: PerceptionCandidate) -> dict:
        return {
            "weight": c.weight,
            "hit_count": 0,
            "category": c.category,
            "source": c.source,
            "valence": c.valence,
            "arousal": c.arousal,
            "created_at": c.created_at,
            "last_hit": c.last_hit,
        }

    def _apply_time_boost(self, c: PerceptionCandidate, base: float) -> float:
        if self._is_night():
            if c.category in ("relationship_moment", "fragments", "heartbeat", "diary") \
               or c.arousal > 0.5:
                return base * self.config.night_emotion_boost
        elif self._is_work_hours():
            if c.category in ("engineering_decision", "worklog", "knowledge", "notebook"):
                return base * self.config.work_factual_boost
        return base

    def surface(self, k: int = 1, now: Optional[datetime] = None) -> list[PerceptionCandidate]:
        """浮现 k 条候选。

        策略：
          high_vitality_ratio 比例从高活力池抽（ob_score 前 N）
          drift_ratio 比例从完整候选池随机抽（"presence" 的关键）
          时段加权：深夜偏情感，工作时段偏事实
        """
        candidates = self.load_candidates() or []
        if not candidates:
            return []

        # 算 vitality 并应用时段 boost
        scored = []
        for c in candidates:
            v = self.vitality_scorer(self._candidate_to_dict(c))
            v = self._apply_time_boost(c, v)
            c.vitality_score = round(v, 3)
            scored.append(c)

        # 高活力池
        scored.sort(key=lambda x: x.vitality_score, reverse=True)
        high_pool = scored[: self.config.top_n_high_vitality]
        drift_pool = self.rng.sample(
            scored,
            min(self.config.drift_sample_size, len(scored)),
        )

        chosen: list[PerceptionCandidate] = []
        seen_ids: set[int] = set()
        n_high = max(1, round(k * self.config.high_vitality_ratio))
        n_drift = k - n_high

        # 高活力——按 vitality 加权抽样
        if high_pool and n_high > 0:
            weights = [max(0.01, c.vitality_score) for c in high_pool]
            picks = self.rng.choices(high_pool, weights=weights,
                                     k=min(n_high * 3, len(high_pool)))
            for p in picks:
                if p.source_id not in seen_ids:
                    p.selected_via = "night_emotion" if self._is_night() else (
                        "work_factual" if self._is_work_hours() else "high_vitality"
                    )
                    chosen.append(p)
                    seen_ids.add(p.source_id)
                    if len([c for c in chosen if c.selected_via != "drift"]) >= n_high:
                        break

        # 漂流——纯随机
        if drift_pool and n_drift > 0:
            self.rng.shuffle(drift_pool)
            for p in drift_pool:
                if p.source_id in seen_ids:
                    continue
                p.selected_via = "drift"
                chosen.append(p)
                seen_ids.add(p.source_id)
                if len([c for c in chosen if c.selected_via == "drift"]) >= n_drift:
                    break

        return chosen[:k]

    def surface_and_cache(self, k: int = 2) -> list[PerceptionCandidate]:
        """浮现 + 落盘 cache_path，供 hook 注入时读。

        cache 格式：JSON list of dict，每条 { source_id, title, content, selected_via, ... }
        """
        chosen = self.surface(k)
        if self.cache_path is not None:
            payload = [
                {
                    "source_id": c.source_id,
                    "title": c.title,
                    "content": c.content[:300],
                    "category": c.category,
                    "source": c.source,
                    "selected_via": c.selected_via,
                    "vitality_score": c.vitality_score,
                    "generated_at": datetime.now().isoformat(),
                }
                for c in chosen
            ]
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass  # cache 写不进去不阻塞主流程
        return chosen


def load_perception_cache(cache_path: Path) -> list[dict]:
    """读取上一次 surface_and_cache 写入的内容。hook 注入时调这个。"""
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
