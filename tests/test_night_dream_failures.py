from __future__ import annotations

import pytest

from extras.pgvector_backend.dream_runner import DreamRunner
from extras.pgvector_backend.night_dream import (
    Candidate,
    Chunk,
    NightDream,
    NightDreamDedupError,
    NightDreamProposerError,
    NightDreamWriteError,
)


def _chunk() -> Chunk:
    return Chunk(id=1, text="A meaningful source passage. " * 8)


def _candidate(
    title: str = "Recovered memory",
    importance: int = 8,
) -> dict[str, object]:
    return {
        "type": "event",
        "title": title,
        "content": f"A durable candidate about {title} grounded in the source passage.",
        "importance": importance,
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


def test_malformed_candidate_aborts_before_any_write() -> None:
    writes: list[str] = []
    malformed = _candidate("Broken memory")
    malformed["content"] = "too short"
    dream = NightDream(
        proposer=lambda _chunks: [_candidate("Valid memory"), malformed],
        write_candidate=lambda candidate: writes.append(candidate.title) or 1,
    )

    with pytest.raises(NightDreamProposerError, match=r"candidate\[1\]"):
        dream.run([_chunk()], apply=True)
    assert writes == []


def test_semantic_dedup_exception_aborts_before_writer() -> None:
    writes: list[str] = []

    def failing_dedup(_candidate: object) -> list[int]:
        raise RuntimeError("embedding unavailable")

    dream = NightDream(
        proposer=lambda _chunks: [_candidate("Unchecked memory")],
        find_semantic_duplicates=failing_dedup,
        write_candidate=lambda candidate: writes.append(candidate.title) or 1,
    )

    with pytest.raises(NightDreamDedupError, match="embedding unavailable"):
        dream.run([_chunk()], apply=True)
    assert writes == []


def test_semantic_duplicates_do_not_consume_max_promote_capacity() -> None:
    written: list[str] = []
    candidates = [
        _candidate("Already stored", importance=10),
        _candidate("Next unique", importance=9),
        _candidate("Deferred unique", importance=8),
    ]
    dream = NightDream(
        proposer=lambda _chunks: candidates,
        find_semantic_duplicates=lambda candidate: [99]
        if candidate.title == "Already stored"
        else [],
        write_candidate=lambda candidate: written.append(candidate.title) or len(written),
        max_promote=1,
    )

    result = dream.run([_chunk()], apply=True)

    assert written == ["Next unique"]
    assert [candidate.title for candidate in result.promoted] == ["Next unique"]
    assert ("Already stored", "semantic_dup:99") in [
        (candidate.title, reason) for candidate, reason in result.rejected
    ]
    assert ("Deferred unique", "exceeds_max_promote") in [
        (candidate.title, reason) for candidate, reason in result.rejected
    ]


def test_writer_duplicate_does_not_consume_max_promote_capacity() -> None:
    attempted: list[str] = []
    candidates = [
        _candidate("Idempotent duplicate", importance=10),
        _candidate("Next unique", importance=9),
        _candidate("Deferred unique", importance=8),
    ]

    def writer(candidate: Candidate) -> int | None:
        attempted.append(candidate.title)
        return None if candidate.title == "Idempotent duplicate" else 101

    dream = NightDream(
        proposer=lambda _chunks: candidates,
        write_candidate=writer,
        max_promote=1,
    )

    result = dream.run([_chunk()], apply=True)

    assert attempted == ["Idempotent duplicate", "Next unique"]
    assert [candidate.title for candidate in result.promoted] == ["Next unique"]
    assert result.written_ids == [101]
    assert ("Idempotent duplicate", "idempotent_duplicate") in [
        (candidate.title, reason) for candidate, reason in result.rejected
    ]
    assert ("Deferred unique", "exceeds_max_promote") in [
        (candidate.title, reason) for candidate, reason in result.rejected
    ]


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
