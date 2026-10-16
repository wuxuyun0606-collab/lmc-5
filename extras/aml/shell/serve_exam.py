#!/usr/bin/env python3
"""Exam serving launcher (2026-10-16): shell/server.py + live JEV rerank.

Import order is load-bearing: AML_RERANK_POOL is read by server.py AT IMPORT, and server.py imports
the clef rerank module which hard-rejects AML_RERANK=jev — so the controlled env is fixed FIRST,
server imports with the rerank mode unset, then the jev env completes, rerank_jev loads (validating
ledger/key-path/limits), and the bridge replaces server.rerank before serve_forever.

Production env contract (exam profile):
- controlled parameters (budget, per-search concurrency, pool width, ledger path, budget id, key
  path) are FORCED to fixed values; an inherited value for any of them, or any test-harness
  variable, refuses startup (fail-closed) — changing the budget means editing THIS file.
- EXAM_ALLOW_TEST_HARNESS=1 (rehearsal only, never written by the production start path) flips the
  controlled parameters to setdefault so a test harness may override them.

RUN_ID is generated fresh per process start (ledger runs.run_id is a primary key; reuse would
refuse startup after a crash-restart — unique-run invariant).
"""
import datetime
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aml_paths import STATE_DIR

T = str(Path(__file__).resolve().parents[1])
sys.path[:0] = [T + "/shell", T + "/jev"]

for k in ("AML_RERANK", "AML_RETURN_N"):
    os.environ.pop(k, None)

_CONTROLLED = {
    "AML_RERANK_BUDGET_USD": "10",
    "AML_RERANK_PER_SEARCH": "16",
    "AML_RERANK_RPM": "600",
    "AML_RERANK_TRANSIENT_RECOVERY": "1",
    "AML_RERANK_POOL": "100",
    "AML_RERANK_LEDGER": str(STATE_DIR / "data/ledger/rerank_exam_jev.sqlite"),
    "AML_RERANK_BUDGET_ID": "aml-jev-serving-v1",
    "AML_JEV_KEY_PATH": str(STATE_DIR / "secrets/.typesafe_key_aml"),
}
_FORBIDDEN = ("AML_RERANK_TEST_URL", "AML_RERANK_RUN_ID", "EXAM_JEV_BUDGET_USD")

_HARNESS = os.environ.get("EXAM_ALLOW_TEST_HARNESS") == "1"
if _HARNESS:
    for _k, _v in _CONTROLLED.items():
        os.environ.setdefault(_k, _v)
else:
    _found = [k for k in (*_CONTROLLED, *_FORBIDDEN) if os.environ.get(k)]
    if _found:
        raise SystemExit("serve_exam: refusing to start, inherited controlled/harness vars in the "
                         "process env without EXAM_ALLOW_TEST_HARNESS=1: %s" % ",".join(_found))
    if len(os.environ.get("AML_API_KEY", "").strip()) < 32:
        raise SystemExit("serve_exam: refusing to start, AML_API_KEY missing/too short — the public "
                         "exam endpoint never runs without auth")
    os.environ.update(_CONTROLLED)

import server  # noqa: E402  (reads AML_RERANK_POOL now; clef rerank loads MODE=off; replaced below)

run_id = "exam-%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"),
                         secrets.token_hex(16))
os.environ.update(AML_RERANK="jev", AML_RETURN_N="100", AML_RERANK_RUN_ID=run_id)
import rerank_jev  # noqa: E402  (validates ledger/config at import; credential is read on the first search)
from exam_jev_bridge import ExamJevBridge  # noqa: E402

server.rerank = ExamJevBridge(rerank_jev)

if __name__ == "__main__":
    server.main()
