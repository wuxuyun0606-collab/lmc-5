"""做梦管线运行器 · 把整条夜间管线串成一个可 cron 的入口

每晚跑一次，把白天积累的原始对话变成结构化记忆：

    consolidate → nap → hippocampus (incl. Y relation build) → heartbeat_detector
        → e_axis_backfill → timeline_sweep(each X-line)
        → narrative_weekly → narrative_monthly (每月初) → z_audit → patrol

每步可选（传 None 就跳）。失败隔离——一步挂了不影响后续步骤。

⚠️ 关于 Y 关系图：**关系图建在 hippocampus 这一步内部**，没有独立的 step。
   传给 `hippocampus=` 的 callable 应该端到端调 `NightDream.run()`，它会同时
   提议候选记忆 *和* 给 promoted 候选扩 top-K 邻居写关系——通过 propose 阶段
   LLM 直接给的 `relation_hints`，不是事后 pair-by-pair classify。详见
   `night_dream.py` 和 `docs/Y_RELATIONS.md`。

⚠️ M 轴衰减/去重 和 stopword learning **不在 dream_runner 内**。这些步骤
   需要单独调度（自己写 callable 或单独的 cron 行）。本文件聚焦"raw events
   → curated memories + relations + Z 审计 + 叙事索引"主流程。

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
        nap=my_nap_fn,                      # light missing-vector/orphan-edge pass
        heartbeat_detect=my_heartbeat_fn,  # detector candidates only; no curated direct insert
        timeline_sweep=my_thread_cleanup_fn,
        timeline_threads=["safety", "engineering", "frontend", "other"],
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
    - timeline_sweep 逐条 X 线执行，单线失败不阻断其他线
    - monthly 只在每月前 3 天触发（可配）
    - DreamSchedule 提供每日 04:00 local-time cron 生成与测试入口
    - 支持 --dry-run（只打印会跑什么，不真跑）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("lmc5.dream_runner")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


@dataclass
class StepResult:
    """单步结果"""
    name: str
    status: str             # "ok" / "skipped" / "error"
    duration_s: float = 0
    output: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_s": round(float(self.duration_s or 0), 3),
            "output": _jsonable(self.output),
            "error": self.error,
        }


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
            tag = (
                "✓"
                if s.status == "ok"
                else ("⊘" if s.status == "skipped" else ("→" if s.status == "would_run" else "✗"))
            )
            line = f"  {tag} {s.name}: {s.status}"
            if s.duration_s > 0:
                line += f" ({s.duration_s:.1f}s)"
            if s.error:
                line += f" — {s.error}"
            lines.append(line)
        return "\n".join(lines)

    @property
    def ok(self) -> bool:
        return not any(step.status == "error" for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.status] = counts.get(step.status, 0) + 1
        return {
            "ok": self.ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_s": round(float(self.total_duration_s or 0), 3),
            "step_counts": counts,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class DreamSchedule:
    """Local-time nightly schedule for the dream runner.

    This does not install cron by itself. It gives deployments a single
    testable source of truth for the intended 04:00 local run time.
    """

    hour: int = 4
    minute: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError("DreamSchedule.hour must be between 0 and 23")
        if not 0 <= self.minute <= 59:
            raise ValueError("DreamSchedule.minute must be between 0 and 59")

    @property
    def cron_expression(self) -> str:
        return f"{self.minute} {self.hour} * * *"

    def should_start_at(self, now: datetime) -> bool:
        """Return True only for the scheduled local minute."""

        return now.hour == self.hour and now.minute == self.minute

    def next_run_after(self, now: datetime) -> datetime:
        """Return the next scheduled local datetime strictly after ``now``."""

        candidate = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def cron_line(
        self,
        *,
        working_dir: str = "/opt/lmc5-agent",
        python: str = "/opt/lmc5-agent/.venv/bin/python",
        module: str = "extras.pgvector_backend.dream_runner",
        log_path: str = "logs/nightly.log",
    ) -> str:
        return (
            f"{self.cron_expression}  cd {working_dir} && "
            f"{python} -m {module} >> {log_path} 2>&1"
        )


@dataclass
class TimelineSweepResult:
    """One X-line cleanup/reflection attempt."""

    thread: str
    status: str
    output: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread": self.thread,
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }


class DreamRunner:
    """做梦管线 · 把夜间所有步骤串成一条线

    每个参数是一个 callable，不传就跳过该步。

    Args:
        consolidate:        () -> Any   原始事件 → chunks
        hippocampus:        () -> Any   chunks → 候选记忆（dry-run 或 apply）
        nap:                () -> Any   session-switch/lightweight maintenance: missing vectors
                               and orphan relation links.
        heartbeat_detect:   () -> Any   chunks → detector candidates only. It must not write
                               raw heartbeat_detector output directly to curated memories.
        timeline_sweep:     (thread) -> Any 逐条 X 线整理/反思/清理
        narrative_weekly:   () -> Any   生成本周叙事索引
        narrative_monthly:  () -> Any   生成本月叙事索引
        z_audit:            () -> Any   Z 线冲突审计
        patrol:             () -> Any   数据库巡检（只读）
        e_axis_backfill:    () -> Any   E 轴评分补全
        monthly_day_limit:  int         每月前 N 天才跑 monthly（默认 3）
    """

    STEP_ORDER = [
        "consolidate",
        "nap",
        "hippocampus",
        "heartbeat_detect",
        "e_axis_backfill",
        "timeline_sweep",
        "narrative_weekly",
        "narrative_monthly",
        "z_audit",
        "patrol",
    ]

    def __init__(
        self,
        consolidate: Optional[Callable[[], Any]] = None,
        nap: Optional[Callable[[], Any]] = None,
        hippocampus: Optional[Callable[[], Any]] = None,
        heartbeat_detect: Optional[Callable[[], Any]] = None,
        timeline_sweep: Optional[Callable[[str], Any]] = None,
        timeline_threads: Sequence[str] | Callable[[], Sequence[str]] = (),
        narrative_weekly: Optional[Callable[[], Any]] = None,
        narrative_monthly: Optional[Callable[[], Any]] = None,
        z_audit: Optional[Callable[[], Any]] = None,
        patrol: Optional[Callable[[], Any]] = None,
        e_axis_backfill: Optional[Callable[[], Any]] = None,
        monthly_day_limit: int = 3,
        clock: Callable[[], datetime] = datetime.now,
    ):
        for name, fn in [
            ("consolidate", consolidate),
            ("nap", nap),
            ("hippocampus", hippocampus),
            ("heartbeat_detect", heartbeat_detect),
            ("timeline_sweep", timeline_sweep),
            ("narrative_weekly", narrative_weekly),
            ("narrative_monthly", narrative_monthly),
            ("z_audit", z_audit),
            ("patrol", patrol),
            ("e_axis_backfill", e_axis_backfill),
            ("timeline_threads", timeline_threads),
            ("clock", clock),
        ]:
            if fn is not None and not callable(fn):
                if name != "timeline_threads":
                    raise TypeError(
                        f"DreamRunner: {name} must be callable or None, "
                        f"got {type(fn).__name__}"
                    )
        if (
            not callable(timeline_threads)
            and timeline_threads is not None
            and not isinstance(timeline_threads, Sequence)
        ):
            raise TypeError(
                "DreamRunner: timeline_threads must be a sequence, callable, or None, "
                f"got {type(timeline_threads).__name__}"
            )
        if isinstance(timeline_threads, (str, bytes)):
            raise TypeError("DreamRunner: timeline_threads must be a sequence of thread names")

        self._steps = {
            "consolidate": consolidate,
            "nap": nap,
            "hippocampus": hippocampus,
            "heartbeat_detect": heartbeat_detect,
            "timeline_sweep": timeline_sweep,
            "narrative_weekly": narrative_weekly,
            "narrative_monthly": narrative_monthly,
            "z_audit": z_audit,
            "patrol": patrol,
            "e_axis_backfill": e_axis_backfill,
        }
        self.timeline_threads = timeline_threads
        self.monthly_day_limit = monthly_day_limit
        self.clock = clock

    def _should_run_monthly(self) -> bool:
        return self.clock().day <= self.monthly_day_limit

    def _resolve_timeline_threads(self) -> list[str]:
        raw = self.timeline_threads() if callable(self.timeline_threads) else self.timeline_threads
        seen: set[str] = set()
        threads: list[str] = []
        for thread in raw or ():
            clean = str(thread).strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            threads.append(clean)
        return threads

    def _run_timeline_sweep(self) -> StepResult:
        fn = self._steps.get("timeline_sweep")
        if fn is None:
            return StepResult(name="timeline_sweep", status="skipped")

        threads = self._resolve_timeline_threads()
        if not threads:
            return StepResult(
                name="timeline_sweep",
                status="skipped",
                error="no timeline_threads configured",
            )

        t0 = time.time()
        results: list[TimelineSweepResult] = []
        for thread in threads:
            try:
                results.append(TimelineSweepResult(thread=thread, status="ok", output=fn(thread)))
            except Exception as e:
                log.error("dream timeline_sweep '%s' failed: %s", thread, e)
                results.append(TimelineSweepResult(thread=thread, status="error", error=str(e)))
        elapsed = time.time() - t0
        status = "error" if any(r.status == "error" for r in results) else "ok"
        return StepResult(
            name="timeline_sweep",
            status=status,
            duration_s=elapsed,
            output=results,
        )

    def _run_step(self, name: str) -> StepResult:
        if name == "timeline_sweep":
            return self._run_timeline_sweep()

        fn = self._steps.get(name)
        if fn is None:
            return StepResult(name=name, status="skipped")

        if name == "narrative_monthly" and not self._should_run_monthly():
            return StepResult(name=name, status="skipped",
                              error=f"day {self.clock().day} > {self.monthly_day_limit}")

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
        started = self.clock()
        steps: list[StepResult] = []

        if dry_run:
            for name in self.STEP_ORDER:
                fn = self._steps.get(name)
                if fn is None:
                    steps.append(StepResult(name=name, status="skipped"))
                elif name == "timeline_sweep" and not self._resolve_timeline_threads():
                    steps.append(StepResult(name=name, status="skipped",
                                            error="no timeline_threads configured"))
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

        finished = self.clock()
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
