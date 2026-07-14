from __future__ import annotations

import pytest

from extras.pgvector_backend.dream_runner import DreamRunner
from extras.pgvector_backend.night_dream import (
    Chunk,
    NightDream,
    NightDreamProposerError,
    NightDreamWriteError,
)


def _chunk() -> Chunk:
    return Chunk(id=1, text="A meaningful source passage. " * 8)


def _candidate() -> dict[str, object]:
    return {
        "type": "event",
        "title": "Recovered memory",
        "content": "A durable candidate grounded in the source passage.",
        "importance": 8,
        "risk": "normal",
        "evidence": "A meaningful source passage.",
        "source_chunk_ids": [1],
    }


def test_proposer_exception_is_not_converted_to_empty_success() -> None:
    def failing_proposer(_chunks: list[Chunk]) -> list[dict[str, object]]:
        raise TimeoutError("provider timed out")

    dream = NightDream(proposer=failing_proposer)

    with pytest.raises(NightDreamProposerError, match="provider timed out"):
        dream.run([_chunk()])


def test_valid_empty_candidate_list_remains_success() -> None:
    dream = NightDream(proposer=lambda _chunks: [])

    result = dream.run([_chunk()], apply=False)

    assert result.candidates == []
    assert result.promoted == []
    assert result.written_ids == []
    assert result.proposer_errors == 0
    assert result.write_errors == 0


def test_apply_requires_a_candidate_writer() -> None:
    dream = NightDream(proposer=lambda _chunks: [_candidate()])

    with pytest.raises(NightDreamWriteError, match="requires write_candidate"):
        dream.run([_chunk()], apply=True)


def test_candidate_write_exception_aborts_run_for_retry() -> None:
    def failing_writer(_candidate: object) -> int:
        raise OSError("database unavailable")

    dream = NightDream(
        proposer=lambda _chunks: [_candidate()],
        write_candidate=failing_writer,
    )

    with pytest.raises(NightDreamWriteError, match="database unavailable"):
        dream.run([_chunk()], apply=True)


def test_dream_runner_marks_proposer_failure_as_error_and_continues() -> None:
    patrol_calls: list[str] = []

    def failing_proposer(_chunks: list[Chunk]) -> list[dict[str, object]]:
        raise ValueError("invalid provider response")

    dream = NightDream(proposer=failing_proposer)
    runner = DreamRunner(
        hippocampus=lambda: dream.run([_chunk()]),
        patrol=lambda: patrol_calls.append("ran"),
    )

    result = runner.run()
    steps = {step.name: step for step in result.steps}

    assert result.ok is False
    assert steps["hippocampus"].status == "error"
    assert "invalid provider response" in steps["hippocampus"].error
    assert steps["patrol"].status == "ok"
    assert patrol_calls == ["ran"]
