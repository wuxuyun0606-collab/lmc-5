from __future__ import annotations

from datetime import datetime

import pytest

from extras.pgvector_backend.dream_runner import DreamRunner, DreamSchedule


def test_dream_schedule_is_daily_0400_local_time() -> None:
    schedule = DreamSchedule()

    assert schedule.cron_expression == "0 4 * * *"
    assert schedule.should_start_at(datetime(2026, 6, 17, 4, 0))
    assert not schedule.should_start_at(datetime(2026, 6, 17, 3, 59))
    assert not schedule.should_start_at(datetime(2026, 6, 17, 4, 1))
    assert schedule.next_run_after(datetime(2026, 6, 17, 3, 59)) == datetime(
        2026, 6, 17, 4, 0
    )
    assert schedule.next_run_after(datetime(2026, 6, 17, 4, 0)) == datetime(
        2026, 6, 18, 4, 0
    )
    assert schedule.cron_line().startswith("0 4 * * *")
    assert "extras.pgvector_backend.dream_runner" in schedule.cron_line()


def test_dream_schedule_validates_time_fields() -> None:
    with pytest.raises(ValueError, match="hour"):
        DreamSchedule(hour=24)

    with pytest.raises(ValueError, match="minute"):
        DreamSchedule(minute=60)


def test_dream_runner_dry_run_respects_monthly_window_and_timeline_threads() -> None:
    runner = DreamRunner(
        consolidate=lambda: "chunks",
        timeline_sweep=lambda thread: thread,
        timeline_threads=["safety", "engineering"],
        narrative_monthly=lambda: "month",
        monthly_day_limit=3,
        clock=lambda: datetime(2026, 6, 17, 4, 0),
    )

    result = runner.run(dry_run=True)
    statuses = {step.name: (step.status, step.error) for step in result.steps}

    assert statuses["consolidate"] == ("would_run", "")
    assert statuses["timeline_sweep"] == ("would_run", "")
    assert statuses["narrative_monthly"][0] == "skipped"
    assert "day 17 > 3" in statuses["narrative_monthly"][1]
    assert "→ consolidate: would_run" in result.summary


def test_dream_runner_sweeps_every_configured_timeline_and_continues_after_errors() -> None:
    calls: list[str] = []

    def timeline_sweep(thread: str) -> str:
        calls.append(thread)
        if thread == "frontend":
            raise RuntimeError("frontend cleanup failed")
        return f"swept:{thread}"

    runner = DreamRunner(
        consolidate=lambda: "chunks",
        hippocampus=lambda: "relations",
        timeline_sweep=timeline_sweep,
        timeline_threads=["safety", "engineering", "frontend", "safety", ""],
        z_audit=lambda: "pending audits",
        patrol=lambda: "warnings",
        clock=lambda: datetime(2026, 6, 17, 4, 0),
    )

    result = runner.run()
    by_name = {step.name: step for step in result.steps}

    assert calls == ["safety", "engineering", "frontend"]
    assert by_name["consolidate"].status == "ok"
    assert by_name["hippocampus"].status == "ok"
    assert by_name["timeline_sweep"].status == "error"
    assert by_name["z_audit"].status == "ok"
    assert by_name["patrol"].status == "ok"

    sweep_results = by_name["timeline_sweep"].output
    assert [item.thread for item in sweep_results] == ["safety", "engineering", "frontend"]
    assert [item.status for item in sweep_results] == ["ok", "ok", "error"]
    assert sweep_results[2].error == "frontend cleanup failed"

    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["step_counts"]["error"] == 1
    sweep_payload = next(step for step in payload["steps"] if step["name"] == "timeline_sweep")
    assert sweep_payload["output"][2]["thread"] == "frontend"
    assert sweep_payload["output"][2]["status"] == "error"


def test_dream_runner_accepts_callable_timeline_thread_loader() -> None:
    calls: list[str] = []
    runner = DreamRunner(
        timeline_sweep=lambda thread: calls.append(thread),
        timeline_threads=lambda: ["identity", "other"],
        clock=lambda: datetime(2026, 6, 17, 4, 0),
    )

    result = runner.run()

    assert calls == ["identity", "other"]
    assert {step.name: step.status for step in result.steps}["timeline_sweep"] == "ok"


def test_dream_runner_validates_timeline_threads_shape() -> None:
    with pytest.raises(TypeError, match="sequence of thread names"):
        DreamRunner(timeline_sweep=lambda thread: thread, timeline_threads="safety")

    with pytest.raises(TypeError, match="timeline_threads"):
        DreamRunner(timeline_sweep=lambda thread: thread, timeline_threads=123)  # type: ignore[arg-type]
