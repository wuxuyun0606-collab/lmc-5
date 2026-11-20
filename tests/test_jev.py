import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from extras.pgvector_backend import rerankers
from lmc5 import MemoryStore
from lmc5.cli import main
from lmc5.jev import MODEL, POOL_SIZE, URL, jev_reranker


def receipt(score, model=MODEL):
    return SimpleNamespace(status_code=200, json=lambda: {
        "model": model, "answers": {"rel": {"score": score}}
    })


@pytest.fixture
def http(monkeypatch):
    session = MagicMock()
    client = session.return_value.__enter__.return_value
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=session, RequestException=OSError))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    return client


def test_wire_contract_stable_ties_and_bounded_pool(http):
    hits = [f"note {i} api_key=private-value" for i in range(POOL_SIZE + 2)]
    http.post.side_effect = [receipt(1), receipt(3), receipt(3)] + [receipt(0)] * (POOL_SIZE - 3)
    result = jev_reranker(str)("question " * 9000 + " host=192.0.2.55", hits, len(hits))
    assert result == [hits[1], hits[2], hits[0], *hits[3:]]
    assert http.post.call_count == POOL_SIZE
    call = http.post.call_args_list[0]
    assert call.args == (URL,)
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-only"
    body = call.kwargs["json"]
    assert body["model"] == MODEL and body["questions"]["rel"]["type"] == "score"
    assert "private-value" not in body["state"] and "192.0.2.55" not in body["state"]
    assert len(body["state"].encode()) <= 30000


@pytest.mark.parametrize("bad", [
    receipt(True), receipt(float("nan")), receipt(4), receipt(2, "wrong-model"),
    SimpleNamespace(status_code=429), OSError("failed"),
])
def test_partial_scoring_restores_complete_baseline(http, bad):
    http.post.side_effect = [receipt(0), bad]
    assert jev_reranker(str)("q", ["first", "second", "third"], 2) == ["first", "second"]
    assert http.post.call_count == 2


def test_pg_explicit_choice_and_missing_key(http, monkeypatch):
    monkeypatch.setenv("LMC5_RERANKER", "jev")
    monkeypatch.setenv("VOYAGE_API_KEY", "unused")
    hits = [SimpleNamespace(title=t, content=t, score=s) for t, s in [("A", 1), ("B", 3), ("C", 2)]]
    http.post.side_effect = [receipt(0), receipt(3), receipt(1)]
    assert rerankers.get_reranker()("question", hits, 2) == [hits[2], hits[0]]
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert rerankers.get_reranker() is None


def test_sqlite_entry_point_and_recorded_rank(http, tmp_path, capsys):
    db = str(tmp_path / "example.sqlite")
    with MemoryStore(db) as store:
        store.init()
        for title in ("rollback alpha", "rollback beta", "rollback gamma"):
            store.add_memory(title=title, content=title)
    args = ["recall", "--db", db, "rollback", "--limit", "2"]
    main(args)
    baseline = json.loads(capsys.readouterr().out)
    assert len(baseline) == 2 and not http.post.called

    # The lowest local candidate receives the best external score.
    def post(url, **kwargs):
        state = kwargs["json"]["state"]
        return receipt(0 if any(hit["title"] in state for hit in baseline) else 3)

    http.post.side_effect = post
    main([*args, "--reranker", "jev"])
    ranked = json.loads(capsys.readouterr().out)
    assert len(ranked) == 2 and ranked[0]["id"] not in [hit["id"] for hit in baseline]
    with MemoryStore(db) as store:
        traces = store.list_recall_traces(memory_id=ranked[0]["id"])
        assert traces[0]["rank"] == ranked[0]["trace"]["rank"] == 1


def test_one_candidate_needs_no_request(http):
    assert jev_reranker(str)("q", ["only"], 5) == ["only"]
    http.post.assert_not_called()
