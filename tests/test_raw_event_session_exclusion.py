"""Regression tests for excluding the active hook session from raw recall."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class FakeCursor:
    def __init__(self, conn, rows):
        self.conn = conn
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.conn.calls.append((query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, *row_batches):
        self.row_batches = list(row_batches)
        self.calls = []

    def cursor(self):
        rows = self.row_batches.pop(0) if self.row_batches else []
        return FakeCursor(self, rows)


def _assert_filter_precedes_limit(query: str) -> None:
    assert "session_id IS DISTINCT FROM %s" in query
    assert query.index("session_id IS DISTINCT FROM %s") < query.index("ORDER BY")
    assert query.index("ORDER BY") < query.index("LIMIT %s")


def test_raw_events_excludes_active_session_before_order_and_limit():
    from extras.pgvector_backend.recall_pipeline import raw_events_search_adapter

    conn = FakeConnection([])
    search = raw_events_search_adapter(conn, exclude_session_id="active-session")

    assert search("old wolf", 5) == []
    query, params = conn.calls[0]
    _assert_filter_precedes_limit(query)
    assert params == ("old wolf", "old wolf", "90", "active-session", 5)


def test_literal_excludes_active_session_before_order_and_limit():
    from extras.pgvector_backend.recall_pipeline import literal_raw_events_search_adapter

    conn = FakeConnection([])
    search = literal_raw_events_search_adapter(
        conn,
        include_neighbors=False,
        exclude_session_id="active-session",
    )

    assert search("搜一下老狼王", 5) == []
    query, params = conn.calls[0]
    _assert_filter_precedes_limit(query)
    assert params[-3:] == ("30", "active-session", 5)


@pytest.mark.parametrize(
    "factory, row",
    [
        (
            "raw",
            (1, "user", "legacy raw", 0.5, "2026-07-16", None),
        ),
        (
            "literal",
            (2, "user", "legacy literal", "2026-07-16", None),
        ),
    ],
)
def test_missing_exclusion_keeps_legacy_null_session_rows(factory, row):
    from extras.pgvector_backend.recall_pipeline import (
        literal_raw_events_search_adapter,
        raw_events_search_adapter,
    )

    conn = FakeConnection([row])
    if factory == "raw":
        search = raw_events_search_adapter(conn)
        hits = search("legacy", 5)
    else:
        search = literal_raw_events_search_adapter(conn, include_neighbors=False)
        hits = search("搜一下legacy", 5)

    query, _ = conn.calls[0]
    assert "session_id IS DISTINCT FROM %s" not in query
    assert hits[0].metadata["session_id"] is None


def test_literal_neighbors_are_loaded_only_from_a_non_excluded_hit_session():
    from extras.pgvector_backend.recall_pipeline import literal_raw_events_search_adapter

    hit = (20, "user", "old wolf", "2026-07-15", "historical-session")
    neighbors = [
        (19, "assistant", "before", "2026-07-15"),
        (20, "user", "old wolf", "2026-07-15"),
        (21, "assistant", "after", "2026-07-15"),
    ]
    conn = FakeConnection([hit], neighbors)
    search = literal_raw_events_search_adapter(
        conn,
        exclude_session_id="active-session",
    )

    hits = search("搜一下 old wolf", 1)
    _assert_filter_precedes_limit(conn.calls[0][0])
    assert conn.calls[1][1][0] == "historical-session"
    assert hits[0].metadata["session_id"] == "historical-session"
    assert "before" in hits[0].content


def test_pipeline_build_wires_exclusion_to_both_raw_adapters(monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")

    from extras.pgvector_backend import embedders, perception, recall_pipeline, rerankers
    from extras.pgvector_backend.hooks import user_prompt_submit as hook

    conn = FakeConnection()
    captured = {}

    def fake_literal_adapter(pg, **kwargs):
        assert pg is conn
        captured["literal"] = kwargs
        return lambda query, top_k: []

    def fake_raw_adapter(pg, **kwargs):
        assert pg is conn
        captured["raw"] = kwargs
        return lambda query, top_k: []

    monkeypatch.setenv("LMC5_PG_DSN", "postgresql://unused")
    monkeypatch.setattr(psycopg2, "connect", lambda dsn: conn)
    monkeypatch.setattr(embedders, "get_embedder", lambda: None)
    monkeypatch.setattr(rerankers, "get_reranker", lambda: None)
    monkeypatch.setattr(perception, "load_perception_cache", lambda path: [])
    monkeypatch.setattr(
        recall_pipeline,
        "literal_raw_events_search_adapter",
        fake_literal_adapter,
    )
    monkeypatch.setattr(
        recall_pipeline,
        "raw_events_search_adapter",
        fake_raw_adapter,
    )

    hook.build_pipeline_from_env(exclude_session_id="active-session")

    assert captured["literal"]["exclude_session_id"] == "active-session"
    assert captured["raw"]["exclude_session_id"] == "active-session"


@pytest.mark.parametrize(
    "session_fields, expected",
    [
        ({"session_id": " snake-session "}, "snake-session"),
        ({"sessionId": "camel-session"}, "camel-session"),
        (
            {"session_id": "   ", "sessionId": "camel-fallback"},
            "camel-fallback",
        ),
        ({"session_id": "   "}, None),
        ({"session_id": 123}, None),
    ],
)
def test_user_prompt_hook_passes_normalized_session_to_pipeline(
    monkeypatch, session_fields, expected
):
    from extras.pgvector_backend.hooks import user_prompt_submit as hook

    captured = []

    class FakePipeline:
        def recall(self, prompt):
            assert prompt == "remember the old wolf"
            return SimpleNamespace(injection_text="")

    def fake_build_pipeline_from_env(*, exclude_session_id=None):
        captured.append(exclude_session_id)
        return FakePipeline()

    event = {"prompt": "remember the old wolf", **session_fields}
    monkeypatch.setattr(hook, "build_pipeline_from_env", fake_build_pipeline_from_env)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(hook.sys, "stdout", io.StringIO())

    assert hook.main() == 0
    assert captured == [expected]
