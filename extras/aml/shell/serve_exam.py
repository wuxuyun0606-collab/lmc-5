#!/usr/bin/env python3
"""Configure the AML JEV profile before importing the HTTP server.
The synthetic loopback harness is opt-in; public start.sh strips harness values.
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
    "AML_MAX_ACTIVE_REQUESTS": "4",
    "AML_RERANK_PER_SEARCH": "16",
    "AML_RERANK_RPM": "600",
    "AML_RERANK_POOL": "100",
    "AML_JEV_KEY_PATH": str(STATE_DIR / "secrets/.typesafe_key_aml"),
}
_FORBIDDEN = ("AML_RERANK_TEST_URL", "AML_RERANK_RUN_ID")

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

run_id = "exam-%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"),
                         secrets.token_hex(16))
os.environ.update(AML_RERANK="jev", AML_RETURN_N="100", AML_RERANK_RUN_ID=run_id)
import server  # noqa: E402  (loads the configured JEV bridge directly)

if __name__ == "__main__":
    server.main()
