"""Ledger recovery invariants, synthetic local SQLite only."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'jev'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


L = load('rerank_ledger_jev')
R = load('serving_recovery')


@pytest.fixture
def ledger(tmp_path):
    led = L.Ledger.init_new(tmp_path / 'budget.sqlite', budget_id='unit', route='test',
                            grant_nano=10**9, actor='test', note='synthetic')
    led.register_run(run_id='run', mode='jev', model='jev-1.13.0', route='test', env_cap_nano=10**9, test_mode=True)
    return led


def hold(led, http=529, phase='response', status='UNKNOWN', usage='missing'):
    ok, ids = led.reserve_search(search_id='s', run_id='run', model='jev-1.13.0', ests=[4200], env_cap_nano=10**9, timeout=1)
    assert ok == 'ok'
    aid = ids[0]
    assert led.mark_sending(aid, 'run', 1) == 'ok'
    led.finalize(aid, 'run', status=status, charge_nano=4200,
                 fields={'phase': phase, 'http_status': http, 'usage_status': usage, 'err': 'http_' + str(http)},
                 stop_kind=status)
    return aid


def recover(led, **kw):
    return R.recover(led, actor='test', env_cap_nano=10**9, cooldown=0, **kw)


def test_retained_exposure_survives_reload_and_no_double_recovery(ledger):
    aid = hold(ledger)
    before = ledger.snapshot()['committed_usd']
    assert recover(ledger)['recovered']
    ledger = L.Ledger(ledger.path)
    assert ledger.get_attempt(aid)['status'] == 'UNKNOWN_KEPT'
    assert ledger.snapshot()['committed_usd'] == before
    assert recover(ledger)['reason'] == 'no_stop'
    assert ledger.audit()['ok']


@pytest.mark.parametrize('args', [dict(http=401), dict(http=429), dict(http=200),
                                 dict(phase='transport'), dict(status='USAGE_INVALID', usage='invalid')])
def test_other_unknowns_remain_hard_stops(ledger, args):
    hold(ledger, **args)
    assert not recover(ledger)['recovered']
    assert ledger.snapshot()['stopped']


def test_extra_hard_stop_prevents_recovery(ledger):
    hold(ledger)
    ledger.add_stop('AUTH_REJECTED', 'run', 'test')
    assert recover(ledger)['reason'] == 'hard_stop'


def test_open_attempt_prevents_recovery(ledger):
    _, ids = ledger.reserve_search(search_id='open', run_id='run', model='jev-1.13.0', ests=[4200], env_cap_nano=10**9, timeout=1)
    hold(ledger)
    assert recover(ledger)['reason'] == 'open_attempts'


def test_recovery_limits_are_persistent(ledger):
    hold(ledger); assert recover(ledger, max_total=1)['recovered']
    hold(ledger)
    assert recover(L.Ledger(ledger.path), max_total=1)['reason'] == 'recovery_limit'


def test_uncertainty_cap_does_not_release_money(ledger):
    hold(ledger)
    assert recover(ledger, max_held_nano=4199)['reason'] == 'uncertainty_cap'
    assert ledger.snapshot()['committed_usd'] == '0.000004200'


def test_budget_and_financial_corruption_refuse_recovery(ledger):
    aid = hold(ledger)
    assert recover(ledger, next_reserve_nano=10**9)['reason'] == 'insufficient_budget'
    with sqlite3.connect(ledger.path) as c:
        c.execute('UPDATE attempts SET charge_nano=1 WHERE attempt_id=?', (aid,))
    assert recover(ledger)['reason'] == 'audit_failed'


def test_cooldown_is_not_bypassed(ledger):
    hold(ledger)
    result = R.recover(ledger, actor='test', env_cap_nano=10**9, cooldown=10)
    assert result['reason'] == 'cooldown' and 0 < result['wait_s'] <= 10
