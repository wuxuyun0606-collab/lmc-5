# AML memory API and JEV serving adapter

## Version and source correspondence

| Record | Public source | What the source establishes |
|---|---|---|
| Registration snapshot · 16 Oct 2026 | [`v0.3.0-aml.1`](https://github.com/wuxuyun0606-collab/lmc-5/tree/v0.3.0-aml.1/extras/aml) · [`d02563a`](https://github.com/wuxuyun0606-collab/lmc-5/commit/d02563ad7885c027778429287a29d35e3fc2d6e7) | The Add/Search service, JEV bridge, budget ledger and launcher documented below. |
| Code Full · 25 Oct source reconciliation | [`aml-code-full-reconciliation-20261025`](https://github.com/wuxuyun0606-collab/lmc-5/tree/aml-code-full-reconciliation-20261025/extras/aml) · [`4f72877`](https://github.com/wuxuyun0606-collab/lmc-5/blob/4f72877621eed555e17c72db6e1a40bc7c762855/extras/aml/CODING-SNAPSHOT-20261025.md) | On 13 Nov the maintainers reconciled the retained October source on `fix/aml-serving-recovery`. The snapshot records that comparison. |
| Text-track retest · 30 Oct | No publicly verified commit | The reconciliation excludes the text retest implementation. |

## Registration snapshot

The serving sources were exported on 2026-10-16 from the exam deployment using
an explicit file allowlist. `deployment-source.json` records their original
SHA-256 digests. Every deployed `src/lmc5/*.py` file matched public core commit
`fb3e72c9ee7357c8b17311097b0750082a8a1237`. All function and class bodies of the
exported Python modules were preserved (AST comparison before publication).
The public package changes module discovery, runtime/credential paths, the
local budget identifier, and account-specific configuration; the startup shell
is portable. It does not silently replace or restart the live exam service.

The service imports the legacy `shell/rerank.py` in **off** mode, then
`serve_exam.py` installs `ExamJevBridge(rerank_jev)` before accepting requests.
The legacy module and its ledger are included to preserve that import contract;
Clef is not the submitted reranker. `shell/server.py` alone is a development
entry point and can run without auth. **Deploy through `start.sh`**.

## Reranker choice and API ownership

The reranking layer can be adapted to **Luna, JEV, or Clef**. Every deployment
must supply its **own provider API credentials**. This repository contains no
shared key, no project-funded API access, and no embedded exam-service credential.
The registration launcher uses **JEV**. A legacy Clef/Clef-flash
backend is included in `shell/rerank.py`; Luna requires an adapter implementing
the same serving contract. The published acceptance tests validate JEV, not all
three providers. Switching providers also requires the corresponding launcher
configuration and provider-specific budget route/pricing; replacing only the
model name is insufficient.

## Registration pipeline

- SQLite files are partitioned by `user_id`; synchronous Add stores message
  text and optional embeddings. Repeated identical `request_id` payloads return
  the cached response; changed payloads under that id return 409. Clients should
  use globally unique request ids and serialize retries of an in-flight Add.
- Lexical and vector recall are combined with RRF (`k=60`). Each recall channel
  retrieves up to the requested `top_k`; the JEV backend consumes at most 100
  candidates. A configured pool of 100 is a ceiling/selection target, not a
  guarantee of 100 hits for small `top_k` or sparse stores.
- Embeddings: DashScope **`text-embedding-v4`**, 1024 dimensions by default.
- Reranker: **TypeSafe** `jev-1.13.0`,
  `https://api.typesafe.ai/v1/systemone`. Each candidate is scored with the same
  question/memory template and 0–3 relevance criteria. JEV is an external API,
  not a model authored or redistributed by this project.
- The backend returns its full scored pool; `ExamJevBridge` returns at most
  `min(top_k, 50)`. On a failed/incomplete rerank it restores RRF ordering.
  There is no cross-request rerank result cache.
- After selection, short hits may include adjacent turns from the same session
  (hit max length 500; neighbors longer than 500 are truncated to 200 characters).
  Results are presented chronologically, or newest-first for current/latest
  queries. These operations do not add result slots.
- Experimental multi-hop (`AML_MH`, `AML_MH2`) and abstention-floor branches are
  included as existing source but **off** in the submitted profile. The module
  does not automatically enable every XYZEM feature described by the core repo.

### Limits and accounting

The fixed startup profile uses a USD 10 **JEV ledger** cap, at most 16 JEV calls
per search, a default process-wide JEV limit of 16, and a default rate of 240
candidate calls/minute (burst 4). This is **not** a verified 16-concurrent-client
HTTP capacity claim. The JEV ledger does not include embedding costs. The
snapshot's price constant is USD 0.042 / million input tokens; operators must
verify current provider prices before budgeting a new deployment.

Requests are limited to 8,000,000 body bytes. The service uses one Python process
and a `ThreadingHTTPServer`; multiple paid workers sharing the state directory
are refused by an instance lock. A search has a default 600-second rerank
deadline; this is not a latency SLA. Unknown billing outcomes, invalid ledgers,
provider auth errors, and budget exhaustion stop further paid sends as specified
by the ledger. The HTTP service can still return RRF fallback results. No paid
request is automatically retried. Inspect `X-AML-Rerank` and the health counters
to distinguish successful reranking from fallback.

## Install and configure (Linux / macOS, Python 3.9+)

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r extras/aml/requirements.txt
export AML_STATE_DIR="$HOME/.local/state/lmc5-aml"
umask 077
mkdir -p "$AML_STATE_DIR/run" "$AML_STATE_DIR/secrets" "$AML_STATE_DIR/data/ledger"
cp extras/aml/public.env.example "$AML_STATE_DIR/run/public.env"
chmod 600 "$AML_STATE_DIR/run/public.env"
```

Edit that private environment file and supply:

1. `AML_API_KEY`: a separate memory-service access key of at least 32 characters.
   This is the value given to the evaluation platform, **not** a provider key.
2. `DASHSCOPE_API_KEY`: your embedding-provider credential.
3. Put only your dedicated JEV credential in
   `$AML_STATE_DIR/secrets/.typesafe_key_aml`; set its permissions to `600`.
   A shared key at `.typesafe_key` is deliberately refused.
4. Choose a bind address and port. The template defaults to loopback. Use
   `AML_BIND=0.0.0.0` only for an intentional public listener; use a TLS proxy
   when deploying HTTPS. The port number alone does not enable TLS.

Create the ledger explicitly **once** before starting the service:

```bash
python extras/aml/jev/wo2_budget_admin_jev.py init \
  --ledger "$AML_STATE_DIR/data/ledger/rerank_exam_jev.sqlite" \
  --budget-id aml-jev-serving-v1 --route jev --grant-usd 10 \
  --note 'Initial JEV serving budget' --i-authorize
bash extras/aml/start.sh
```

This initializes a local accounting ceiling; it does not purchase credits or
make an API call. Starting the service generates a fresh run id. Do not recreate,
move, copy over, or delete the live ledger to reset spending. The ledger checks
its identity and financial invariants. `AML_STATE_DIR` must be the same for the
server and the admin tool. Changing the fixed profile requires a reviewed edit
in `shell/serve_exam.py`; `start.sh` clears the listed controlled variables both
before and after loading the environment file.

`aml.service.example` shows a systemd setup. Adjust the service user, checkout,
virtualenv and state paths, ensure that user owns the state directory, install
the unit, then use `systemctl daemon-reload` and enable/start the unit. Neither
installation nor tests in this repository modify a remote machine.

Read-only budget inspection:

```bash
python extras/aml/jev/wo2_budget_admin_jev.py show \
  --ledger "$AML_STATE_DIR/data/ledger/rerank_exam_jev.sqlite"
python extras/aml/jev/wo2_budget_admin_jev.py audit \
  --ledger "$AML_STATE_DIR/data/ledger/rerank_exam_jev.sqlite"
```

## API contract

`GET /health` is public and returns service, embedding and rerank/budget
metadata. A restarted process resets its in-memory counters; ledger totals are
persistent. Successful health alone does not prove a successful paid rerank.

Both POST endpoints accept `Authorization: Bearer <memory-service-key>`
(or `X-Api-Key: <memory-service-key>`). Auth is checked before body processing.
No credentials are returned. One service key covers the service; `user_id`
selects a storage namespace, not a separate authenticated identity.

`POST /v1/memories/add`:

```json
{
  "request_id": "example-add-001",
  "user_id": "example-user",
  "session_id": "example-session",
  "messages": [
    {"role": "user", "content": "I prefer tea.", "timestamp": 1700000000000}
  ]
}
```

Timestamps are Unix milliseconds. Success returns HTTP 200 with
`{"success": true, "request_id": "...", "user_id": "...", "session_id": "..."}`.
Add is synchronous: there is no asynchronous Add-status URL to register.

`POST /v1/memories/search`:

```json
{"user_id": "example-user", "query": "Which drink do I prefer?", "top_k": 100}
```

Success returns HTTP 200 with `{"data": [...]}`; each item includes `id`,
`content`, `score`, and, where available, `created_at`. Unknown user namespaces
return an empty list. `X-AML-Rerank: rerank` indicates successful reranking;
`fallback` indicates the original RRF candidates were used instead. Because
presentation order can be chronological, `score` need not decrease down the list.

## Verification without provider credentials

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest extras/aml/tests -q
```

Tests start a loopback-only JEV stub with synthetic memory text and fake keys,
use temporary SQLite stores, and make no paid model calls. They exercise auth,
sequential idempotency, namespace isolation, real HTTP Add/Search, JEV request
and receipt parsing, return limits, durable usage accounting, error fallback,
startup refusal on missing auth/ledger or forbidden budget overrides, and the
public launcher’s harness clearing, exclusive lock and fresh run ids on restart.
They do not establish official benchmark scores or production load capacity.

## Disclosure and license

See [the submission text](SUBMISSION.zh-CN.md) for the historical registration description.
LMC-5 core and these first-party adapters follow the repository's
AGPL-3.0-or-later license. Design influences are credited in
[docs/credits.md](../../docs/credits.md). Provider services and client libraries
retain their own terms/licenses. Only runtime adapter source and synthetic tests
are included here; no API keys, live databases, evaluation questions/answers,
or private persona data are part of this release.
