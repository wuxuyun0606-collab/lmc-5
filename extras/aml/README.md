# AML memory API and JEV serving adapter

This optional module publishes the LMC-5 team's AML serving implementation.
It includes the synchronous Add/Search HTTP service, TypeSafe JEV adapter,
serving bridge, startup profile, and offline acceptance tests. JEV has no local
billing ledger, price estimate or spending cap. The core `src/lmc5` remains offline and
has no new dependencies. No provider weights, credentials, user memories,
evaluation datasets, or private run logs are distributed.

## Version and source correspondence

Maintenance line: **`fix/aml-serving-recovery`**, independent of the full LMC-5
system on `main`. This competition branch is not a merge candidate for the
full-system branch. Keep competition fixes and releases on this maintenance line.

Original submission snapshot: **`v0.3.0-aml.1`** (on the 0.3.0 core).
The current branch also reconciles the retained October 25 code Full source.
See [the November reconciliation](CODING-SNAPSHOT-20261025.md) and its separate
[coding source manifest](coding-source-20261025.json). The October 23 fixes and
the later retained deployment repairs are not in the immutable registration tag.
The new publication is not a contemporaneous October commit or a replacement
registration. The original snapshot is distinct from `v0.2.0`.

The serving sources were exported on 2026-10-16 from the exam deployment using
an explicit file allowlist. `deployment-source.json` records their original
SHA-256 digests. Every deployed `src/lmc5/*.py` file matched public core commit
`fb3e72c9ee7357c8b17311097b0750082a8a1237`. All function and class bodies of the
exported Python modules were preserved (AST comparison before publication).
The public package changes module discovery, runtime/credential paths and
account-specific configuration; the startup shell is portable. It does not
silently replace or restart the live exam service.

`serve_exam.py` sets the competition configuration before `server.py` loads the
JEV bridge directly. There is no legacy reranker or billing module in this
startup path. `shell/server.py` alone is a development entry point and can run
without auth. **Deploy through `start.sh`**.

## Reranker choice and API ownership

The reranking layer can be adapted to **Luna, JEV, or Clef**. Every deployment
must supply its **own provider API credentials**. This repository contains no
shared key, no project-funded API access, and no embedded exam-service credential.
This competition branch ships **JEV**. Luna and Clef require an adapter
implementing the same serving contract; the unused legacy Clef implementation
has been removed from this branch. The acceptance tests validate JEV. Switching providers also requires the corresponding launcher
configuration and request/response adapter; replacing only the model name is
insufficient.

## Actual submitted pipeline

- SQLite files are partitioned by `user_id`; synchronous Add stores message
  text and optional embeddings. Repeated identical `request_id` payloads return
  the cached response; changed payloads under that id return 409. Clients should
  use globally unique request ids and serialize retries of an in-flight Add.
- Lexical and vector recall are combined with RRF (`k=60`). Each recall channel
  retrieves up to the requested `top_k`; the JEV backend consumes at most 100
  candidates. A configured pool of 100 is a ceiling/selection target, not a
  guarantee of 100 hits for small `top_k` or sparse stores.
- Vector recall scans **all matching vectors** using a bounded top-K heap. For
  `top_k=100`, it retains 100 compact candidates and loads only their full
  records, instead of materializing every vector and record. Scores are rounded
  to six decimals before ranking; ties retain the original SQL row order. This
  preserves the original selection and does not search only the first 100 rows.
  The core API still honors other `limit` values; legacy slicing and non-finite
  edge cases use the preserved full-sort implementation.
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

### Runtime limits and failure handling

The serving profile admits at most four POST requests before reading bodies;
waiting connections/threads are not globally bounded. Health bypasses this queue.
It uses at most 16 concurrent JEV calls per search, a default
process-wide limit of 16, and 600 candidate calls/minute (burst 4). The generic
backend default remains 240. This is not a verified HTTP client-concurrency
capacity claim. The request body limit is 8,000,000 bytes. A process lock avoids
starting two serving processes against the same state directory.

The billing modules, price estimates, budget gates and recovery machinery have
been deleted. There is no replacement accounting component or compatibility
layer. Provider-reported token counts are optional diagnostics; actual billing
and spending controls belong to the provider account.

A failed, invalid or uncertain JEV call falls back to RRF for that search. It does
not create a sticky stop for other searches or future process starts. Missing
or malformed usage does not invalidate an otherwise valid JEV score. Failed
calls are not automatically retried, including after restarting the service.

Operational limits remain: a default 600-second rerank deadline, per-call socket
watchdogs, concurrency/RPM scheduling, provider `Retry-After`, and a finite
circuit cooldown after repeated failures. A valid long `Retry-After` is honored
in full while this process runs; it is no longer persisted in a billing DB.
These are not latency guarantees. Inspect `X-AML-Rerank`, fallback reasons and
rate-hold counters to distinguish successful reranking from fallback.

### Relation to the historical competition

The current source reconstructs the retained code Full implementation with
explicit public path/account substitutions and a portable four-request profile.
The original tag and `deployment-source.json` remain October 16 evidence; use
`coding-source-20261025.json` for the November reconciliation. Publishing this
source does not deploy, restart, or alter any exam service. Matching source
functions does not establish identical indexes, data, upstream behavior or
results. See the reconciliation document for known limits and evidence scope.

### Historical long-input behavior

An embedding query is retried once with a 7680-byte UTF-8 head/tail copy only
after a definitive HTTP 400 input-length rejection. Successful inputs remain
unchanged. JEV receives a request-only state bounded to 30000 UTF-8 bytes;
stored text is not rewritten. Lexical queries above 64 terms skip the lexical
candidate channel. These can affect long-input results. Add retains the original
behavior; the later text-retake long-Add repair is not included.

## Install and configure (Linux / macOS, Python 3.9+)

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r extras/aml/requirements.txt
export AML_STATE_DIR="$HOME/.local/state/lmc5-aml"
umask 077
mkdir -p "$AML_STATE_DIR/run" "$AML_STATE_DIR/secrets"
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

Start the service:

```bash
bash extras/aml/start.sh
```

Starting the service generates a fresh diagnostic run id. The fixed candidate
pool and concurrency profile is in `shell/serve_exam.py`; `start.sh` clears its
listed controlled/harness variables before and after loading the environment.

`aml.service.example` shows a systemd setup. Adjust the service user, checkout,
virtualenv and state paths, ensure that user owns the state directory, install
the unit, then use `systemctl daemon-reload` and enable/start the unit. Neither
installation nor tests in this repository modify a remote machine.

## API contract

`GET /health` is public and returns service, embedding and rerank diagnostics.
JEV reports call/search counts, fallback reasons, optional input-token counts,
in-flight calls and rate holds; it has no `budget` or estimated-dollar fields.
Counters reset on process restart. Successful health alone does not prove a
successful paid rerank.

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
and score parsing, return limits, missing/invalid usage, error fallback,
401/403/503/529 recovery on the next search without replay, Retry-After and
deadlines, independent concurrent searches, and startup without a billing DB.
The public launcher is checked for auth, harness clearing, process locking and
fresh diagnostic run ids. Run the vector equivalence regressions with
`PYTHONPATH=src python -m pytest tests/test_vectors.py tests/test_vector_topk.py -q`.
They do not establish official benchmark scores or production load capacity.

## Disclosure and license

See [the submission text](SUBMISSION.zh-CN.md) for the form-ready description.
LMC-5 core and these first-party adapters follow the repository's
AGPL-3.0-or-later license. Design influences are credited in
[docs/credits.md](../../docs/credits.md). Provider services and client libraries
retain their own terms/licenses. Only runtime adapter source and synthetic tests
are included here; no API keys, live databases, evaluation questions/answers,
or private persona data are part of this release.


### Regression note for the serving repair

An HTTP-200 memory response can contain an RRF fallback. Track `X-AML-Rerank`,
the rerank/fallback counters and provider rate holds separately from HTTP
availability. A short three-candidate smoke test does not establish latency for
a 100-candidate search: at 240 candidate calls/minute the rate limiter alone
accounts for roughly 25 seconds; the serving rate of 600 targets roughly 10
seconds before network and storage overhead. Neither is a latency guarantee.

The existing source snapshot and release links above identify the original
submission. This change removes JEV billing gates and adds bounded-memory vector selection;
it does not rewrite the original release tag or its source-hash manifest.
