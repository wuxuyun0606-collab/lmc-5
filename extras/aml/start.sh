#!/usr/bin/env bash
# Public launcher: load the operator's environment, then use the fixed exam profile.
set -euo pipefail
MODULE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AML_STATE_DIR="${AML_STATE_DIR:-${HOME}/.local/state/lmc5-aml}"
export AML_STATE_DIR
ENV_FILE="${AML_ENV_FILE:-${AML_STATE_DIR}/run/public.env}"
CLEAR_VARS="EXAM_ALLOW_TEST_HARNESS AML_RERANK_TEST_URL EXAM_JEV_BUDGET_USD AML_JEV_KEY_PATH
AML_RERANK_LEDGER AML_RERANK_BUDGET_ID AML_RERANK_RUN_ID AML_RERANK AML_RETURN_N
AML_RERANK_BUDGET_USD AML_RERANK_PER_SEARCH AML_RERANK_POOL AML_RERANK_RPM AML_RERANK_TRANSIENT_RECOVERY"
unset $CLEAR_VARS
set -a
source "$ENV_FILE"
set +a
unset $CLEAR_VARS
exec "${AML_PYTHON:-python3}" "$MODULE_DIR/shell/serve_exam.py"
