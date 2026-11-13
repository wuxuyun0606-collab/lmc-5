"""Exercise the historical four-request admission queue with local synthetic work."""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import pytest
from test_incident import servers


@pytest.fixture
def queued(servers):
    server = servers[1]
    server.API_KEY = ''
    server.REQUEST_CONCURRENCY = 4
    server._request_slots = threading.BoundedSemaphore(4)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    opener = build_opener(ProxyHandler({}))

    def request(path, body=None):
        req = Request(f'http://127.0.0.1:{httpd.server_port}{path}', data=body,
                      headers={'Content-Type': 'application/json'})
        try:
            result = opener.open(req, timeout=10)
        except HTTPError as exc:
            result = exc
        with result:
            return result.status, json.load(result)

    try:
        yield server, request
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=3)


def test_mixed_requests_admit_four_and_health_bypasses_queue(queued):
    server, request = queued
    release, full = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = calls = 0

    def work(body):
        nonlocal active, peak, calls
        with lock:
            active += 1
            peak = max(peak, active)
            calls += 1
            if active == 4:
                full.set()
        release.wait(5)
        with lock:
            active -= 1
        return 200, {'data': [], 'request': body['n']}

    server.do_add = server.do_search = work
    with ThreadPoolExecutor(max_workers=16) as pool:
        jobs = [pool.submit(request, '/v1/memories/' + ('add' if i % 2 else 'search'),
                            json.dumps({'n': i}).encode()) for i in range(16)]
        try:
            assert full.wait(3)
            code, health = request('/health')
            assert code == 200 and health['request_concurrency']['active'] == 4
        finally:
            release.set()
        rows = [job.result() for job in jobs]
    assert peak == 4 and calls == 16
    assert all(code == 200 for code, _ in rows)
    assert {body['request'] for _, body in rows} == set(range(16))
    # The client can read the body before the handler's finally releases its slot.
    deadline = time.monotonic() + 2
    while server.request_concurrency_snapshot()['active'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert server.request_concurrency_snapshot() == {'limit': 4, 'active': 0, 'waiting': 0}


def test_exceptions_and_bad_json_release_slots(queued):
    server, request = queued

    def fail(body):
        raise RuntimeError('synthetic')

    server.do_search = fail
    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda _: request('/v1/memories/search', b'{}')[0], range(16)))
    assert codes == [500] * 16
    assert request('/v1/memories/search', b'{')[0] == 400
    server.do_search = lambda body: (200, {'data': []})
    assert request('/v1/memories/search', b'{}') == (200, {'data': []})
    # The client can read the body before the handler's finally releases its slot.
    deadline = time.monotonic() + 2
    while server.request_concurrency_snapshot()['active'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert server.request_concurrency_snapshot() == {'limit': 4, 'active': 0, 'waiting': 0}
