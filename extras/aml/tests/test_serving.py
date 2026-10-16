"""Offline acceptance tests: local HTTP providers, synthetic messages, no paid calls."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import pytest

MODULE = Path(__file__).resolve().parents[1]
REPO = MODULE.parents[1]
AUTH = 'local-test-auth-' + 'a' * 32


def clean_env(tmp):
    return {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp),
            'PYTHONPATH': str(REPO / 'src'), 'PYTHONDONTWRITEBYTECODE': '1',
            'AML_STATE_DIR': str(tmp / 'state')}


def invoke(args, env):
    return subprocess.run([sys.executable, *map(str, args)], env=env,
                          capture_output=True, text=True, timeout=15)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def serving(tmp_path):
    state = {'invalid_score': False, 'http_status': 200, 'calls': 0, 'requests': []}
    state_lock = threading.Lock()

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.path == '/run'
            assert self.headers['Authorization'] == 'Bearer stub-not-a-real-token'
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['model'] == 'jev-1.13.0'
            assert 'Question: ' in body['state'] and 'Memory: ' in body['state']
            with state_lock:
                state['calls'] += 1
                state['requests'].append(body['state'])
            payload = json.dumps({'model': 'jev-1.13.0',
                                  'answers': {'rel': {'score': 'invalid' if state['invalid_score'] else 2.5}},
                                  'usage': {'input_tokens': 20}}).encode()
            if state['http_status'] != 200:
                payload = b'{}'
            self.send_response(state['http_status'])
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    stub = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    env = clean_env(tmp_path)
    ledger = tmp_path / 'ledger.sqlite'
    init = invoke([MODULE / 'jev/wo2_budget_admin_jev.py', 'init', '--ledger', ledger,
                   '--budget-id', 'offline-test', '--route', 'test', '--grant-usd', '1',
                   '--note', 'synthetic local acceptance', '--i-authorize'], env)
    assert init.returncode == 0, init.stderr
    key = tmp_path / 'stub-key'
    key.write_text('stub-not-a-real-token')
    port = free_port()
    env.update(EXAM_ALLOW_TEST_HARNESS='1', AML_RERANK_TEST_URL=f'http://127.0.0.1:{stub.server_port}/run',
               AML_RERANK_LEDGER=str(ledger), AML_RERANK_BUDGET_ID='offline-test',
               AML_RERANK_BUDGET_USD='1', AML_JEV_KEY_PATH=str(key), AML_API_KEY=AUTH,
               AML_NO_EMBED='1', AML_TRIAL_PORT=str(port), AML_BIND='127.0.0.1',
               AML_RERANK_RPM='100000', AML_RERANK_BURST='200', AML_RERANK_DEADLINE_SEC='10')
    base = f'http://127.0.0.1:{port}'
    opener = build_opener(ProxyHandler({}))

    def request(path, body=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + AUTH
        req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers)
        try:
            response = opener.open(req, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), json.load(response)

    proc = subprocess.Popen([sys.executable, str(MODULE / 'shell/serve_exam.py')], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if proc.poll() is not None:
                pytest.fail('service exited: ' + proc.communicate()[1])
            try:
                if request('/health', auth=False)[0] == 200:
                    break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail('service did not start')
        yield request, state, env, ledger
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        stub.shutdown()
        stub.server_close()


def add_body(count=3):
    return {'request_id': 'synthetic-add', 'user_id': 'alice', 'session_id': 'demo',
            'messages': [{'role': 'user', 'content': f'orbit workshop unique note {i}',
                          'timestamp': 1700000000000 + i * 1000} for i in range(count)]}


def test_auth_health_and_user_isolation(serving):
    request, state, _, _ = serving
    code, _, health = request('/health', auth=False)
    assert code == 200 and health['rerank']['mode'] == 'jev'
    assert health['rerank']['test_mode'] is True
    for path in ('/v1/memories/add', '/v1/memories/search'):
        assert request(path, {}, auth=False)[0] == 401
    body = add_body()
    assert request('/v1/memories/add', body)[0] == 200
    assert request('/v1/memories/add', body)[0] == 200
    changed = {**body, 'session_id': 'changed'}
    assert request('/v1/memories/add', changed)[0] == 409
    code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and len(result['data']) == 3
    assert headers['X-AML-Rerank'] == 'rerank'
    assert state['calls'] == 3
    assert [x['created_at'] for x in result['data']] == sorted(x['created_at'] for x in result['data'])
    code, _, result = request('/v1/memories/search', {'user_id': 'bob', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and result['data'] == []
    assert state['calls'] == 3


def test_return_cap_and_durable_usage_ledger(serving):
    request, state, env, ledger = serving
    assert request('/v1/memories/add', add_body(55))[0] == 200
    code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'] == 'rerank'
    assert len(result['data']) == 50 and state['calls'] == 55
    code, _, health = request('/health')
    stats = health['rerank']
    assert stats['calls_ok'] == 55 and stats['calls_unknown'] == 0
    assert stats['budget']['committed_usd'] == '0.000046200'
    audit = invoke([MODULE / 'jev/wo2_budget_admin_jev.py', 'audit', '--ledger', ledger], env)
    assert audit.returncode == 0 and json.loads(audit.stdout)['ok']


def test_bad_provider_score_falls_back_without_retry(serving):
    request, state, _, _ = serving
    assert request('/v1/memories/add', add_body())[0] == 200
    state['invalid_score'] = True
    code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'].startswith('fallback')
    assert len(result['data']) == 3
    # A bad receipt can cancel other candidates before they send. No candidate
    # may be resent; do not require all three to send before the abort wins.
    assert 1 <= state['calls'] <= 3
    assert len(set(state['requests'])) == state['calls']
    assert all(x['score'] < 1 for x in result['data'])


def test_production_launcher_refuses_missing_auth(tmp_path):
    result = invoke([MODULE / 'shell/serve_exam.py'], clean_env(tmp_path))
    assert result.returncode != 0 and 'AML_API_KEY missing/too short' in result.stderr


def test_production_launcher_refuses_budget_override(tmp_path):
    env = clean_env(tmp_path)
    env.update(AML_API_KEY=AUTH, AML_RERANK_BUDGET_USD='20')
    result = invoke([MODULE / 'shell/serve_exam.py'], env)
    assert result.returncode != 0 and 'inherited controlled/harness vars' in result.stderr


def test_production_launcher_refuses_missing_ledger(tmp_path):
    env = clean_env(tmp_path)
    env['AML_API_KEY'] = AUTH
    result = invoke([MODULE / 'shell/serve_exam.py'], env)
    assert result.returncode != 0 and 'ledger refused' in result.stderr


@pytest.mark.parametrize('mode', ['fallback', 'error', 'unexpected'])
def test_bridge_restores_rrf_and_caps_fallback(mode):
    spec = importlib.util.spec_from_file_location('isolated_bridge', MODULE / 'shell/exam_jev_bridge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Backend:
        MODE = 'jev'
        RETURN_N = 100

        def apply(self, query, cands, top_k):
            assert top_k == 100
            if mode == 'error':
                raise RuntimeError('synthetic')
            return [(999, 3.0)], {'mode': mode}

    bridge = module.ExamJevBridge(Backend(), log=lambda _: None)
    cands = [(i, .02, 'synthetic') for i in range(70)]
    pairs, _ = bridge.apply('question', cands, 100)
    assert pairs == [(i, .02) for i in range(50)]


def test_public_startup_clears_harness_and_restarts_with_fresh_run(tmp_path):
    """Exercise the documented paid-profile startup without issuing a paid request."""
    env = clean_env(tmp_path)
    state = Path(env['AML_STATE_DIR'])
    ledger = state / 'data/ledger/rerank_exam_jev.sqlite'
    ledger.parent.mkdir(parents=True)
    init = invoke([MODULE / 'jev/wo2_budget_admin_jev.py', 'init', '--ledger', ledger,
                   '--budget-id', 'aml-jev-serving-v1', '--route', 'jev', '--grant-usd', '10',
                   '--note', 'offline startup check, no sends', '--i-authorize'], env)
    assert init.returncode == 0, init.stderr
    (state / 'run').mkdir()
    port = free_port()
    (state / 'run/public.env').write_text(
        f'AML_API_KEY={AUTH}\nAML_NO_EMBED=1\nAML_TRIAL_PORT={port}\n'
        'EXAM_ALLOW_TEST_HARNESS=1\nAML_RERANK_TEST_URL=http://127.0.0.1:1/run\n'
        'AML_RERANK_BUDGET_USD=20\n')
    env.update(AML_PYTHON=sys.executable, EXAM_ALLOW_TEST_HARNESS='1', AML_RERANK_BUDGET_USD='20')
    opener = build_opener(ProxyHandler({}))
    runs = []
    for _ in range(2):
        proc = subprocess.Popen(['/bin/bash', str(MODULE / 'start.sh')], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for attempt in range(100):
                if proc.poll() is not None:
                    pytest.fail(proc.communicate()[1])
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health', timeout=1) as res:
                        health = json.load(res)
                    break
                except OSError:
                    time.sleep(.05)
            else:
                pytest.fail('public launcher did not start')
            rr = health['rerank']
            assert rr['mode'] == 'jev' and rr['test_mode'] is False
            assert rr['budget']['cap_usd'] == '10.000000000'
            assert rr['calls_sent'] == 0 and rr['budget']['committed_usd'] == '0.000000000'
            runs.append(rr['run_id'])
            second = subprocess.run(['/bin/bash', str(MODULE / 'start.sh')], env=env,
                                    capture_output=True, text=True, timeout=10)
            assert second.returncode != 0 and 'another rerank instance' in second.stderr
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
    assert runs[0] != runs[1]


def test_transient_529_retains_charge_and_next_search_recovers(serving):
    import sqlite3
    request, state, env, ledger = serving
    assert request('/v1/memories/add', add_body(1))[0] == 200
    state['http_status'] = 529
    code, headers, _ = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'].startswith('fallback')
    assert state['calls'] == 1
    with sqlite3.connect(ledger) as db:
        aid, charge = db.execute("SELECT attempt_id,charge_nano FROM attempts WHERE status='UNKNOWN'").fetchone()
    state['http_status'] = 200
    code, headers, _ = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit followup', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'] == 'rerank'
    assert state['calls'] == 2  # The first request was never replayed.
    with sqlite3.connect(ledger) as db:
        assert db.execute('SELECT status,charge_nano FROM attempts WHERE attempt_id=?', (aid,)).fetchone() == ('UNKNOWN_KEPT', charge)
    assert json.loads(invoke([MODULE / 'jev/wo2_budget_admin_jev.py', 'audit', '--ledger', ledger], env).stdout)['ok']


def test_auth_stop_is_not_automatically_recovered(serving):
    request, state, _, _ = serving
    assert request('/v1/memories/add', add_body(1))[0] == 200
    state['http_status'] = 401
    assert request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})[1]['X-AML-Rerank'].startswith('fallback')
    state['http_status'] = 200
    assert request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit followup', 'top_k': 100})[1]['X-AML-Rerank'].startswith('fallback')
    assert state['calls'] == 1
