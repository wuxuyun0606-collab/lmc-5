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
            'AML_STATE_DIR': str(tmp / 'state'),
            'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}


def invoke(args, env):
    return subprocess.run([sys.executable, *map(str, args)], env=env,
                          capture_output=True, text=True, timeout=15)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def serving(tmp_path, request):
    use_embedding = getattr(request, "param", {}).get("embedding", False)
    state = {'invalid_score': False, 'http_status': 200, 'calls': 0, 'requests': [], 'usage': {'input_tokens': 20}, 'retry_after': None, 'delay': 0}
    state_lock = threading.Lock()

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path == '/embeddings':
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                rejected = any(len(text.encode('utf-8')) > 7680 for text in body['input'])
                payload = ({'error': {'code': 'InvalidParameter', 'message': 'input length too long'}}
                           if rejected else {'data': [{'index': i, 'embedding': [1., 0., 0.]} for i, _ in enumerate(body['input'])],
                                             'usage': {'total_tokens': 10}})
                raw = json.dumps(payload).encode()
                self.send_response(400 if rejected else 200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            assert self.path == '/run'
            assert self.headers['Authorization'] == 'Bearer stub-not-a-real-token'
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['model'] == 'jev-1.13.0'
            assert 'Question: ' in body['state'] and 'Memory: ' in body['state']
            with state_lock:
                state['calls'] += 1
                state['requests'].append(body['state'])
            if state.get('drop_response'):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if state['delay']:
                time.sleep(state['delay'])
            payload = json.dumps({'model': 'jev-1.13.0',
                                  'answers': {'rel': {'score': 'invalid' if state['invalid_score'] else 2.5}},
                                  'usage': state['usage']}).encode()
            if state['http_status'] != 200:
                payload = b'{}'
            self.send_response(state['http_status'])
            self.send_header('Content-Length', str(len(payload)))
            if state['retry_after'] is not None:
                self.send_header('Retry-After', state['retry_after'])
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    stub = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    env = clean_env(tmp_path)
    key = tmp_path / 'stub-key'
    key.write_text('stub-not-a-real-token')
    port = free_port()
    env.update(EXAM_ALLOW_TEST_HARNESS='1', AML_RERANK_TEST_URL=f'http://127.0.0.1:{stub.server_port}/run',
               AML_JEV_KEY_PATH=str(key), AML_API_KEY=AUTH,
               AML_NO_EMBED='1', AML_TRIAL_PORT=str(port), AML_BIND='127.0.0.1',
               AML_RERANK_RPM='100000', AML_RERANK_BURST='200', AML_RERANK_DEADLINE_SEC='0.5',
               AML_RERANK_BREAKER_COOLDOWN='0.1')
    if use_embedding:
        env.update(AML_NO_EMBED='0', DASHSCOPE_API_KEY='synthetic-embedding-key',
                   DASHSCOPE_BASE_URL=f'http://127.0.0.1:{stub.server_port}', EMBED_DIM='3')
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
        yield request, state, env
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
    request, state, _ = serving
    code, _, health = request('/health', auth=False)
    assert code == 200 and health['rerank']['mode'] == 'jev'
    assert health['rerank']['test_mode'] is True
    assert health['request_concurrency']['limit'] == 4
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


def test_return_cap_and_optional_usage_without_ledger(serving):
    request, state, env = serving
    assert request('/v1/memories/add', add_body(55))[0] == 200
    code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'] == 'rerank'
    assert len(result['data']) == 50 and state['calls'] == 55
    code, _, health = request('/health')
    stats = health['rerank']
    assert stats['calls_ok'] == 55 and stats['calls_unknown'] == 0
    assert stats['input_tokens_reported'] == 55 * 20
    assert 'budget' not in stats and 'est_usd_settled' not in stats


def test_bad_provider_score_falls_back_without_retry(serving):
    request, state, _ = serving
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


def test_production_import_has_no_billing_modules(tmp_path):
    env = clean_env(tmp_path)
    env['AML_API_KEY'] = AUTH
    code = (
        f"import runpy, sys; m = runpy.run_path({str(MODULE / 'shell/serve_exam.py')!r}); "
        "assert not any('ledger' in name or name == 'rerank' for name in sys.modules); "
        "assert m['server'].rerank.MODE == 'jev'"
    )
    result = invoke(['-c', code], env)
    assert result.returncode == 0, result.stderr
    assert not list(Path(env['AML_STATE_DIR']).rglob('*ledger*'))


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
    (state / 'run').mkdir(parents=True)
    port = free_port()
    (state / 'run/public.env').write_text(
        f'AML_API_KEY={AUTH}\nAML_NO_EMBED=1\nAML_TRIAL_PORT={port}\n'
        'EXAM_ALLOW_TEST_HARNESS=1\nAML_RERANK_TEST_URL=http://127.0.0.1:1/run\n')
    env.update(AML_PYTHON=sys.executable, EXAM_ALLOW_TEST_HARNESS='1')
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
            assert 'budget' not in rr
            assert rr['calls_sent'] == 0
            assert not (state / 'data/ledger').exists()
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


@pytest.mark.parametrize('status', [401, 403, 503, 529])
def test_failed_call_does_not_latch_stop_or_replay(serving, status):
    request, state, _ = serving
    assert request('/v1/memories/add', add_body(1))[0] == 200
    state['http_status'] = status
    code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'].startswith('fallback')
    assert len(result['data']) == 1 and state['calls'] == 1
    state['http_status'] = 200
    code, headers, _ = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit followup', 'top_k': 100})
    assert code == 200 and headers['X-AML-Rerank'] == 'rerank'
    assert state['calls'] == 2


@pytest.mark.parametrize('usage', [None, {}, {'input_tokens': -1}, {'input_tokens': True},
                                  {'input_tokens': '20'}, {'input_tokens': 10**12}])
def test_usage_never_blocks_valid_scores(serving, usage):
    request, state, _ = serving
    request('/v1/memories/add', add_body(1))
    state['usage'] = usage
    for _ in range(2):
        code, headers, result = request('/v1/memories/search', {'user_id': 'alice', 'query': 'orbit', 'top_k': 100})
        assert code == 200 and headers['X-AML-Rerank'] == 'rerank'
        assert result['data'][0]['score'] == 2.5
    assert state['calls'] == 2


def test_retry_after_is_honored_without_a_persistent_stop(serving):
    request, state, _ = serving
    request('/v1/memories/add', add_body(1))
    state.update(http_status=429, retry_after='1')
    body = {'user_id': 'alice', 'query': 'orbit', 'top_k': 100}
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'].startswith('fallback')
    state.update(http_status=200, retry_after=None)
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'].startswith('fallback')
    assert state['calls'] == 1
    time.sleep(1.05)
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'] == 'rerank'
    assert state['calls'] == 2


def test_search_deadline_returns_fallback_and_later_search_works(serving):
    request, state, _ = serving
    request('/v1/memories/add', add_body(1))
    state['delay'] = 1
    body = {'user_id': 'alice', 'query': 'orbit', 'top_k': 100}
    t0 = time.monotonic()
    code, headers, result = request('/v1/memories/search', body)
    assert time.monotonic() - t0 < .9
    assert code == 200 and headers['X-AML-Rerank'].startswith('fallback') and result['data']
    state['delay'] = 0
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'] == 'rerank'
    time.sleep(1.05)
    assert state['calls'] == 2  # no replay after the late first response


def test_connection_loss_does_not_stop_next_search(serving):
    request, state, _ = serving
    request('/v1/memories/add', add_body(1))
    body = {'user_id': 'alice', 'query': 'orbit', 'top_k': 100}
    state['drop_response'] = True
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'].startswith('fallback')
    assert state['calls'] == 1
    state['drop_response'] = False
    assert request('/v1/memories/search', body)[1]['X-AML-Rerank'] == 'rerank'
    assert state['calls'] == 2
    assert request('/health')[2]['rerank']['calls_unknown'] == 1
