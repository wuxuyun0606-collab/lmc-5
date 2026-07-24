from __future__ import annotations

import json
import os
import time

from extras.pgvector_backend.recall_history import (
    InMemoryRecallHistory,
    JsonFileRecallHistory,
    recall_identity,
)
from extras.pgvector_backend.recall_pipeline import RecallHit, RecallPipeline
from extras.pgvector_backend.hooks.user_prompt_submit import extract_session_id


def _ranked_hits(_query, _top_k):
    return [
        RecallHit(source_id=1, title="one", content="first", score=0.9, channel="vector"),
        RecallHit(source_id=2, title="two", content="second", score=0.8, channel="vector"),
        RecallHit(source_id=3, title="three", content="third", score=0.7, channel="vector"),
    ]


def test_same_session_filters_previous_winner_and_backfills():
    history = InMemoryRecallHistory()
    pipeline = RecallPipeline(
        vector_search=_ranked_hits,
        final_top_k=1,
        injection_history=history,
    )

    first = pipeline.recall("query", session_id="session-a")
    second = pipeline.recall("query", session_id="session-a")
    fresh = pipeline.recall("query", session_id="session-b")

    assert [hit.source_id for hit in first.hits] == [1]
    assert [hit.source_id for hit in second.hits] == [2]
    assert [hit.source_id for hit in fresh.hits] == [1]
    assert second.trace["cascade"]["session_history"] == {
        "enabled": True,
        "session_id_present": True,
        "suppressed": 1,
        "marked": 1,
        "status": "filtered",
    }


def test_missing_session_id_keeps_legacy_stateless_behavior():
    pipeline = RecallPipeline(
        vector_search=_ranked_hits,
        final_top_k=1,
        injection_history=InMemoryRecallHistory(),
    )
    assert pipeline.recall("query").hits[0].source_id == 1
    assert pipeline.recall("query").hits[0].source_id == 1


def test_file_history_persists_across_instances_and_stores_hashes_only(tmp_path):
    first = JsonFileRecallHistory(tmp_path)
    raw_key = recall_identity("curated", 42)
    first.mark("private-session-id", [raw_key])

    second = JsonFileRecallHistory(tmp_path)
    assert second.seen("private-session-id", [raw_key, recall_identity("curated", 7)]) == {raw_key}

    state = next(tmp_path.glob("session-*.json")).read_text(encoding="utf-8")
    payload = json.loads(state)
    assert payload["version"] == 1
    assert raw_key not in state
    assert "private-session-id" not in state


def test_history_read_failure_is_fail_open():
    class BrokenHistory:
        def seen(self, _session_id, _keys):
            raise RuntimeError("read unavailable")

        def mark(self, _session_id, _keys):
            return None

    result = RecallPipeline(
        vector_search=_ranked_hits,
        final_top_k=1,
        injection_history=BrokenHistory(),
    ).recall("query", session_id="session-a")

    assert result.hits[0].source_id == 1
    assert result.trace["cascade"]["session_history"]["status"] == "read_error_fail_open"


def test_history_key_keeps_owner_namespaces_separate():
    history = InMemoryRecallHistory()
    history.mark("s", [recall_identity("curated", 1)])
    assert history.seen(
        "s", [recall_identity("curated", 1), recall_identity("raw_events", 1)]
    ) == {recall_identity("curated", 1)}


def test_expired_file_history_does_not_suppress(tmp_path):
    history = JsonFileRecallHistory(tmp_path, ttl_seconds=60)
    key = recall_identity("curated", 1)
    new_key = recall_identity("curated", 2)
    history.mark("s", [key])
    state = next(tmp_path.glob("session-*.json"))
    old = time.time() - 61
    os.utime(state, (old, old))
    assert history.seen("s", [key]) == set()
    history.mark("s", [new_key])
    assert history.seen("s", [key, new_key]) == {new_key}


def test_hook_extracts_runtime_session_id_without_global_bucket(monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    assert extract_session_id({"session_id": "abc"}) == "abc"
    assert extract_session_id({}) == ""
