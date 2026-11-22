# Code Full source reconciliation — published November 2026

This is a retrospective source reconciliation for task
`teval_f1815ffa991191d7`, registered as `v0.3.0-aml.1`.
It is prepared on **2026-11-13 (America/Los_Angeles)** from retained October
source files and deployment receipts. The new commit is a November publication,
not an October commit, original registration, or new competition submission.

The immutable registration tag remains at
`d02563ad7885c027778429287a29d35e3fc2d6e7`. The October 23 maintenance work is
already in parent `23f4aa4694eb3d22c73312d729fefc87b7b30050` on
`fix/aml-serving-recovery`. This reconciliation stays on that separate AML line.
It does not update the full-system `main`, move the old tag, or deploy a service.

## What is reconciled

The retained queued server, JEV implementation, input-limit helper, bridge and
13 core Python modules, plus the portable path helper, are mapped in [coding-source-20261025.json](coding-source-20261025.json).
The manifest records both historical SHA-256 and published SHA-256. All top-level
function/class ASTs in the 17 implementation files match their retained counterparts. This
checks code structure, not environment, data, external API behavior or scores.

The server retains public module discovery and configurable secret paths and
Cloudflare account settings instead of publishing private host/account defaults.
`aml_paths.py` retains the public state-directory default. Those portability
differences are explicit; the entire checkout is not claimed byte-identical to
the historical deployment. No evaluation data, live databases, credentials or
unreviewed operational logs are included. DEJA experiments, later multimodal
changes, and the October 30 text-retake implementation are not imported.

The public launcher now selects `AML_MAX_ACTIVE_REQUESTS=4`, matching the
historical systemd drop-in. It clears inherited overrides before loading the
fixed profile. This is a November packaging change reproducing a recorded
setting, not a claim that the historical launcher had those exact bytes.

## Historical repair chain and evidence

| Evidence / date | Change or observation | Limit of the evidence |
| --- | --- | --- |
| October 23 PDT, commits `af797d6`, `23f4aa4` | Streamed vector top-K; removal of billing ledger, financial gates and legacy backend | Commit time is not precise push/deployment time |
| Retained server, comment dated October 18 | Embedding ConnectTimeout recovery with at most five attempts and remaining per-batch time allowance | The comment alone does not establish its first deployment timestamp; phase timeouts are not a total transport wall-clock guarantee |
| October 24 UTC deployment chain | Once-per-process store initialization; diagnostic isolation and upstream error metadata | Retained deployment receipts, not a full-run trace |
| October 24 09:07:26 UTC backup/deployment receipt | One 7680-byte query-copy retry after explicit embedding HTTP 400 input-length rejection; JEV state capped at 30000 UTF-8 bytes | Stored text unchanged; long provider inputs can lose middle content and affect retrieval/scoring |
| Present in October 24/25 retained store | Skip lexical candidates above 64 query terms; catch SQLite expression-depth errors | First introduction time not established; this can affect lexical recall |
| October 25 15:56 UTC queue deployment | Four admitted POST requests before body reads; health bypasses queue | Bounds admitted work, not all sockets, waiting threads or total latency |
| Code Full start and first-failure samples | Same server digest and PID 1385992; request limit 4 | Sample-point correspondence only, not proof of unchanged code throughout the Full |

The historical queued-server SHA-256 is
`155b06a4231772e2a108e58e479338657d30c172e2680d8adafa87a17317c602`.
The prior server was
`7e627f8255d8275bdb9dc0642afdb79886e2fe48cc42ee13837edd0575d013e7`.
The manifest contains timestamped excerpts and hashes of the local receipt
files; hashes identify retained evidence, not a third-party timestamp attestation.

## Runtime contract and limitations

The bridge still selects at most 100 rerank candidates and returns at most
`min(top_k, 50)`. Time presentation, neighbor merging, scoring template and
default-off multi-hop/abstention settings retain the historical implementation.
JEV uses the historical official endpoint. This is not the later OR-fallback
text-retake build. Rerank failures may return HTTP 200 with RRF fallback; inspect
headers and counters. Local billing ledgers and spending caps remain absent.

Long-query recovery and lexical skipping above are not represented as
semantically neutral for every input. The historical Add path is retained;
the later long-single-Add truncation and atomic-write improvements are not
backported. The queue and synthetic tests provide no promise of a latency
ceiling, complete immunity to OOM, or successful handling of every extreme input.

This publication does not establish which endpoint/commit, index, storage
namespace, request payload or settings the organizer used for its rerun. It
does not explain the reported score/response differences without paired logs.

## Reproduction checks

The verifier reads immutable commit `4f72877` from local Git history by default.
For a downloaded archive, run the current verifier with `--source-root PATH` pointing
to the extracted historical snapshot. Its byte hashes and Python 3.9-format AST
digests refer to that snapshot. Later maintenance fixes have their own commits.

Use the documented virtualenv and optional service dependencies, then run:

```bash
python extras/aml/verify_coding_snapshot.py
PYTHONPATH=src python -m pytest extras/aml/tests tests/test_vectors.py tests/test_vector_topk.py -q
```

These tests use synthetic data, temporary SQLite databases and loopback provider
stubs. They cover input-length rejection/recovery, valid short inputs, Unicode
bounds, source correspondence, initialization, diagnostic privacy, admission
queue slot release, auth, idempotency, rerank fallback and vector selection.
They are not a repeat of the official Full or the organizer's 120 executions.

The October query-length acceptance receipt separately records six real-provider
synthetic searches, 600 successful JEV candidate calls and zero fallback. That
is historical evidence, not new provider testing performed for this publication.
