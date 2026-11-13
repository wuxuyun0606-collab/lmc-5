"""TypeSafe JEV reranker without local pricing, budgets or a billing ledger.

Scores retain the original question/memory template, 0–3 scale, stable ordering
and all-or-nothing RRF fallback. A failed call affects its search; it never
creates a persistent stop or gets replayed automatically. Usage is optional
diagnostic metadata, not a condition for accepting an otherwise valid score.

Process-local concurrency, RPM scheduling, provider Retry-After, finite circuit
cooldowns and per-call/search deadlines remain. The send gate checks deadlines
and cancellation before the first request byte. DNS may outlive a deadline, but
the worker cannot subsequently send; apply() does not join those workers.
Health and logs contain metadata only, never queries, memory text or credentials.
"""
from __future__ import annotations

import atexit
import errno as _errno
import fcntl
import json
import math
import os
import re
import socket
import sys
import threading
import time
import uuid
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aml_paths import SECRETS_DIR, PAID_INSTANCE_LOCK
from input_limits import JEV_STATE_BYTES, jev_state
from urllib.parse import urlsplit

import requests
import urllib3.connection as _u3conn
import urllib3.connectionpool as _u3pool
import urllib3.exceptions as _u3e
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---- copied from T/round2/rerank_common.py (frozen) -------------------------------------------
INSTRUCTIONS = "How useful is this memory for answering the question?"
CRITERIA = ["Irrelevant", "Loosely related", "Partially answers", "Directly answers"]
QUESTIONS = {"rel": {"type": "score", "instructions": INSTRUCTIONS, "criteria": CRITERIA}}
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
JEV_KEY_PATH = SECRETS_DIR / ".typesafe_key_aml"
JEV_PROD_KEY_PATH = SECRETS_DIR / ".typesafe_key"  # shared production key is refused


def state_text(question: str, memory: str) -> str:
    return jev_state(question, memory)
# ------------------------------------------------------------------------------------------------

TEST_PATH = "/run"


# ---- strict config ------------------------------------------------------------------------------
def _cfg_fail(msg):
    raise SystemExit("rerank config refused: " + msg)


_INT_RE = re.compile(r"[0-9]{1,9}")
_DEC_RE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9})?")


def env_int(name, default, lo, hi, env=None):
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        if default is None:
            _cfg_fail(name + " is required")
        v = default
    else:
        if not _INT_RE.fullmatch(raw.strip()):
            _cfg_fail(name + " must be a plain integer")
        v = int(raw.strip())
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        _cfg_fail("%s out of range [%s, %s]" % (name, lo, hi))
    return v


def env_dec(name, default, lo_excl, hi, env=None) -> Decimal:
    """Decimal from plain decimal text; rejects nan/inf/snan/exponent/sign; lo < v <= hi."""
    env = os.environ if env is None else env
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        if default is None:
            _cfg_fail(name + " is required")
        s = default
    else:
        s = raw.strip()
    if not isinstance(s, str) or not _DEC_RE.fullmatch(s):
        _cfg_fail(name + " must be a plain finite decimal")
    d = Decimal(s)
    if not d.is_finite() or not (Decimal(lo_excl) < d <= Decimal(hi)):
        _cfg_fail("%s out of range (%s, %s]" % (name, lo_excl, hi))
    return d


def validate_test_url(u: str) -> str:
    """Raises ValueError(reason). Field-by-field, after parsing."""
    if not isinstance(u, str) or any(ch in u for ch in "@\\ \t\r\n") or any(ord(ch) < 32 or ord(ch) > 126 for ch in u):
        raise ValueError("bad_chars")
    p = urlsplit(u)
    if p.scheme != "http":
        raise ValueError("scheme")
    if p.username is not None or p.password is not None:
        raise ValueError("userinfo")
    if p.hostname != "127.0.0.1":
        raise ValueError("host")
    try:
        port = p.port
    except ValueError:
        raise ValueError("port")
    if port is None or not 1 <= port <= 65535 or p.netloc != "127.0.0.1:%d" % port:
        raise ValueError("port")
    if p.path != TEST_PATH or p.query or p.fragment or u.endswith("?") or u.endswith("#"):
        raise ValueError("path_query_fragment")
    return u


MODE = os.environ.get("AML_RERANK", "off").strip().lower() or "off"
if MODE not in ("off", "jev"):
    _cfg_fail("AML_RERANK must be off|jev")
_rn = os.environ.get("AML_RETURN_N", "")
RETURN_N = env_int("AML_RETURN_N", 0, 1, 1000) if _rn.strip() else None
ACTIVE = MODE != "off" or RETURN_N is not None   # False -> server.py takes the untouched path

MAX_CAND = 100                                   # protocol max; rerank mode returns <= 100
_test_url_raw = os.environ.get("AML_RERANK_TEST_URL", "")
TEST_MODE = bool(_test_url_raw)
TEST_URL = None
if TEST_MODE:
    try:
        TEST_URL = validate_test_url(_test_url_raw)
    except ValueError as _e:
        _cfg_fail("AML_RERANK_TEST_URL rejected (%s); must be exactly http://127.0.0.1:<port>/run" % _e)

if MODE != "off":
    PER_SEARCH = env_int("AML_RERANK_PER_SEARCH", 2, 1, 16)
    GLOBAL_INFLIGHT = env_int("AML_RERANK_GLOBAL_INFLIGHT", 16, 1, 64)
    RPM = float(env_dec("AML_RERANK_RPM", "240", "0", "100000" if TEST_MODE else "600"))
    BURST = env_int("AML_RERANK_BURST", 4, 1, 200 if TEST_MODE else 16)
    CALL_TIMEOUT = float(env_dec("AML_RERANK_CALL_TIMEOUT", "20", "0", "120"))
    # Absolute per-Search deadline (fixed at apply() entry). 600 s: nominal 16 x 100 calls at 240 RPM
    # is an ESTIMATED ~400 s (not a measured worst case); fallback is guaranteed at the deadline.
    DEADLINE = float(env_dec("AML_RERANK_DEADLINE_SEC", "600", "0", "1500"))
    RETRY_AFTER_MAX = float(env_dec("AML_RERANK_RETRY_AFTER_MAX", "120", "0", "3600"))
    BREAKER_FAILS = env_int("AML_RERANK_BREAKER_FAILS", 3, 1, 1000)
    BREAKER_COOLDOWN = float(env_dec("AML_RERANK_BREAKER_COOLDOWN", "60", "0", "3600"))
    RUN_ID = os.environ.get("AML_RERANK_RUN_ID", "").strip() or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", RUN_ID):
        _cfg_fail("AML_RERANK_RUN_ID is required ([A-Za-z0-9._:-]{1,80})")
    if os.environ.get("AML_CF_TOKEN_PATH"):
        _cfg_fail("AML_CF_TOKEN_PATH is not used by the jev copy; use AML_JEV_KEY_PATH")
    TOKEN_PATH = Path(os.environ.get("AML_JEV_KEY_PATH") or JEV_KEY_PATH)
    try:
        _tp = TOKEN_PATH.resolve()
    except OSError:
        _tp = TOKEN_PATH
    _real = {JEV_KEY_PATH.resolve(), JEV_PROD_KEY_PATH.resolve()}
    if _tp == JEV_PROD_KEY_PATH.resolve():
        _cfg_fail("the production Jev key is refused (J50 uses the AML key file only; no fallback)")
    if TEST_MODE:
        if _tp in _real or SECRETS_DIR.resolve() in _tp.parents or JEV_KEY_PATH.resolve().parent in _tp.parents \
                or _tp == JEV_KEY_PATH.resolve().parent:
            _cfg_fail("test mode refuses both real Jev keys, their directory and the secrets directory (path only, not read); "
                      "point AML_JEV_KEY_PATH at a stub key file")
else:
    PER_SEARCH = GLOBAL_INFLIGHT = BURST = BREAKER_FAILS = 1
    RPM = CALL_TIMEOUT = DEADLINE = RETRY_AFTER_MAX = BREAKER_COOLDOWN = 1.0
    TOKEN_PATH = None
    RUN_ID = ""

BACKOFF_BASE, BACKOFF_MAX, BACKOFF_EXP_CAP = 2.0, 60.0, 10
EXIT_GRACE = 5.0

# One serving process per state directory. This lock is independent of billing.
_instance_lock_fd = None


def _acquire_instance_lock():
    global _instance_lock_fd
    lp = PAID_INSTANCE_LOCK
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        _cfg_fail("another rerank instance holds the instance lock; refusing to start")
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": os.getpid(), "run_id": RUN_ID}).encode())
    _instance_lock_fd = fd
    return lp


INSTANCE_LOCK_PATH = _acquire_instance_lock() if MODE != "off" else None


# ---- token ------------------------------------------------------------------------------------
_token_lock = threading.Lock()
_token: str | None = None
_token_loaded = False
_token_state = "not_loaded"
_FAKE_TOKEN_RE = re.compile(r"stub-not-a-real-token(?:-[a-z0-9]{1,24})?")


def _get_token():
    global _token, _token_loaded, _token_state
    with _token_lock:
        if not _token_loaded:
            _token_loaded = True
            try:
                v = TOKEN_PATH.read_text().strip()
            except OSError:
                v = ""
            if not v:
                _token, _token_state = None, "no_token"
            elif TEST_MODE and not _FAKE_TOKEN_RE.fullmatch(v):
                _token, _token_state = None, "test_cred_refused"     # never sent to a test endpoint
            else:
                _token, _token_state = v, "ok"
            v = None
        return _token


# ---- stats (metadata only) --------------------------------------------------------------------
_stats_lock = threading.Lock()
STATS = {"searches": 0, "reranked": 0, "fallback": 0, "fallback_reasons": {},
         "calls_sent": 0, "calls_ok": 0, "calls_failed": 0, "calls_unknown": 0,
         "http_429": 0, "valid_candidates": 0, "queue_wait_s": 0.0,
         "input_tokens_reported": 0, "usage_unavailable": 0, "not_sent_proven": 0,
         "retry_after_invalid": 0, "breaker_open_until": 0.0, "cancelled_before_send": 0}
_INFLIGHT: set = set()
_inflight_lock = threading.Lock()


def _stat_add(**kw):
    with _stats_lock:
        for k, v in kw.items():
            STATS[k] = STATS[k] + v


def stats_snapshot() -> dict:
    with _stats_lock:
        s = json.loads(json.dumps(STATS))
    s["mode"] = MODE
    s["return_n"] = RETURN_N
    s["run_id"] = RUN_ID or None
    s["pid"] = os.getpid()
    s["test_mode"] = TEST_MODE
    s["rpm"] = RPM
    s["per_search_concurrency"] = PER_SEARCH
    s["queue_wait_s"] = round(s["queue_wait_s"], 3)
    s["breaker_open"] = time.time() < s.pop("breaker_open_until")
    with _inflight_lock:
        s["inflight_calls"] = len(_INFLIGHT)
    s["rate_hold"] = SCHED.hold_info()
    return s


# ---- process-wide rate scheduler --------------------------------------------------------------
class _Scheduler:
    def __init__(self, rpm: float, burst: int):
        self.cfg_rate = rpm / 60.0
        self.rate = self.cfg_rate
        self.burst = max(1, burst)
        self.tokens = float(self.burst)
        self.t = time.monotonic()
        self.pause_until = 0.0
        self.hold_reason = None
        self.n429_streak = 0
        self.cv = threading.Condition()

    def _refill(self, now):
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def acquire(self, deadline: float, abort: threading.Event) -> bool:
        with self.cv:
            while True:
                if abort.is_set():
                    return False
                now = time.monotonic()
                if now >= deadline:
                    return False
                if self.pause_until > now:
                    if self.pause_until >= deadline:
                        return False          # paused past this Search's deadline: give up now
                    self.cv.wait(min(self.pause_until - now, 0.5))
                    continue
                self._refill(now)
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                need = (1.0 - self.tokens) / self.rate
                if now + need >= deadline:
                    return False
                self.cv.wait(min(need, 0.5))

    REASON_PRIO = {None: 0, "retry_after_over_max": 1}

    def _set_reason(self, new):
        """Caller holds self.cv; call BEFORE extending pause_until."""
        active = self.pause_until > time.monotonic() and self.hold_reason is not None
        if active and self.REASON_PRIO.get(self.hold_reason, 0) >= self.REASON_PRIO.get(new, 0):
            return
        self.hold_reason = new

    def on_429(self, retry_after):
        """retry_after: validated finite >= 0 seconds, or None (absent/invalid)."""
        with self.cv:
            self.n429_streak = min(self.n429_streak + 1, 1000)
            if retry_after is None:
                pause = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** min(self.n429_streak - 1, BACKOFF_EXP_CAP)))
                self._set_reason(None)
            else:
                pause = retry_after                 # honoured in full, never silently capped
                self._set_reason("retry_after_over_max" if retry_after > RETRY_AFTER_MAX else None)
            self.pause_until = max(self.pause_until, time.monotonic() + pause)
            self.rate = max(self.cfg_rate / 16.0, self.rate / 2.0)
            self.tokens = 0.0
            self.cv.notify_all()
            return pause

    def on_ok(self):
        with self.cv:
            self.n429_streak = 0
            self.rate = min(self.cfg_rate, self.rate + 1.0 / 60.0)

    def hold_info(self):
        with self.cv:
            rem = self.pause_until - time.monotonic()
            return {"paused_s": round(rem, 1) if rem > 0 else 0, "reason": self.hold_reason if rem > 0 else None}


SCHED = _Scheduler(RPM, BURST)


GLOBAL_SEM = threading.BoundedSemaphore(GLOBAL_INFLIGHT)   # process-local only
_breaker_lock = threading.Lock()
_breaker = {"streak": 0, "open_until": 0.0}


# ---- HTTP layer: retries off, redirects off, per-call socket watchdog ---------------------------
_conn_reg: dict = {}            # thread ident -> live urllib3 connection used by that worker
_conn_reg_lock = threading.Lock()


def _register_conn(conn):
    with _conn_reg_lock:
        _conn_reg[threading.get_ident()] = conn


class _SendCancelled(Exception):
    """Raised by the send gate BEFORE any request byte is written. Deliberately NOT an OSError /
    HTTPException, so urllib3 and requests neither retry nor wrap it."""


class _CallGate:
    """W2-C. One per call. Cancellation sources: watchdog (call deadline), Search deadline, Search
    abort or provider rate hold. check() runs before connect and after connect; commit() runs under the
    same lock immediately before the first request byte. After commit, cancellation can only shut the
    socket (outcome UNKNOWN)."""

    def __init__(self, send_deadline: float, abort: threading.Event | None, on_commit=None):
        self.lock = threading.Lock()
        self.send_deadline = send_deadline
        self.abort = abort
        self.cancelled = None
        self.committed = False
        self.where = None
        self.on_commit = on_commit

    def _why(self):
        if self.cancelled:
            return self.cancelled
        if self.abort is not None and self.abort.is_set():
            return "search_aborted"
        if SCHED.pause_until > time.monotonic():
            return "rate_hold"
        if time.monotonic() >= self.send_deadline:
            return "send_deadline"
        return None

    def check(self, where):
        with self.lock:
            if self.committed:
                return
            why = self._why()
            if why:
                self.cancelled, self.where = why, where
                raise _SendCancelled(where + ":" + why)

    def commit(self, where):
        with self.lock:
            if self.committed:
                return
            why = self._why()
            if why:
                self.cancelled, self.where = why, where
                raise _SendCancelled(where + ":" + why)
            self.committed, self.where = True, where
            _stat_add(calls_sent=1)
            if self.on_commit is not None:
                self.on_commit()

    def fire(self) -> bool:
        """Watchdog. Returns True if the call is committed (caller must shut the socket)."""
        with self.lock:
            if not self.committed:
                self.cancelled = self.cancelled or "watchdog_before_send"
                return False
            return True


_gate_reg: dict = {}            # thread ident -> _CallGate of the call that thread is running
_gate_reg_lock = threading.Lock()


def _gate():
    with _gate_reg_lock:
        return _gate_reg.get(threading.get_ident())


class _GateMixin:
    def connect(self):
        _register_conn(self)
        g = _gate()
        if g is not None:
            g.check("before_connect")              # nothing resolved, no socket
        self._wd_connecting = True
        try:
            super().connect()
        finally:
            self._wd_connecting = False
        if g is not None:
            try:
                g.check("after_connect")           # DNS/TCP done, no request byte yet
            except _SendCancelled:
                try:
                    self.close()
                finally:
                    raise

    def request(self, *a, **kw):
        _register_conn(self)
        self._wd_req_gated = False
        return super().request(*a, **kw)

    def send(self, data):
        if self.sock is None and self.auto_open:
            self.connect()                         # connect (and its gates) BEFORE the commit gate
        if not getattr(self, "_wd_connecting", False) and not getattr(self, "_wd_req_gated", True):
            g = _gate()
            if g is not None:
                try:
                    g.commit("first_request_byte")
                except _SendCancelled:
                    try:
                        self.close()
                    finally:
                        raise
            self._wd_req_gated = True
        return super().send(data)


class _WatchedHTTPConnection(_GateMixin, _u3conn.HTTPConnection):
    pass


class _WatchedHTTPSConnection(_GateMixin, _u3conn.HTTPSConnection):
    pass


class _WatchedHTTPPool(_u3pool.HTTPConnectionPool):
    ConnectionCls = _WatchedHTTPConnection


class _WatchedHTTPSPool(_u3pool.HTTPSConnectionPool):
    ConnectionCls = _WatchedHTTPSConnection


NO_RETRY = Retry(total=0, connect=0, read=0, redirect=0, status=0, other=0, raise_on_redirect=False,
                 raise_on_status=False)


class _Adapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        super().init_poolmanager(*a, **kw)
        self.poolmanager.pool_classes_by_scheme = {"http": _WatchedHTTPPool, "https": _WatchedHTTPSPool}

    def proxy_manager_for(self, proxy, **kw):
        m = super().proxy_manager_for(proxy, **kw)
        m.pool_classes_by_scheme = {"http": _WatchedHTTPPool, "https": _WatchedHTTPSPool}
        return m


_session = requests.Session()
_session.mount("https://", _Adapter(pool_connections=4, pool_maxsize=max(GLOBAL_INFLIGHT, 4), max_retries=NO_RETRY))
_session.mount("http://", _Adapter(pool_connections=2, pool_maxsize=max(GLOBAL_INFLIGHT, 4), max_retries=NO_RETRY))


def _kill_thread_conn(ident, fired: list, gate=None):
    fired.append(True)
    if gate is not None and not gate.fire():
        return                      # not committed: the gate refuses any later send (no socket needed)
    with _conn_reg_lock:
        conn = _conn_reg.get(ident)
    sock = getattr(conn, "sock", None) if conn is not None else None
    if sock is not None:
        try:
            socket.socket.shutdown(sock, socket.SHUT_RDWR)   # base-class call: wakes a blocked recv
        except OSError:
            pass


_RA_INT = re.compile(r"[0-9]{1,12}")


def parse_retry_after(v):
    """Returns (seconds | None, valid: bool). Only delay-seconds digits or an HTTP-date."""
    if v is None:
        return None, True
    v = v.strip()
    if _RA_INT.fullmatch(v):
        return float(int(v)), True
    if re.fullmatch(r"[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT", v):
        try:
            dt = parsedate_to_datetime(v)
            sec = dt.timestamp() - time.time()
            if math.isfinite(sec):
                return max(0.0, sec), True
        except (TypeError, ValueError, OverflowError):
            pass
    return None, False          # NaN / Inf / negative / exponent / garbage


def parse_usage(data):
    """Optional provider usage, independent of score validity. No price conversion."""
    if not isinstance(data, dict):
        return "malformed_body", None, None
    res = data                                   # J50: jev reply has no {"success","result"} envelope
    if "usage" not in res or res.get("usage") is None:
        return "missing", None, None
    u = res["usage"]
    if not isinstance(u, dict):
        return "malformed_usage", None, json.dumps({"type": type(u).__name__})
    raw = json.dumps({str(k)[:40]: (v if isinstance(v, (int, float, bool)) or v is None else "<%s>" % type(v).__name__)
                      for k, v in list(u.items())[:12]}, allow_nan=True)
    if "input_tokens" not in u:
        return "missing", None, raw
    it = u["input_tokens"]
    if isinstance(it, bool):
        return "bool", None, raw
    if not isinstance(it, int):
        return "non_integer", None, raw
    if it < 0:
        return "negative", None, raw
    return "ok", it, raw


def parse_score(data):
    res = data if isinstance(data, dict) else {}  # J50: jev reply is top-level {"model","answers","usage"}
    if res.get("model") != JEV_MODEL:              # J50: model must be exactly jev-1.13.0, else no_score
        return None
    ans = res.get("answers") or {}
    ans = (ans.get("rel") or {}) if isinstance(ans, dict) else {}
    score = ans.get("score") if isinstance(ans, dict) else None
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) \
            or not 0 <= score <= 3:
        return None
    return float(score)


class CallFail(Exception):
    """Carries what is known: sent ("no_proven"|"yes"|"unknown"), phase, http status, usage."""

    def __init__(self, code, *, sent, phase, http_status=None, usage=None, is429=False, err_type=None,
                 err_errno=None, retry_after_raw=None, pause_until_epoch=None):
        super().__init__(code)
        self.code, self.sent, self.phase, self.http_status = code, sent, phase, http_status
        self.usage = usage          # None = no response body parsed; else (status, tokens, raw)
        self.is429, self.err_type, self.err_errno = is429, err_type, err_errno
        self.retry_after_raw, self.pause_until_epoch = retry_after_raw, pause_until_epoch


class Receipt:
    def __init__(self, score, usage, http_status):
        self.score, self.usage, self.http_status = score, usage, http_status


def _exc_chain(exc):
    out, e, seen = [], exc, set()
    while e is not None and id(e) not in seen and len(out) < 8:
        seen.add(id(e))
        out.append(e)
        nxt = None
        if isinstance(e, requests.RequestException) and e.args and isinstance(e.args[0], BaseException):
            nxt = e.args[0]
        elif isinstance(e, _u3e.MaxRetryError):
            nxt = e.reason
        elif isinstance(e, _u3e.ProxyError):
            nxt = getattr(e, "original_error", None)
        e = nxt or e.__cause__ or e.__context__
    return out


def proven_not_sent(exc) -> tuple[bool, int | None]:
    """True only for a fresh TCP connect refused (direct, or to the proxy) with retries off."""
    chain = _exc_chain(exc)
    errno_seen = None
    for e in chain:
        if isinstance(e, OSError) and getattr(e, "errno", None):
            errno_seen = errno_seen or e.errno
    mre = next((e for e in chain if isinstance(e, _u3e.MaxRetryError)), None)
    if mre is None:
        return False, errno_seen
    reason = mre.reason
    if isinstance(reason, _u3e.ProxyError):
        reason = getattr(reason, "original_error", None)
    if type(reason) is not _u3e.NewConnectionError:      # exact: not NameResolutionError, not timeouts
        return False, errno_seen
    cause = reason.__cause__ or reason.__context__
    if isinstance(cause, ConnectionRefusedError) or getattr(cause, "errno", None) == _errno.ECONNREFUSED:
        return True, _errno.ECONNREFUSED
    return False, errno_seen


def _ra_meta(raw, valid):
    if raw is None:
        return None
    return raw.strip()[:40] if valid else "<invalid len=%d>" % len(raw)


def _call(model: str, token: str, question: str, memory: str, timeout: float, gate: _CallGate | None = None) -> Receipt:
    """One request, no resend (urllib3 retries off). Returns Receipt or raises CallFail."""
    url = TEST_URL if TEST_MODE else JEV_URL      # J50
    body = {"model": model, "state": state_text(question, memory), "questions": QUESTIONS}
    fired: list = []
    ident = threading.get_ident()
    t_call = time.monotonic()
    if gate is None:
        gate = _CallGate(t_call + timeout, None)
    gate.send_deadline = min(gate.send_deadline, t_call + timeout)
    with _gate_reg_lock:
        _gate_reg[ident] = gate
    wd = threading.Timer(max(0.01, timeout), _kill_thread_conn, args=(ident, fired, gate))
    wd.daemon = True
    wd.start()
    try:
        try:
            gate.check("before_post")
            r = _session.post(url, json=body, headers={"Authorization": "Bearer " + token},
                              timeout=(min(5.0, timeout), timeout), allow_redirects=False)
            raw = r.content
        except _SendCancelled as exc:
            # the gate refused before the first request byte: provably not sent (no errno heuristics)
            raise CallFail("cancelled_before_send", sent="no_proven", phase="pre_send_gate",
                           err_type=str(exc)[:60])
        except requests.RequestException as exc:
            ok, eno = proven_not_sent(exc)
            if ok and not fired:
                raise CallFail("net_conn_refused", sent="no_proven", phase="tcp_connect",
                               err_type=type(exc).__name__, err_errno=eno)
            raise CallFail(("watchdog_" if fired else "net_") + type(exc).__name__, sent="unknown",
                           phase="watchdog" if fired else "request_or_receipt", err_type=type(exc).__name__,
                           err_errno=eno)
    finally:
        wd.cancel()
        with _conn_reg_lock:
            _conn_reg.pop(ident, None)
        with _gate_reg_lock:
            _gate_reg.pop(ident, None)
    if fired:   # socket was shut down by the watchdog: whatever was parsed is truncated, not a receipt
        raise CallFail("watchdog_truncated", sent="unknown", phase="watchdog", http_status=None)
    status = r.status_code
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    usage = parse_usage(data) if data is not None else ("no_body" if not raw else "bad_json", None, None)
    if status == 429:
        ra_raw = r.headers.get("Retry-After")
        ra, valid = parse_retry_after(ra_raw)
        if not valid:
            _stat_add(retry_after_invalid=1)
        pause = SCHED.on_429(ra)                   # original Retry-After honoured in full (or bounded backoff)
        raise CallFail("http_429", sent="yes", phase="response", http_status=429, usage=usage, is429=True,
                       retry_after_raw=_ra_meta(ra_raw, valid), pause_until_epoch=time.time() + pause)
    if status != 200:
        raise CallFail("http_" + str(status), sent="yes", phase="response", http_status=status, usage=usage)
    score = parse_score(data)
    if score is None:
        raise CallFail("bad_json" if data is None else "no_score", sent="yes", phase="response", http_status=200,
                       usage=usage)
    return Receipt(score, usage, 200)


def _truncate_rrf(cands, n):
    return [(mid, score) for mid, score, _ in cands[:n]]


def apply(query: str, cands: list[tuple[int, float, str]], top_k: int) -> tuple[list[tuple[int, float]], dict]:
    """cands: immutable RRF-ordered [(mid, rrf_score, content)], len <= top_k.
    Returns ([(mid, score)], meta). meta is metadata only. Never blocks past the deadline."""
    t0 = time.monotonic()
    deadline = t0 + DEADLINE                 # absolute, fixed at entry
    cands = tuple(cands)
    n = min(RETURN_N, top_k) if RETURN_N is not None else top_k
    if MODE != "off":
        n = min(n, MAX_CAND)
    meta = {"mode": "off" if MODE == "off" else "fallback", "n_cand": len(cands), "returned": 0}

    def done(out, mode, reason=None, **kw):
        meta.update(kw)
        meta["mode"] = mode
        if reason:
            meta["reason"] = reason
        meta["returned"] = len(out)
        if MODE != "off" and cands:
            with _stats_lock:
                STATS["searches"] += 1
                if mode == "rerank":
                    STATS["reranked"] += 1
                else:
                    STATS["fallback"] += 1
                    STATS["fallback_reasons"][reason] = STATS["fallback_reasons"].get(reason, 0) + 1
        return out, meta

    if MODE == "off":
        return done(_truncate_rrf(cands, n), "off")
    if not cands:
        return done([], "empty")
    # Insertion point (2), NOT in this work order:
    #   "forget" marks = HARD EXCLUSION, applied here BEFORE reranking (C6). The fallback path
    #   must also use the post-exclusion list: a fallback never undoes a forget.
    pool = cands[:MAX_CAND]
    meta["input_length"] = {
        "bounded_pairs": sum(len(("Question: " + query + "\nMemory: " + c[2]).encode("utf-8"))
                             > JEV_STATE_BYTES for c in pool),
        "original_query_chars": len(query), "state_byte_ceiling": JEV_STATE_BYTES,
    }
    if len({c[0] for c in pool}) != len(pool):
        return done(_truncate_rrf(pool, n), "fallback", "dup_input_id")
    token = _get_token()
    if not token:
        return done(_truncate_rrf(pool, n), "fallback", _token_state)
    with _breaker_lock:
        if time.time() < _breaker["open_until"]:
            return done(_truncate_rrf(pool, n), "fallback", "breaker_open")
    if SCHED.pause_until >= deadline:
        return done(_truncate_rrf(pool, n), "fallback", "rate_hold", n_sent=0)
    search_id = uuid.uuid4().hex
    abort = threading.Event()
    slot_lock = threading.Lock()
    state = ["pending"] * len(pool)   # pending -> sending -> ok|failed ; pending -> cancelled
    scores: list[float | None] = [None] * len(pool)
    agg = {"fail": 0, "unknown": 0, "n429": 0, "sent": 0, "queue": 0.0, "first_err": None,
           "remote_fail": False, "queue_timeouts": 0}
    queued = [False] * len(pool)      # slot currently blocked on the process-wide semaphore
    next_i = [0]
    live = [0]
    all_done = threading.Event()

    def fail(code, remote=True):
        with slot_lock:
            agg["fail"] += 1
            if agg["first_err"] is None:
                agg["first_err"] = code
            if remote:
                agg["remote_fail"] = True
        abort.set()

    def sent():
        with slot_lock:
            agg["sent"] += 1

    def work(i):
        q0 = time.monotonic()
        got = False
        with slot_lock:
            queued[i] = True
        while not abort.is_set():
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            if GLOBAL_SEM.acquire(timeout=min(rem, 0.1)):
                got = True
                break
        with slot_lock:
            queued[i] = False
            if not got and not abort.is_set():
                agg["queue_timeouts"] += 1
        if not got:
            if not abort.is_set():
                fail("queue_timeout", remote=False)
            return
        gate = _CallGate(deadline, abort, on_commit=sent)
        call_id = (search_id, i)
        try:
            if not SCHED.acquire(deadline, abort):
                if not abort.is_set():
                    fail("rate_hold" if SCHED.pause_until > time.monotonic() else "rate_wait_past_deadline",
                         remote=False)
                return
            wait = time.monotonic() - q0
            _stat_add(queue_wait_s=wait)
            with slot_lock:
                agg["queue"] += wait
                if abort.is_set() or state[i] != "pending":
                    return
                state[i] = "sending"
            with _inflight_lock:
                _INFLIGHT.add(call_id)
            try:
                outcome = _call(JEV_MODEL, token, query, pool[i][2], CALL_TIMEOUT, gate)
            except CallFail as exc:
                outcome = exc
            except Exception as exc:
                # Keep unexpected client errors local to this search; never echo request data.
                outcome = CallFail("client_" + type(exc).__name__,
                                   sent="unknown" if gate.committed else "no_proven", phase="client")
            usage = outcome.usage
            if usage and usage[0] == "ok":
                _stat_add(input_tokens_reported=usage[1])
            else:
                _stat_add(usage_unavailable=1)
            if isinstance(outcome, Receipt):
                SCHED.on_ok()
                with slot_lock:
                    scores[i] = outcome.score
                    state[i] = "ok"
                _stat_add(calls_ok=1, valid_candidates=1)
                return
            unknown = outcome.sent == "unknown"
            _stat_add(calls_failed=1, calls_unknown=int(unknown), http_429=int(outcome.is429),
                      not_sent_proven=int(outcome.sent == "no_proven"),
                      cancelled_before_send=int(outcome.phase == "pre_send_gate"))
            with slot_lock:
                state[i] = "failed"
                agg["unknown"] += int(unknown)
                agg["n429"] += int(outcome.is429)
            fail(outcome.code, remote=outcome.sent != "no_proven")
        finally:
            with _inflight_lock:
                _INFLIGHT.discard(call_id)
            GLOBAL_SEM.release()

    def runner():
        try:
            while True:
                with slot_lock:
                    if abort.is_set() or next_i[0] >= len(pool):
                        return
                    i = next_i[0]
                    next_i[0] += 1
                work(i)
        finally:
            with slot_lock:
                live[0] -= 1
                if live[0] == 0:
                    all_done.set()

    nthreads = max(1, min(PER_SEARCH, len(pool)))
    live[0] = nthreads
    for _ in range(nthreads):
        threading.Thread(target=runner, name="rerank-" + search_id[:8], daemon=True).start()
    timed_out = False
    while not all_done.is_set():
        rem = deadline - time.monotonic()
        if rem <= 0:
            timed_out = True
            break
        all_done.wait(min(rem, 0.25))
        if abort.is_set():
            break
    if timed_out or abort.is_set():
        abort.set()
    # Never join: an already sent call finishes under its watchdog; no call is replayed.
    with slot_lock:
        unsent = [i for i in range(len(pool)) if state[i] == "pending"]
        for i in unsent:
            state[i] = "cancelled"
        inflight = sum(st == "sending" for st in state)
    elapsed = time.monotonic() - t0
    with slot_lock:
        ok_all = (not timed_out and not abort.is_set() and agg["fail"] == 0
                  and all(st == "ok" for st in state)
                  and all(isinstance(s, float) and math.isfinite(s) and 0 <= s <= 3 for s in scores))
        counts = {"search_id": search_id, "n_sent": agg["sent"], "n_valid": sum(st == "ok" for st in state),
                  "n_fail": agg["fail"], "n_unknown": agg["unknown"], "n_inflight_at_return": inflight,
                  "n_skipped": len(unsent), "n_429": agg["n429"],
                  "queue_wait_s": round(agg["queue"], 3), "rerank_sec": round(elapsed, 3)}
        first_err = agg["first_err"] or ("deadline" if timed_out else "invalid")
        # stable reason (test_limits queue_expiry): a deadline-class failure while any slot was blocked on
        # the global semaphore (still queued at return, or timed out there) is reported as queue_timeout,
        # whichever thread noticed the deadline first. Both facts are recorded.
        n_queued = sum(1 for q in queued if q)
        counts.update({"deadline_hit": timed_out, "n_queue_timeouts": agg["queue_timeouts"],
                       "n_queued_at_return": n_queued})
        if first_err in ("deadline", "queue_timeout") and (agg["queue_timeouts"] or n_queued):
            first_err = "queue_timeout"
        remote_fail = agg["remote_fail"]
    with _breaker_lock:
        if ok_all:
            _breaker["streak"] = 0
        elif remote_fail or timed_out:
            _breaker["streak"] += 1
            if _breaker["streak"] >= BREAKER_FAILS:
                _breaker["open_until"] = time.time() + BREAKER_COOLDOWN
                _breaker["streak"] = 0
                with _stats_lock:
                    STATS["breaker_open_until"] = _breaker["open_until"]
    if not ok_all:
        return done(_truncate_rrf(pool, n), "fallback", first_err, **counts)
    order = sorted(range(len(pool)), key=lambda i: (-scores[i], i))   # desc, ties keep RRF order
    # Insertion point (2), NOT in this work order:
    #   "void/superseded" marks = reorder + annotate here, AFTER rerank sort, BEFORE truncation (C6).
    out = [(pool[i][0], scores[i]) for i in order[:n]]
    return done(out, "rerank", None, **counts)


def _exit_grace():
    """Give already-sent calls a bounded chance to finish on normal interpreter exit."""
    end = time.monotonic() + EXIT_GRACE
    while time.monotonic() < end:
        with _inflight_lock:
            if not _INFLIGHT:
                return
        time.sleep(0.05)


if MODE != "off":
    atexit.register(_exit_grace)
