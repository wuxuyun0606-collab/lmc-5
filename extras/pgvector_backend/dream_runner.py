"""做梦管线运行器 · 把整条夜间管线串成一个可 cron 的入口

每晚跑一次，把白天积累的原始对话变成结构化记忆：

    consolidate → hippocampus → heartbeat_detector → narrative(weekly)
                                                      ↑ 每月初加 monthly
    → z-audit → patrol

每步可选（传 None 就跳）。失败隔离——一步挂了不影响后续步骤。

用法：
    # 最小配置：只跑 consolidate + hippocampus
    runner = DreamRunner(
        consolidate=my_consolidate_fn,
        hippocampus=my_hippocampus_fn,
    )
    result = runner.run()

    # 满血配置
    runner = DreamRunner(
        consolidate=my_consolidate_fn,
        hippocampus=my_hippocampus_fn,
        heartbeat_detect=my_heartbeat_fn,
        narrative_weekly=my_weekly_fn,
        narrative_monthly=my_monthly_fn,
        z_audit=my_z_audit_fn,
        patrol=my_patrol_fn,
        e_axis_backfill=my_e_backfill_fn,
    )
    result = runner.run()

    # cron 入口
    python -m extras.pgvector_backend.dream_runner

设计：
    - 每步是一个 Callable，不传就跳——零耦合
    - 每步独立 try/except——一步挂了不影响后续
    - 返回 DreamResult 包含每步的状态和耗时
    - monthly 只在每月前 3 天触发（可配）
    - 支持 --dry-run（只打印会跑什么，不真跑）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

log = logging.getLogger("lmc5.dream_runner")


@dataclass
class StepResult:
    """单步结果"""
    name: str
    status: str             # "ok" / "skipped" / "error"
    duration_s: float = 0
    output: Any = None
    error: str = ""


@dataclass
class DreamResult:
    """整条管线结果"""
    started_at: str
    finished_at: str
    steps: list[StepResult] = field(default_factory=list)
    total_duration_s: float = 0

    @property
    def summary(self) -> str:
        lines = [f"Dream run: {self.started_at} → {self.finished_at} ({self.total_duration_s:.1f}s)"]
        for s in self.steps:
            tag = "✓" if s.status == "ok" else ("⊘" if s.status == "skipped" else "✗")
            line = f"  {tag} {s.name}: {s.status}"
            if s.duration_s > 0:
                line += f" ({s.duration_s:.1f}s)"
            if s.error:
                line += f" — {s.error}"
            lines.append(line)
        return "\n".join(lines)


class DreamRunner:
    """做梦管线 · 把夜间所有步骤串成一条线

    每个参数是一个 callable，不传就跳过该步。

    Args:
        consolidate:        () -> Any   原始事件 → chunks
        hippocampus:        () -> Any   chunks → 候选记忆（dry-run 或 apply）
        heartbeat_detect:   () -> Any   chunks → 心跳时刻 + 情绪碎片检测
        narrative_weekly:   () -> Any   生成本周叙事索引
        narrative_monthly:  () -> Any   生成本月叙事索引
        z_audit:            () -> Any   Z 线冲突审计
        patrol:             () -> Any   数据库巡检（只读）
        e_axis_backfill:    () -> Any   E 轴评分补全
        monthly_day_limit:  int         每月前 N 天才跑 monthly（默认 3）
    """

    STEP_ORDER = [
        "consolidate",
        "hippocampus",
        "heartbeat_detect",
        "e_axis_backfill",
        "narrative_weekly",
        "narrative_monthly",
        "z_audit",
        "patrol",
    ]

    def __init__(
        self,
        consolidate: Optional[Callable[[], Any]] = None,
        hippocampus: Optional[Callable[[], Any]] = None,
        heartbeat_detect: Optional[Callable[[], Any]] = None,
        narrative_weekly: Optional[Callable[[], Any]] = None,
        narrative_monthly: Optional[Callable[[], Any]] = None,
        z_audit: Optional[Callable[[], Any]] = None,
        patrol: Optional[Callable[[], Any]] = None,
        e_axis_backfill: Optional[Callable[[], Any]] = None,
        monthly_day_limit: int = 3,
    ):
        for name, fn in [
            ("consolidate", consolidate),
            ("hippocampus", hippocampus),
            ("heartbeat_detect", heartbeat_detect),
            ("narrative_weekly", narrative_weekly),
            ("narrative_monthly", narrative_monthly),
            ("z_audit", z_audit),
            ("patrol", patrol),
            ("e_axis_backfill", e_axis_backfill),
        ]:
            if fn is not None and not callable(fn):
                raise TypeError(f"DreamRunner: {name} must be callable or None, got {type(fn).__name__}")

        self._steps = {
            "consolidate": consolidate,
            "hippocampus": hippocampus,
            "heartbeat_detect": heartbeat_detect,
            "narrative_weekly": narrative_weekly,
            "narrative_monthly": narrative_monthly,
            "z_audit": z_audit,
            "patrol": patrol,
            "e_axis_backfill": e_axis_backfill,
        }
        self.monthly_day_limit = monthly_day_limit

    def _should_run_monthly(self) -> bool:
        return datetime.now().day <= self.monthly_day_limit

    def _run_step(self, name: str) -> StepResult:
        fn = self._steps.get(name)
        if fn is None:
            return StepResult(name=name, status="skipped")

        if name == "narrative_monthly" and not self._should_run_monthly():
            return StepResult(name=name, status="skipped",
                              error=f"day {datetime.now().day} > {self.monthly_day_limit}")

        t0 = time.time()
        try:
            output = fn()
            elapsed = time.time() - t0
            log.info("dream step '%s' completed in %.1fs", name, elapsed)
            return StepResult(name=name, status="ok", duration_s=elapsed, output=output)
        except Exception as e:
            elapsed = time.time() - t0
            log.error("dream step '%s' failed after %.1fs: %s", name, elapsed, e)
            return StepResult(name=name, status="error", duration_s=elapsed, error=str(e))

    def run(self, dry_run: bool = False) -> DreamResult:
        """跑完整条管线。dry_run=True 只打印会跑什么，不真跑。"""
        started = datetime.now()
        steps: list[StepResult] = []

        if dry_run:
            for name in self.STEP_ORDER:
                fn = self._steps.get(name)
                if fn is None:
                    steps.append(StepResult(name=name, status="skipped"))
                elif name == "narrative_monthly" and not self._should_run_monthly():
                    steps.append(StepResult(name=name, status="skipped",
                                            error=f"day {started.day} > {self.monthly_day_limit}"))
                else:
                    steps.append(StepResult(name=name, status="would_run"))
            return DreamResult(
                started_at=started.isoformat(),
                finished_at=started.isoformat(),
                steps=steps,
                total_duration_s=0,
            )

        log.info("=== Dream run starting at %s ===", started.isoformat())
        for name in self.STEP_ORDER:
            steps.append(self._run_step(name))

        finished = datetime.now()
        total = (finished - started).total_seconds()
        result = DreamResult(
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            steps=steps,
            total_duration_s=total,
        )
        log.info("=== Dream run finished in %.1fs ===", total)
        log.info("\n%s", result.summary)
        return result


# === CLI 入口 ===

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="LMC-5 Dream Runner")
    parser.add_argument("--dry-run", action="store_true", help="Print what would run without running")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    # 空 runner 做 dry-run demo — 实际部署时替换成真正的 callable
    runner = DreamRunner()
    result = runner.run(dry_run=args.dry_run)
    print(result.summary)
    sys.exit(0 if all(s.status != "error" for s in result.steps) else 1)
