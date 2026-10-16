"""TypeSafe JEV rerank adapter for shell/server.py, with durable accounting.

Revision 3 (review WO123_latest_free_review_chenchen.json, section WO2_v2), on top of revision 2:
- W2-A: a Search is "rerank" only if EVERY attempt's settlement is durable: finalize() returned True,
  or (after it raised LedgerError / returned False) verify_settlement() independently reads back the
  same identity + same receipt. Otherwise: whole Search falls back, the row keeps its reservation
  (stays SENDING, charge == est -> orphan -> UNKNOWN at next start), the process stops sending
  (_LOCAL_STOP), a LEDGER_WRITE_FAILED stop is written if the ledger accepts it, and a metadata-only
  recoverable record is fsync-appended to <ledger>.anomalies.jsonl (reconciled at next start).
- W2-B: startup refuses a ledger whose financial invariants fail (validate -> ledger_audit_failed).
- W2-C: per-call send gate. Checked before connect (before DNS), after connect, and atomically at
  the first byte of the request (send()). A call whose Search deadline / call deadline passed, whose
  Search was aborted, whose watchdog fired, or that is under a hard stop is cancelled BEFORE any
  request byte is written -> RELEASED_NOT_SENT_PROVEN (phase pre_send_gate). The watchdog no longer
  needs a socket: if no socket exists it marks the gate cancelled and the later send is refused.
  RETRACTED (rev2 claim): "worker <= deadline + CALL_TIMEOUT". getaddrinfo cannot be interrupted, so
  a worker blocked in DNS can outlive that bound. ACTUAL guarantees: apply() returns at the Search
  deadline; no request byte is written after min(Search deadline, call start + CALL_TIMEOUT) or after
  abort/hard stop; once bytes are committed, the watchdog shuts the socket at call start + CALL_TIMEOUT.
- W2-D: route health in the ledger (schema 3): 401/403 -> AUTH_REJECTED stop on first; other 4xx incl.
  429 -> consecutive counter in observation (ledger commit) order, 3 -> HTTP_4XX_STREAK stop; a valid
  200 (score and usage valid) resets it; every 429 honours the original Retry-After (persisted
  pause_until, loaded at startup, enforced by mark_sending); a 429 is never resent. Only
  BUDGET_INSUFFICIENT lets already-reserved slots send; UNKNOWN / auth / 4xx stops block every send.

Request/parse logic copied from T/round2/rerank_common.py (frozen 2026-10-02): same endpoint,
same body {"model", "state", "questions"}, same "Question: ...\nMemory: ..." state text, same
0-3 score scale and validity rule. This adapter uses JEV only. The
interface (ACTIVE, MODE, RETURN_N, apply(), stats_snapshot()) is kept.

Revision 2 (reviews wo2_review.md + WO2_source_review_addendum.md, 2026-10-03):
- Paid mode needs an explicit authorisation: AML_RERANK_LEDGER (an existing ledger created by
  wo2_budget_admin.py init), AML_RERANK_BUDGET_ID (must match the ledger), AML_RERANK_RUN_ID
  (unique per run) and AML_RERANK_BUDGET_USD (process ceiling). No default 5 USD. Missing,
  empty, corrupt, moved/copied, foreign-route ledgers -> the process refuses to start.
  All numeric config is strict decimal text, finite, typed and range-checked (no nan/inf/1e3).
- Money: integer nano-USD in a transactional SQLite ledger (rerank_ledger.py), one row per attempt
  with identity, reservation, send state, raw usage, final charge, adjustments trail.
- Single instance: a paid process holds an exclusive flock for its whole lifetime
  (paid: fixed T/run/rerank_paid.instance.lock; test mode: <ledger>.instance.lock). A second
  instance refuses to start. ONLY covers processes that import this module (see report).
- UNKNOWN (sent or maybe sent, no billing receipt) persistently stops new scheduling on first
  occurrence (stops table; checked in the same transaction that marks an attempt SENDING).
  Restart does not clear it; only the audited admin path does.
- Billing receipt parsing is separate from score validation. HTTP 200 with a bad score but a
  valid usage is SETTLED at the real cost (no refund) and only drives fallback/breaker.
  usage missing / negative / bool / non-integer / malformed -> USAGE_INVALID: reservation kept,
  persistent stop. Actual > reservation -> SETTLED_OVER at the real cost (not clamped) +
  persistent stop. Settlement is exactly-once per attempt id.
- NOT_SENT_PROVEN only for: a fresh TCP connect refused (urllib3 NewConnectionError exact type,
  cause ECONNREFUSED; directly or as the original error of "unable to connect to proxy"), with
  urllib3 retries disabled (Retry(total=0)). Everything else after the HTTP layer saw the request
  is UNKNOWN. Slots never handed to HTTP (SENDING never committed) are RELEASED_NOT_SENT.
  Definite 4xx (incl. 429) without any usage in the body -> RELEASED_HTTP_4XX (money), plus the
  rev3 W2-D route policy (auth stop / 3-in-a-row stop / Retry-After pause).
- Deadline: one absolute deadline fixed at apply() entry covers token load, ledger/lock waits
  (sqlite busy timeout = remaining), semaphore, rate scheduler and the decision to send. apply()
  never joins workers; it returns at the deadline. Each HTTP call has a watchdog that shuts the
  socket down at CALL_TIMEOUT, so header/body trickle cannot extend a COMMITTED call (a watchdog-
  truncated response is UNKNOWN). A call already committed at the Search deadline keeps running until
  call start + CALL_TIMEOUT, is tracked in _INFLIGHT and settles its receipt in the background
  (or stays SENDING -> orphan -> UNKNOWN if the process dies). [rev3: see W2-C above for what is and
  is not bounded; DNS time is not.]
- Retry-After: digits (or HTTP-date) only; NaN/Inf/negative/garbage -> treated as absent (bounded
  backoff, exponent capped). A value above AML_RERANK_RETRY_AFTER_MAX is honoured in full
  (scheduler holds until then, no silent 120 s resume); the current Search falls back.
  rev3: the pause is also persisted in the ledger and survives restarts.
- Test URL: parsed and checked field by field (http, hostname exactly 127.0.0.1, explicit valid
  port, no userinfo/query/fragment, path exactly /run); redirects disabled. Test mode refuses a
  token path inside T/secrets (checked by path, never read) and only accepts stub credentials.
- Logs / health carry metadata only. Never query/content/token.

Contract kept from revision 1: all-or-nothing (any failure -> whole Search falls back to the RRF
order truncated to min(N, top_k) with RRF scores, never mixed); no automatic resend of a paid
request; PER_SEARCH (2) / GLOBAL_INFLIGHT (16) / RPM (240, burst 4) are process-local.
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
from aml_paths import STATE_DIR, SECRETS_DIR, PAID_INSTANCE_LOCK
from urllib.parse import urlsplit

import requests
import urllib3.connection as _u3conn
import urllib3.connectionpool as _u3pool
import urllib3.exceptions as _u3e
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rerank_ledger_jev as L  # noqa: E402  (J50 ruling F1-a: this dir's copy of shell/rerank_ledger.py + jev price)

# ---- copied from T/round2/rerank_common.py (frozen) -------------------------------------------
CF_ACCOUNT = os.environ.get("AML_CF_ACCOUNT_ID", "")
CF_URL = "https://api.cloudflare.com/client/v4/accounts/" + CF_ACCOUNT + "/ai/run/@cf/cloudflare/{model}"
CF_TOKEN_PATH = SECRETS_DIR / ".cloudflare_token"
INSTRUCTIONS = "How useful is this memory for answering the question?"
CRITERIA = ["Irrelevant", "Loosely related", "Partially answers", "Directly answers"]
QUESTIONS = {"rel": {"type": "score", "instructions": INSTRUCTIONS, "criteria": CRITERIA}}
MODELS = {   # price per M input tokens (rerank_common) == nano-USD per token, exact integers
    "clef": {"name": "clef", "price_per_m": "0.24", "nano_per_token": 240},
    "clef-flash": {"name": "clef-flash", "price_per_m": "0.09", "nano_per_token": 90},
    "jev": {"name": "jev-1.13.0", "price_per_m": "0.042", "nano_per_token": 42},
}
# J50 (from T/round2/rerank_common.py JEV_URL / JEV_MODEL; key path per J50 v1.2 ruling 8: AML-only key file)
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"
JEV_KEY_PATH = SECRETS_DIR / ".typesafe_key_aml"
JEV_PROD_KEY_PATH = SECRETS_DIR / ".typesafe_key"  # shared production key is refused


def state_text(question: str, memory: str) -> str:
    return "Question: " + question + "\nMemory: " + memory
# ------------------------------------------------------------------------------------------------

ROUTE_PAID = "typesafe:systemone"                 # J50: jev route (cloudflare route not used by this copy)
ROUTE_TEST = "test:loopback"
TEST_PATH = "/run"
TOK_OVERHEAD = 512   # reservation estimate = utf8 bytes of state text + overhead (>= tokens, see report)


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
    LEDGER_WAIT = float(env_dec("AML_RERANK_LEDGER_WAIT_SEC", "5", "0", "60"))
    BUDGET_USD = env_dec("AML_RERANK_BUDGET_USD", None, "0", "20")        # REQUIRED, no default
    BUDGET_NANO = L.usd_to_nano(str(BUDGET_USD))
    if os.environ.get("AML_RERANK_BUDGET_PATH"):
        _cfg_fail("AML_RERANK_BUDGET_PATH is retired (rev1 auto-created ledgers); use AML_RERANK_LEDGER")
    _lp = os.environ.get("AML_RERANK_LEDGER", "").strip()
    if not _lp:
        _cfg_fail("AML_RERANK_LEDGER is required (create it with wo2_budget_admin.py init)")
    LEDGER_PATH = Path(_lp)
    BUDGET_ID = os.environ.get("AML_RERANK_BUDGET_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", BUDGET_ID):
        _cfg_fail("AML_RERANK_BUDGET_ID is required")
    RUN_ID = os.environ.get("AML_RERANK_RUN_ID", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", RUN_ID):
        _cfg_fail("AML_RERANK_RUN_ID is required ([A-Za-z0-9._:-]{1,80})")
    if os.environ.get("AML_CF_TOKEN_PATH"):
        _cfg_fail("AML_CF_TOKEN_PATH is not used by the jev copy; use AML_JEV_KEY_PATH")
    TOKEN_PATH = Path(os.environ.get("AML_JEV_KEY_PATH") or JEV_KEY_PATH)
    try:
        _tp = TOKEN_PATH.resolve()
    except OSError:
        _tp = TOKEN_PATH
    _real = {JEV_KEY_PATH.resolve(), JEV_PROD_KEY_PATH.resolve(), CF_TOKEN_PATH.resolve()}
    if _tp == JEV_PROD_KEY_PATH.resolve():
        _cfg_fail("the production Jev key is refused (J50 uses the AML key file only; no fallback)")
    if TEST_MODE:
        if _tp in _real or SECRETS_DIR.resolve() in _tp.parents or JEV_KEY_PATH.resolve().parent in _tp.parents \
                or _tp == JEV_KEY_PATH.resolve().parent:
            _cfg_fail("test mode refuses both real Jev keys, their directory and the secrets directory (path only, not read); "
                      "point AML_JEV_KEY_PATH at a stub key file")
    PRICE_NANO = MODELS[MODE]["nano_per_token"]
else:
    PER_SEARCH = GLOBAL_INFLIGHT = BURST = BREAKER_FAILS = 1
    RPM = CALL_TIMEOUT = DEADLINE = RETRY_AFTER_MAX = BREAKER_COOLDOWN = LEDGER_WAIT = 1.0
    BUDGET_NANO = 0
    LEDGER_PATH = TOKEN_PATH = None
    BUDGET_ID = RUN_ID = ""
    PRICE_NANO = 0

BACKOFF_BASE, BACKOFF_MAX, BACKOFF_EXP_CAP = 2.0, 60.0, 10      # 429 without (valid) Retry-After
EXIT_GRACE = 5.0


# ---- single instance lock + ledger validation (paid or test mode only) --------------------------
_instance_lock_fd = None
LEDGER = None


def _acquire_instance_lock():
    global _instance_lock_fd
    lp = Path(str(LEDGER_PATH.resolve()) + ".instance.lock") if TEST_MODE else PAID_INSTANCE_LOCK
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        _cfg_fail("another rerank instance holds the instance lock; refusing to start")
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": os.getpid(), "run_id": RUN_ID, "ts": L.now_iso()}).encode())
    _instance_lock_fd = fd           # held (never closed) for the life of the process
    return lp


INSTANCE_LOCK_PATH = None
if MODE != "off":
    INSTANCE_LOCK_PATH = _acquire_instance_lock()
    LEDGER = L.Ledger(LEDGER_PATH)
    try:
        LEDGER.validate(budget_id=BUDGET_ID, route=ROUTE_TEST if TEST_MODE else ROUTE_PAID, timeout=LEDGER_WAIT)
        LEDGER.register_run(run_id=RUN_ID, mode=MODE, model=MODELS[MODE]["name"],
                            route=ROUTE_TEST if TEST_MODE else ROUTE_PAID, env_cap_nano=BUDGET_NANO,
                            test_mode=TEST_MODE, timeout=LEDGER_WAIT)
        ORPHANS = LEDGER.recover_orphans(RUN_ID, actor="startup:" + RUN_ID, timeout=LEDGER_WAIT)
    except L.LedgerError as _e:
        _cfg_fail("ledger refused: " + _e.code)
    ANOMALY_PATH = Path(str(LEDGER_PATH.resolve()) + ".anomalies.jsonl")
else:
    ORPHANS = None
    ANOMALY_PATH = None
_anomaly_lock = threading.Lock()


def _read_anomalies():
    """Returns (entries, n_malformed). Metadata-only JSON lines."""
    if ANOMALY_PATH is None or not ANOMALY_PATH.exists():
        return [], 0
    out, bad = [], 0
    for line in ANOMALY_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                raise ValueError
            out.append(e)
        except ValueError:
            bad += 1
    return out, bad


def _journal_anomaly(entry: dict) -> bool:
    """fsync-appended, metadata only (ids, statuses, integers). Never query/content/token."""
    if ANOMALY_PATH is None:
        return False
    line = (json.dumps(entry, sort_keys=True, default=str) + "\n").encode()
    try:
        with _anomaly_lock:
            fd = os.open(str(ANOMALY_PATH), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
        return True
    except OSError:
        return False


RECONCILED = 0
if MODE != "off":
    _ents, _bad = _read_anomalies()
    if _bad:
        _cfg_fail("anomaly journal has %d unreadable line(s); inspect %s before starting" % (_bad, ANOMALY_PATH.name))
    if _ents:
        try:
            RECONCILED = LEDGER.reconcile_anomalies(_ents, RUN_ID, timeout=LEDGER_WAIT)
        except L.LedgerError as _e:
            _cfg_fail("ledger refused (anomaly reconcile): " + _e.code)


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
STATS = {"searches": 0, "reranked": 0, "fallback": 0, "fallback_reasons": {}, "calls_sent": 0,
         "calls_ok": 0, "calls_unknown": 0, "http_429": 0, "valid_candidates": 0,
         "queue_wait_s": 0.0, "settled_nano": 0, "usage_invalid": 0, "settled_over": 0,
         "not_sent_proven": 0, "released_4xx": 0, "dup_settle_refused": 0, "ledger_errors": 0,
         "retry_after_invalid": 0, "breaker_open_until": 0.0, "settle_failed": 0, "settle_verified_existing": 0,
         "cancelled_before_send": 0, "anomaly_journal_failed": 0}
_INFLIGHT: dict = {}        # attempt_id -> {"search": sid, "since": monotonic}; workers past their Search
_inflight_lock = threading.Lock()
_LOCAL_STOP = threading.Event()   # hard stop (UNKNOWN-class): no new Search, no new send. Ledger is authoritative
_LOCAL_BUDGET_STOP = threading.Event()   # budget insufficient: no new Search; reserved slots may still send


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
    s["transient_recovery_enabled"] = os.environ.get("AML_RERANK_TRANSIENT_RECOVERY") == "1"
    s["queue_wait_s"] = round(s["queue_wait_s"], 3)
    s["est_usd_settled"] = L.nano_to_usd(s.pop("settled_nano"))
    s["breaker_open"] = time.time() < s.pop("breaker_open_until")
    with _inflight_lock:
        s["inflight_after_search"] = len(_INFLIGHT)
    s["rate_hold"] = SCHED.hold_info()
    if LEDGER is None:
        s["budget"] = {"stopped": False, "ledger": None}
        return s
    try:
        b = LEDGER.snapshot(env_cap_nano=BUDGET_NANO, timeout=1.0)
        s["budget"] = {k: b[k] for k in ("cap_usd", "committed_usd", "available_usd", "stopped", "open_stops",
                                         "unresolved_unknown", "open_attempts")}
        s["budget"]["stopped"] = b["stopped"] or _LOCAL_STOP.is_set() or _LOCAL_BUDGET_STOP.is_set()
        rs = LEDGER.route_state(timeout=1.0)
        s["budget"]["route_consec_4xx"] = rs["consec_4xx"]
        s["budget"]["route_pause_s"] = round(rs["pause_remaining_s"], 1)
    except L.LedgerError as exc:
        s["budget"] = {"error": exc.code, "stopped": True}
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

    # rev3.1 (round-4 T1): fixed label priority. While a pause is active, a label is only replaced by
    # one of strictly higher priority, so a later sync_pause() can never overwrite retry_after_over_max
    # with persisted_retry_after (or with None) because of thread timing.
    REASON_PRIO = {None: 0, "persisted_retry_after": 1, "retry_after_over_max": 2}

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

    def sync_pause(self, pause_until_epoch, reason="persisted_retry_after"):
        """Adopt a persisted (wall-clock) pause from the ledger; never shortens a pause."""
        if pause_until_epoch is None:
            return 0.0
        rem = float(pause_until_epoch) - time.time()
        if rem <= 0:
            return 0.0
        with self.cv:
            p = time.monotonic() + rem + 0.005
            if p > self.pause_until:
                self._set_reason(reason)
                self.pause_until = p
            self.tokens = 0.0
            self.cv.notify_all()
        return rem

    def on_ok(self):
        with self.cv:
            self.n429_streak = 0
            self.rate = min(self.cfg_rate, self.rate + 1.0 / 60.0)

    def hold_info(self):
        with self.cv:
            rem = self.pause_until - time.monotonic()
            return {"paused_s": round(rem, 1) if rem > 0 else 0, "reason": self.hold_reason if rem > 0 else None}


SCHED = _Scheduler(RPM, BURST)


def _sync_route_pause():
    if LEDGER is None:
        return None
    try:
        rs = LEDGER.route_state(timeout=max(0.05, min(LEDGER_WAIT, 1.0)))
    except L.LedgerError:
        return None
    SCHED.sync_pause(rs["pause_until_epoch"])
    return rs


ROUTE_AT_START = _sync_route_pause() if MODE != "off" else None
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
    abort, process hard stop. check() runs before connect and after connect; commit() runs under the
    same lock immediately before the first request byte. After commit, cancellation can only shut the
    socket (outcome UNKNOWN)."""

    def __init__(self, send_deadline: float, abort: threading.Event | None):
        self.lock = threading.Lock()
        self.send_deadline = send_deadline
        self.abort = abort
        self.cancelled = None
        self.committed = False
        self.where = None

    def _why(self):
        if self.cancelled:
            return self.cancelled
        if self.abort is not None and self.abort.is_set():
            return "search_aborted"
        if _LOCAL_STOP.is_set():
            return "hard_stop"
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
    """Billing receipt, independent of the score. Returns (status, tokens|None, raw_meta)."""
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


def decide(est_nano: int, price_nano: int, outcome) -> tuple[str, int, str | None]:
    """Pure settlement rule. outcome: Receipt | CallFail. Returns (status, charge_nano, stop_kind)."""
    usage = outcome.usage
    if isinstance(outcome, CallFail) and outcome.sent == "no_proven":
        return "RELEASED_NOT_SENT_PROVEN", 0, None
    if usage is None:                                     # sent / maybe sent, no response at all
        return "UNKNOWN", est_nano, "UNKNOWN"
    ustat, tokens, _raw = usage
    if ustat == "ok":
        charge = int(tokens) * int(price_nano)
        if charge > est_nano:
            return "SETTLED_OVER", charge, "OVER_RESERVATION"     # real cost, never clamped
        return "SETTLED", charge, None
    hs = outcome.http_status
    if hs is not None and 400 <= hs < 500 and ustat in ("missing", "no_body", "bad_json", "malformed_result",
                                                        "malformed_body"):
        return "RELEASED_HTTP_4XX", 0, None       # definite client-side rejection, no usage reported
    if hs == 200:
        return "USAGE_INVALID", est_nano, "USAGE_INVALID"
    return "UNKNOWN", est_nano, "UNKNOWN"         # 5xx / 3xx / 1xx / malformed usage on non-200


def route_observation(outcome, st):
    """W2-D classification of one observed response (None = does not touch the 4xx counter)."""
    hs = outcome.http_status
    if hs in (401, 403):
        kind = "auth"
    elif hs is not None and 400 <= hs < 500:
        kind = "4xx"                                   # includes 429
    elif isinstance(outcome, Receipt) and hs == 200 and st in ("SETTLED", "SETTLED_OVER"):
        kind = "ok200"                                 # score valid (Receipt) AND usage valid (settled)
    else:
        return None
    return {"kind": kind, "http_status": hs, "retry_after_raw": getattr(outcome, "retry_after_raw", None),
            "pause_until_epoch": getattr(outcome, "pause_until_epoch", None)}


def _finalize_durable(aid, est, st, charge, fields, stop, obs, why_ctx):
    """W2-A. True only if this settlement is durable in the ledger: finalize() == True, or an
    independent read-back finds the same identity + same receipt. Otherwise keep the reservation,
    stop sending, write LEDGER_WRITE_FAILED if possible and journal a recoverable record."""
    res, why = {}, None
    try:
        ok = LEDGER.finalize(aid, RUN_ID, status=st, charge_nano=charge, fields=fields, stop_kind=stop,
                             route_obs=obs, result=res, timeout=max(LEDGER_WAIT, 1.0))
    except L.LedgerError as exc:
        ok, why = None, "finalize_error:" + exc.code
    if ok is True:
        if [k for k in res.get("stops_added", []) if k not in L.SOFT_STOPS]:
            _LOCAL_STOP.set()
        return True
    if ok is False:
        _stat_add(dup_settle_refused=1)
        why = "finalize_returned_false"
    if LEDGER.verify_settlement(aid, RUN_ID, status=st, charge_nano=charge, usage_tokens=fields.get("usage_tokens"),
                                usage_status=fields.get("usage_status"), http_status=fields.get("http_status")):
        _stat_add(settle_verified_existing=1)
        return True
    _LOCAL_STOP.set()
    _stat_add(settle_failed=1, ledger_errors=1)
    entry = {"ts": L.now_iso(), "run_id": RUN_ID, "attempt_id": aid, "why": why, "ctx": why_ctx,
             "intended_status": st, "intended_charge_nano": int(charge), "est_nano": int(est),
             "usage_status": fields.get("usage_status"), "usage_tokens": fields.get("usage_tokens"),
             "http_status": fields.get("http_status"), "sent": fields.get("sent"), "err": fields.get("err"),
             "route_obs": (obs or {}).get("kind")}
    if not _journal_anomaly(entry):
        _stat_add(anomaly_journal_failed=1)
    try:
        LEDGER.add_stop("LEDGER_WRITE_FAILED", RUN_ID, (why or "")[:80], timeout=max(LEDGER_WAIT, 1.0), attempt_id=aid)
    except L.LedgerError:
        pass                     # row stays SENDING -> UNKNOWN + stop at next start; journal reconciled too
    return False


def _settle(aid, est, outcome):
    """Exactly-once ledger settlement for one sent attempt. Returns (status, stop_kind, durable)."""
    st, charge, stop = decide(est, PRICE_NANO, outcome)
    u = outcome.usage or (None, None, None)
    fields = {"sent": {"no_proven": "no", "yes": "yes", "unknown": "unknown"}.get(getattr(outcome, "sent", "yes"), "yes"),
              "phase": getattr(outcome, "phase", "response"), "err": getattr(outcome, "code", None),
              "err_errno": getattr(outcome, "err_errno", None), "http_status": outcome.http_status,
              "usage_status": u[0], "usage_raw": u[2], "usage_tokens": u[1],
              "note": getattr(outcome, "err_type", None) if getattr(outcome, "phase", None) == "pre_send_gate" else None}
    obs = route_observation(outcome, st)
    if stop or (obs and obs["kind"] == "auth"):
        _LOCAL_STOP.set()                     # stop new scheduling in this process immediately
    ok = _finalize_durable(aid, est, st, charge, fields, stop, obs, "settle")
    if ok and st in ("SETTLED", "SETTLED_OVER"):
        _stat_add(settled_nano=charge)
    if getattr(outcome, "phase", None) == "pre_send_gate":
        _stat_add(cancelled_before_send=1)
    if st == "USAGE_INVALID":
        _stat_add(usage_invalid=1)
    if st == "SETTLED_OVER":
        _stat_add(settled_over=1)
    if st == "RELEASED_NOT_SENT_PROVEN":
        _stat_add(not_sent_proven=1)
    if st == "RELEASED_HTTP_4XX":
        _stat_add(released_4xx=1)
    return st, stop, ok


# ---- the hook -----------------------------------------------------------------------------------
def _truncate_rrf(cands, n):
    return [(mid, s) for mid, s, _ in cands[:n]]


_recovery_lock = threading.Lock()


def recover_serving_transient(query, cands):
    """Serving-only audited recovery. Uncertain calls are retained, never replayed."""
    if os.environ.get("AML_RERANK_TRANSIENT_RECOVERY") != "1" or MODE == "off":
        return {"recovered": False, "reason": "disabled"}
    from serving_recovery import recover
    if not _recovery_lock.acquire(timeout=0.1):
        return {"recovered": False, "reason": "recovery_busy"}
    try:
        # A changed local flag alone never authorizes sends; reserve/mark_sending
        # still consult the durable ledger on every new request.
        need = sum((len(state_text(query, c[2]).encode("utf-8")) + TOK_OVERHEAD) * PRICE_NANO
                   for c in cands[:MAX_CAND])
        cooldown = 0.05 if TEST_MODE else 10.0
        result = recover(LEDGER, actor=RUN_ID, env_cap_nano=BUDGET_NANO,
                         next_reserve_nano=need, cooldown=cooldown)
        if result.get("reason") == "cooldown" and result["wait_s"] <= cooldown:
            time.sleep(result["wait_s"] + 0.01)
            result = recover(LEDGER, actor=RUN_ID, env_cap_nano=BUDGET_NANO,
                             next_reserve_nano=need, cooldown=cooldown)
        if result.get("recovered"):
            _LOCAL_STOP.clear()
        return result
    except Exception as exc:
        return {"recovered": False, "reason": "recovery_error:" + type(exc).__name__}
    finally:
        _recovery_lock.release()


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
    if len({c[0] for c in pool}) != len(pool):
        return done(_truncate_rrf(pool, n), "fallback", "dup_input_id")
    token = _get_token()
    if not token:
        return done(_truncate_rrf(pool, n), "fallback", _token_state)
    with _breaker_lock:
        if time.time() < _breaker["open_until"]:
            return done(_truncate_rrf(pool, n), "fallback", "breaker_open")
    if _LOCAL_STOP.is_set() or _LOCAL_BUDGET_STOP.is_set():
        return done(_truncate_rrf(pool, n), "fallback", "budget_stopped", n_sent=0)
    if SCHED.hold_info()["reason"] and SCHED.pause_until >= deadline:
        return done(_truncate_rrf(pool, n), "fallback", "rate_hold", n_sent=0)

    search_id = uuid.uuid4().hex
    est = [(len(state_text(query, c[2]).encode("utf-8")) + TOK_OVERHEAD) * PRICE_NANO for c in pool]
    rem = deadline - time.monotonic()
    if rem <= 0:
        return done(_truncate_rrf(pool, n), "fallback", "deadline", n_sent=0)
    try:
        res, ids = LEDGER.reserve_search(search_id=search_id, run_id=RUN_ID, model=MODELS[MODE]["name"], ests=est,
                                         env_cap_nano=BUDGET_NANO, timeout=min(LEDGER_WAIT, rem))
    except L.LedgerError as exc:
        _stat_add(ledger_errors=1)
        return done(_truncate_rrf(pool, n), "fallback", "ledger_" + exc.code.replace("ledger_", ""), n_sent=0)
    if res != "ok":
        if res == "insufficient" or (ids and set(ids) == {"BUDGET_INSUFFICIENT"}):
            _LOCAL_BUDGET_STOP.set()
        else:
            _LOCAL_STOP.set()
        return done(_truncate_rrf(pool, n), "fallback", "budget_" + res, n_sent=0)

    abort = threading.Event()
    slot_lock = threading.Lock()
    state = ["pending"] * len(pool)   # pending -> claimed -> sending -> ok|failed ; pending -> cancelled
    scores: list[float | None] = [None] * len(pool)
    agg = {"fail": 0, "unknown": 0, "n429": 0, "sent": 0, "queue": 0.0, "first_err": None,
           "remote_fail": False, "released_self": 0, "queue_timeouts": 0, "settle_failed": 0}
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

    def release_self(i, reason):
        try:
            LEDGER.release_unsent([ids[i]], RUN_ID, reason, timeout=max(LEDGER_WAIT, 1.0))
        except L.LedgerError:
            _stat_add(ledger_errors=1)       # stays RESERVED -> released as orphan on next start
        with slot_lock:
            agg["released_self"] += 1

    def work(i):
        q0 = time.monotonic()
        rem = deadline - q0
        with slot_lock:
            queued[i] = True
        got = rem > 0 and GLOBAL_SEM.acquire(timeout=rem)
        with slot_lock:
            queued[i] = False
            if not got:
                agg["queue_timeouts"] += 1
        if not got:
            fail("queue_timeout", remote=False)
            return
        try:
            ms = None
            for _round in range(8):                     # re-entered only when the ledger reports a persisted pause
                if not SCHED.acquire(deadline, abort):
                    if not abort.is_set():
                        fail("rate_hold" if SCHED.hold_info()["reason"] else "rate_wait_past_deadline")
                    return
                with slot_lock:
                    if _round == 0:
                        agg["queue"] += time.monotonic() - q0
                    if abort.is_set() or state[i] != "pending":
                        return
                    if _LOCAL_STOP.is_set():
                        agg["first_err"] = agg["first_err"] or "budget_stopped"
                        agg["fail"] += 1
                        abort.set()
                        return
                    state[i] = "claimed"
                rem = deadline - time.monotonic()
                if rem <= 0.05:
                    release_self(i, "deadline_before_send")
                    with slot_lock:
                        state[i] = "cancelled"
                    fail("deadline", remote=False)
                    return
                if abort.is_set():
                    release_self(i, "abort_before_mark_sending")
                    with slot_lock:
                        state[i] = "cancelled"
                    return
                try:
                    ms = LEDGER.mark_sending(ids[i], RUN_ID, timeout=min(LEDGER_WAIT, rem))
                except L.LedgerError as exc:
                    ms = "ledger_" + exc.code
                if ms != "paused":
                    break
                _sync_route_pause()                     # W2-D: ledger pause is authoritative
                with slot_lock:
                    if abort.is_set():
                        state[i] = "cancelled"
                        cancel_now = True
                    else:
                        state[i] = "pending"            # back in the queue; apply() releases it if it ends
                        cancel_now = False
                if cancel_now:
                    release_self(i, "route_paused")
                    return
            if ms == "paused":
                release_self(i, "route_paused")
                with slot_lock:
                    state[i] = "cancelled"
                fail("rate_hold", remote=False)
                return
            if ms != "ok":
                release_self(i, "mark_sending_" + ms)
                with slot_lock:
                    state[i] = "cancelled"
                if ms == "stopped":
                    _LOCAL_STOP.set()
                fail("budget_stopped" if ms == "stopped" else "ledger_error", remote=False)
                return
            if abort.is_set() or _LOCAL_STOP.is_set():
                # SENDING committed but the request was never handed to HTTP: truthful release
                _finalize_durable(ids[i], est[i], "RELEASED_NOT_SENT", 0,
                                  {"sent": "no", "phase": "before_http", "note": "abort_after_mark_sending"},
                                  None, None, "abort_after_mark_sending")
                with slot_lock:
                    state[i] = "cancelled"
                    agg["released_self"] += 1
                if not abort.is_set():
                    fail("budget_stopped", remote=False)
                return
            with slot_lock:
                state[i] = "sending"
                agg["sent"] += 1
            with _inflight_lock:
                _INFLIGHT[ids[i]] = {"search": search_id, "since": time.monotonic()}
            try:
                # rev3 (W2-C): the send gate refuses the first request byte after min(Search deadline,
                # call start + CALL_TIMEOUT), after abort, or under a hard stop. A call committed before
                # that keeps running under its watchdog and settles its receipt in the background.
                # RETRACTED: "worker lifetime <= deadline + CALL_TIMEOUT" -- DNS (getaddrinfo) is not
                # interruptible, so a worker can stay blocked longer; it just cannot send afterwards.
                gate = _CallGate(deadline, abort)
                try:
                    outcome = _call(MODELS[MODE]["name"], token, query, pool[i][2], CALL_TIMEOUT, gate)
                except CallFail as exc:
                    outcome = exc
                st, _stop, durable = _settle(ids[i], est[i], outcome)
            finally:
                with _inflight_lock:
                    _INFLIGHT.pop(ids[i], None)
            if not durable:
                with slot_lock:
                    state[i] = "failed"
                    agg["settle_failed"] += 1
                fail("ledger_settle_failed", remote=False)    # W2-A: never report success on a lost settlement
                return
            if isinstance(outcome, Receipt) and st == "SETTLED":
                SCHED.on_ok()
                with slot_lock:
                    if scores[i] is not None or state[i] != "sending":
                        agg["fail"] += 1
                        agg["first_err"] = agg["first_err"] or "dup_result"
                        abort.set()
                        return
                    scores[i] = outcome.score
                    state[i] = "ok"
                _stat_add(calls_ok=1)
                return
            with slot_lock:
                state[i] = "failed"
                if st in ("UNKNOWN", "USAGE_INVALID"):
                    agg["unknown"] += 1
                if isinstance(outcome, CallFail) and outcome.is429:
                    agg["n429"] += 1
            if isinstance(outcome, Receipt):           # valid score but usage invalid / over reservation
                fail(st.lower(), remote=False)
            else:
                fail(outcome.code, remote=st not in ("UNKNOWN", "USAGE_INVALID", "RELEASED_NOT_SENT_PROVEN"))
                if outcome.http_status in (401, 403):
                    _LOCAL_STOP.set()
        finally:
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
    # never join: anything still sending keeps running under its own watchdog and settles itself
    with slot_lock:
        unsent = [i for i in range(len(pool)) if state[i] == "pending"]
        for i in unsent:
            state[i] = "cancelled"
        inflight = sum(1 for st in state if st in ("sending", "claimed"))
    if unsent:
        rem = max(0.05, min(LEDGER_WAIT, deadline - time.monotonic()))
        try:
            LEDGER.release_unsent([ids[i] for i in unsent], RUN_ID, "search_ended_unsent", timeout=rem)
        except L.LedgerError:
            _stat_add(ledger_errors=1)       # RESERVED rows are released as orphans on next start
    elapsed = time.monotonic() - t0
    with slot_lock:
        ok_all = (not timed_out and not abort.is_set() and agg["fail"] == 0
                  and all(st == "ok" for st in state)
                  and all(isinstance(s, float) and math.isfinite(s) and 0 <= s <= 3 for s in scores))
        counts = {"search_id": search_id, "n_sent": agg["sent"], "n_valid": sum(st == "ok" for st in state),
                  "n_fail": agg["fail"], "n_unknown": agg["unknown"], "n_inflight_at_return": inflight,
                  "n_released_unsent": len(unsent) + agg["released_self"], "n_429": agg["n429"],
                  "queue_wait_s": round(agg["queue"], 3), "rerank_sec": round(elapsed, 3),
                  "est_usd_reserved": L.nano_to_usd(sum(est)), "n_settle_failed": agg["settle_failed"]}
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
    _stat_add(calls_sent=counts["n_sent"], calls_unknown=counts["n_unknown"], http_429=counts["n_429"],
              valid_candidates=counts["n_valid"], queue_wait_s=agg["queue"])
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
    """Normal interpreter exit: give in-flight receipts a bounded chance to settle.
    (SIGKILL/SIGTERM skip this; their SENDING rows become UNKNOWN at the next start.)"""
    end = time.monotonic() + EXIT_GRACE
    while time.monotonic() < end:
        with _inflight_lock:
            if not _INFLIGHT:
                return
        time.sleep(0.05)


if MODE != "off":
    atexit.register(_exit_grace)
