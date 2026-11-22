"""Integration tests for an explicit disposable PostgreSQL database with pgvector."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


PG = Path(__file__).resolve().parents[1] / "extras" / "pgvector_backend"


@pytest.fixture
def pg_database():
    dsn = os.environ.get("LMC5_TEST_PG_DSN")
    if not dsn:
        pytest.skip("requires an isolated PostgreSQL database with pgvector")
    import psycopg2
    schema = "test_lmc5_" + uuid4().hex
    with psycopg2.connect(dsn) as admin:
        with admin.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            cur.execute(f"CREATE SCHEMA {schema}")
    scoped = psycopg2.extensions.make_dsn(dsn, options=f"-c search_path={schema},public -c timezone=UTC")
    conn = psycopg2.connect(scoped)
    try:
        yield conn, scoped
    finally:
        conn.close()
        with psycopg2.connect(dsn) as admin, admin.cursor() as cur:
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


def test_vector_top_one_only_sees_live_fact(pg_database):
    from extras.pgvector_backend.vector_pgvector import PgvectorStore
    from extras.pgvector_backend.recall_pipeline import RecallPipeline, vector_search_adapter
    conn, dsn = pg_database
    with conn, conn.cursor() as cur:
        cur.execute((PG / "schema.sql").read_text())
        ids = []
        for title, status, active in [("Alice", "superseded", False),
                                      ("Bob", "current", False), ("Sam", "current", True)]:
            cur.execute("INSERT INTO lmc5_curated_memories "
                        "(source,category,content,fact_key,version_status,active_fact) "
                        "VALUES ('manual','fact',%s,'owner',%s,%s) RETURNING id",
                        (title, status, active))
            ids.append(cur.fetchone()[0])
    store = PgvectorStore(dsn)
    query_vec = [1., 0.] + [0.] * 3070
    for row, title, offset in zip(ids, ["Alice", "Bob", "Sam"], [0., .1, .2]):
        store.write_vector("curated", row, title, [1., offset] + [0.] * 3070)
    search = vector_search_adapter(store, lambda _: query_vec)
    assert [hit.content for hit in search("owner", 1)] == ["Sam"]
    result = RecallPipeline(vector_search=search).recall("Who owns releases?")
    assert "Sam" in result.injection_text
    assert all(name not in result.injection_text for name in ("Alice", "Bob"))


def test_archive_migration_and_replay(pg_database, tmp_path):
    from extras.pgvector_backend.hooks.session_end import archive_session_jsonl
    conn, _ = pg_database
    event = {"role": "user", "content": "same", "timestamp": "2025-01-01T00:00:00Z"}
    with conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE lmc5_raw_events (id bigserial PRIMARY KEY, session_id text, "
                    "role text, content text, metadata jsonb, created_at timestamptz DEFAULT now(), "
                    "UNIQUE(session_id,role,content))")
        cur.execute("INSERT INTO lmc5_raw_events (session_id,role,content,metadata,created_at) "
                    "VALUES ('s','user','same',%s,'2026-01-01T00:00:00Z')",
                    (json.dumps({"timestamp": event["timestamp"]}),))
        for _ in range(2):
            cur.execute((PG / "migrations/20261122_raw_event_identity.sql").read_text())
        cur.execute("SELECT id,created_at,ingested_at FROM lmc5_raw_events")
        old_id, original_time, ingestion = cur.fetchone()
        assert (original_time.year, ingestion.year) == (2025, 2026)
    rows = [event, dict(event), {"uuid":"abc","type":"user","timestamp":0,
                                 "message":{"role":"user","content":"same"}}]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(map(json.dumps, rows)))
    assert archive_session_jsonl(conn, "s", path) == 2
    assert archive_session_jsonl(conn, "s", path) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT id,created_at,ingested_at,event_key FROM lmc5_raw_events ORDER BY id")
        saved = cur.fetchall()
    assert saved[0][:3] == (old_id, original_time, ingestion)
    assert [r[1].year for r in saved] == [2025, 2025, 1970]
    assert len({r[3] for r in saved}) == 3
