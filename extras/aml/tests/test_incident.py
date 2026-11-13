"""Portable regressions for the historical coding-run repair; no provider calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

HERE = Path(__file__).resolve().parent
from lmc5 import MemoryStore


@pytest.fixture
def servers(tmp_path, monkeypatch):
    rr = types.ModuleType('rerank')
    rr.MODE, rr.ACTIVE = 'off', False
    keys = ('searches', 'reranked', 'fallback', 'fallback_reasons', 'calls_sent',
            'calls_unknown', 'http_429', 'valid_candidates', 'queue_wait_s',
            'breaker_open')
    rr.stats_snapshot = lambda: dict.fromkeys(keys, 0)
    rr.apply = lambda query, candidates, k: (
        [(mid, score) for mid, score, content in candidates[:k]], {'mode': 'rerank'})
    monkeypatch.setitem(sys.modules, 'rerank', rr)
    monkeypatch.setitem(sys.modules, 'rerank_jev', rr)
    monkeypatch.setitem(sys.modules, 'exam_jev_bridge', types.SimpleNamespace(ExamJevBridge=lambda backend: backend))
    monkeypatch.setitem(sys.modules, 'aml_paths', types.SimpleNamespace(STATE_DIR=tmp_path, SECRETS_DIR=tmp_path/'secrets'))
    # Prevent inherited switches or credentials from enabling external calls.
    for key, value in {'AML_NO_EMBED': '1', 'AML_MH': 'off', 'AML_MH2': 'off',
                       'AML_ABSTAIN_FLOOR': 'off', 'AML_NEIGHBOR_TURNS': 'on',
                       'AML_TIME_ORDER': 'on'}.items():
        monkeypatch.setenv(key, value)
    result = [None]
    for name, source in [('new', HERE.parent / 'shell/server.py')]:
        root = tmp_path / name
        monkeypatch.setenv('AML_TRIAL_DATA', str(root))
        monkeypatch.setenv('AML_TRIAL_LOG', str(root / 'timing.jsonl'))
        spec = importlib.util.spec_from_file_location('tested_' + name, source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result.append(module)
    return result


def open_close(server, uid):
    with server.user_lock(uid):
        server.open_store(uid).close()


def add(server, uid, n):
    return server.do_add({'request_id': f'{uid}-{n}', 'user_id': uid,
        'session_id': 'session', 'messages': [{'role': 'user',
        'timestamp': 1700000000000 + n * 60000,
        'content': f'Alice project Orion release {n} uses Python on server alpha.'}]})


def test_init_once_per_user_concurrent_and_restart(servers, monkeypatch):
    server = servers[1]
    original = MemoryStore.init
    calls = []
    def count(store):
        calls.append(store.path)
        return original(store)
    monkeypatch.setattr(MemoryStore, 'init', count)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda uid: open_close(server, uid), ['a', 'b', 'a', 'b'] * 4))
    assert calls.count(server.db_path('a')) == calls.count(server.db_path('b')) == 1
    server._initialized_stores.clear()  # process-local cache resets on restart
    open_close(server, 'a')
    assert calls.count(server.db_path('a')) == 2


def test_failed_init_closes_connection_and_retries(servers, monkeypatch):
    server = servers[1]
    original = MemoryStore.init
    attempts = []
    def fail_once(store):
        attempts.append(store)
        if len(attempts) == 1:
            raise OSError('synthetic initialization failure')
        return original(store)
    monkeypatch.setattr(MemoryStore, 'init', fail_once)
    with pytest.raises(OSError):
        open_close(server, 'a')
    assert server.db_path('a') not in server._initialized_stores
    with pytest.raises(Exception, match='closed'):
        attempts[0].conn.execute('SELECT 1')
    open_close(server, 'a')
    assert len(attempts) == 2


def test_independent_users_do_not_hold_global_guard(servers, monkeypatch):
    server = servers[1]
    barrier = threading.Barrier(2)
    original = MemoryStore.init
    def synchronized(store):
        barrier.wait(timeout=5)
        original(store)
    monkeypatch.setattr(MemoryStore, 'init', synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda uid: open_close(server, uid), ['a', 'b']))


def test_incremental_fts_entities_vectors_and_idempotency(servers):
    server = servers[1]
    open_close(server, 'alice')
    assert add(server, 'alice', 1)[0] == 200
    assert add(server, 'alice', 1)[0] == 200
    with server.user_lock('alice'):
        store = server.open_store('alice')
        try:
            assert store.conn.execute('SELECT count(*) FROM memories').fetchone()[0] == 1
            assert store.conn.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'Orion'").fetchone()[0] == 1
            assert store.conn.execute('SELECT count(*) FROM memory_entities WHERE memory_id=1').fetchone()[0] > 0
            store.upsert_vector(owner_type='memory', owner_id=1, vector=[1., 0., 0.], provider='offline', model='test')
        finally:
            store.close()
    with server.user_lock('alice'):
        store = server.open_store('alice')
        try:
            hits = store.search_vectors(query_vector=[1., 0., 0.], provider='offline', model='test', limit=1)
            assert hits[0]['record']['id'] == 1
        finally:
            store.close()
    assert server.do_search({'query': 'Orion', 'user_id': 'alice', 'top_k': 3})[1]['data']


@pytest.mark.parametrize('error', [OSError, RuntimeError, KeyError])
def test_stats_failure_does_not_break_search_response_or_health(servers, error):
    server = servers[1]
    server.rerank.ACTIVE, server.rerank.MODE = True, 'test'
    add(server, 'alice', 1)
    def fail():
        raise error('synthetic statistics failure')
    server.rerank.stats_snapshot = fail
    code, payload = server.do_search({'query': 'Orion', 'user_id': 'alice', 'top_k': 3})
    assert code == 200 and payload['data']
    event = json.loads(server.LOG_PATH.read_text().splitlines()[-1])
    assert event['rerank_cum']['metrics_error'] == error.__name__
    handler = object.__new__(server.Handler)
    handler.wfile = io.BytesIO()
    codes, headers = [], {}
    handler.send_response = codes.append
    handler.send_header = lambda key, value: headers.update({key: value})
    handler.end_headers = lambda: None
    handler._send(code, payload)
    assert codes == [200] and json.loads(handler.wfile.getvalue()) == payload
    assert headers['X-AML-Rerank'] == 'rerank'
    assert server._tls.rerank_meta is None
    handler.wfile = io.BytesIO()
    handler.path = '/health'
    handler.do_GET()
    health = json.loads(handler.wfile.getvalue())
    assert health['status'] == 'ok'
    assert health['rerank']['metrics_error'] == error.__name__


@pytest.mark.parametrize('status', [400, 401, 404, 409, 413, 422, 500])
def test_error_status_logged_without_body_or_credentials(servers, status):
    server = servers[1]
    handler = object.__new__(server.Handler)
    handler.wfile = io.BytesIO()
    codes = []
    handler.send_response = codes.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.path = '/private-path-with-sensitive-query'
    handler.command = 'POST'
    payload = {'detail': {'reason': 'sensitive payload must not enter logs'}}
    handler._send(status, payload)
    assert codes == [status] and json.loads(handler.wfile.getvalue()) == payload
    event = json.loads(server.LOG_PATH.read_text().splitlines()[-1])
    assert event['status'] == status and event['route'] == 'other'
    assert 'sensitive' not in json.dumps(event)


def test_handler_exception_keeps_500_and_logs_upstream_status(servers):
    server = servers[1]
    handler = object.__new__(server.Handler)
    raw = b'{"query":"private text","user_id":"private user","top_k":1}'
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.headers = {'Content-Length': str(len(raw))}
    handler.path, handler.command = '/v1/memories/search', 'POST'
    handler._authed = lambda: True
    codes = []
    handler.send_response = codes.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    class ProviderError(Exception):
        response = types.SimpleNamespace(status_code=429)
    def fail(body):
        raise ProviderError('private upstream URL with credential')
    server.do_search = fail
    handler.do_POST()
    assert codes == [500]
    assert json.loads(handler.wfile.getvalue()) == {'detail': {'reason': 'internal error: ProviderError'}}
    event = json.loads(server.LOG_PATH.read_text().splitlines()[-1])
    assert event['error'] == 'ProviderError' and event['upstream_status'] == 429
    assert 'private' not in json.dumps(event)


def test_failed_error_logging_cannot_prevent_response(servers, capsys):
    server = servers[1]
    def fail_log(event):
        raise OSError('synthetic log failure')
    server.log = fail_log
    handler = object.__new__(server.Handler)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda *args: None
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    payload = {'detail': {'reason': 'invalid JSON'}}
    handler._send(400, payload)
    assert json.loads(handler.wfile.getvalue()) == payload
    assert json.loads(capsys.readouterr().err)['log_error'] == 'OSError'
