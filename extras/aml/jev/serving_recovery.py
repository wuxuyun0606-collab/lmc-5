"""Bounded serving recovery for explicit transient HTTP failures; never refunds or retries.

Only a completed 500/502/503/504/529 response with missing usage can be retained
conservatively. Transport ambiguity, orphan sends, invalid usage, auth, rate-limit,
accounting and budget failures remain manual stops. No new schema is required.
"""
from datetime import datetime, timezone
import time

TRANSIENT = {500, 502, 503, 504, 529}
MARKER = 'auto-transient-keep'


def recover(ledger, *, actor, env_cap_nano, next_reserve_nano=0,
            cooldown=10.0, max_per_hour=5, max_total=25,
            max_held_nano=50_000_000, timeout=2.0):
    """All checks and the keep/stop resolution commit in one SQLite transaction."""
    def tx(c):
        stops = c.execute('SELECT id,kind,attempt_id FROM stops WHERE resolved_ts IS NULL').fetchall()
        if not stops:
            return {'recovered': False, 'reason': 'no_stop'}
        if any(kind != 'UNKNOWN' or aid is None for _, kind, aid in stops):
            return {'recovered': False, 'reason': 'hard_stop'}
        if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or ledger._financial_problems(c):
            return {'recovered': False, 'reason': 'audit_failed'}
        if c.execute("SELECT COUNT(*) FROM attempts WHERE status IN ('RESERVED','SENDING')").fetchone()[0]:
            return {'recovered': False, 'reason': 'open_attempts'}
        pause = c.execute('SELECT MAX(pause_until_epoch) FROM route_state').fetchone()[0]
        if pause is not None and float(pause) > time.time():
            return {'recovered': False, 'reason': 'provider_pause'}
        rows = c.execute("SELECT attempt_id,status,phase,http_status,usage_status,err,charge_nano,ts_final "
                         "FROM attempts WHERE status IN ('UNKNOWN','USAGE_INVALID','HISTORY_UNKNOWN')").fetchall()
        if not rows or {r[0] for r in rows} != {s[2] for s in stops}:
            return {'recovered': False, 'reason': 'hold_stop_mismatch'}
        latest = 0.0
        for aid, status, phase, http, usage, err, charge, final in rows:
            if (status != 'UNKNOWN' or phase != 'response' or http not in TRANSIENT
                    or usage != 'missing' or err != 'http_' + str(http)):
                return {'recovered': False, 'reason': 'not_explicit_transient_response'}
            try:
                latest = max(latest, datetime.fromisoformat(final.replace('Z', '+00:00')).timestamp())
            except (ValueError, AttributeError):
                return {'recovered': False, 'reason': 'invalid_timestamp'}
        # Include both manually retained and automatically retained uncertainty in exposure cap.
        kept = c.execute("SELECT COALESCE(SUM(charge_nano),0) FROM attempts WHERE status IN "
                         "('UNKNOWN_KEPT','USAGE_INVALID_KEPT')").fetchone()[0]
        if kept + sum(r[6] for r in rows) > max_held_nano:
            return {'recovered': False, 'reason': 'uncertainty_cap'}
        past = c.execute("SELECT ts_final FROM attempts WHERE status='UNKNOWN_KEPT' AND note LIKE ?",
                         ('%' + MARKER + '%',)).fetchall()
        now = time.time()
        try:
            recent = sum(datetime.fromisoformat(t[0].replace('Z', '+00:00')).timestamp() >= now - 3600 for t in past)
        except (ValueError, AttributeError):
            return {'recovered': False, 'reason': 'invalid_recovery_history'}
        if len(past) + len(rows) > max_total or recent + len(rows) > max_per_hour:
            return {'recovered': False, 'reason': 'recovery_limit'}
        if ledger._exposure(c) + next_reserve_nano > min(ledger._cap(c), env_cap_nano):
            return {'recovered': False, 'reason': 'insufficient_budget'}
        wait = max(0.0, latest + cooldown - now)
        if wait:
            return {'recovered': False, 'reason': 'cooldown', 'wait_s': wait}
        ts = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        for aid, *_ in rows:
            # No charge adjustment: the full existing reservation stays charged.
            c.execute("UPDATE attempts SET status='UNKNOWN_KEPT',ts_final=?,note=COALESCE(note,'') || ? "
                      "WHERE attempt_id=? AND status='UNKNOWN'", (ts, ' | ' + MARKER, aid))
            c.execute("UPDATE stops SET resolved_ts=?,resolved_by=?,resolve_note=? "
                      "WHERE attempt_id=? AND kind='UNKNOWN' AND resolved_ts IS NULL",
                      (ts, actor, MARKER + ': full reservation retained; no replay', aid))
        if ledger._financial_problems(c):
            raise RuntimeError('recovery_financial_invariant_failed')
        return {'recovered': True, 'reason': MARKER, 'attempts': len(rows),
                'held_nano': sum(r[6] for r in rows)}
    return ledger.tx(tx, timeout)
