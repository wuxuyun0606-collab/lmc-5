# Automation Boundaries

> **Short version:** LMC-5 contains automatic passes, but it does not magically
> run them just because a memory was written. Writing memory is not the same as
> connecting, auditing, scoring, or metabolizing it.

Use this page when you are wiring a real deployment and need to know which parts
can run unattended, which parts need a scheduler, and which parts must remain
reviewed.

## What "Automatic" Means

In this project, **automatic** means:

- The code path exists.
- You have wired the required callbacks or storage adapter.
- You have scheduled it with cron, systemd, a worker, or your own job runner.
- You have decided whether the job is dry-run, review-only, or allowed to apply
  safe writes.

It does **not** mean `add_memory(...)` immediately builds the full XYZEM graph.
The live write path should stay cheap and safe. Heavy consolidation belongs in
background jobs.

## Axis By Axis

| Axis | Can Run Automatically | You Must Wire | Must Stay Reviewed / Manual |
|---|---|---|---|
| **X Timeline** | `consolidate` can turn raw events into chunks; `timeline_sweep(thread)` can run per configured X-line. | Raw event logging, consolidate callable, `timeline_threads=[...]`, nightly schedule. | Choosing durable thread names and interpreting split/merge suggestions. |
| **Y Relations** | The hippocampus pass can write safe relation edges from promoted candidates and `relation_hints`. | `NightDream.run()` or equivalent hippocampus callable, vector neighbor lookup, `write_safe_relation`, `queue_review_relation`, nightly schedule. | Review relations such as `contradicts`, `cause_effect`, and `supports`; broad graph cleanup. |
| **Z Fact Evolution** | `z_audit` can collect contradiction/supersession candidates into an audit path. | A contradiction source, audit table/queue, reviewer workflow, schedule. | Applying supersession unless the verdict is explicit and safe. |
| **E Experience** | Heartbeat detection, E-axis backfill, and scorer helpers can run in batch or shadow mode. | Detector/scorer callable, confidence thresholds, storage fields, schedule. | Letting noisy emotion/risk scores steer ranking before shadow validation. |
| **M Metabolism** | `patrol` is read-only and can run every night; custom decay/dedup/condensation jobs can be scheduled separately. | `patrol` callable, report destination, optional separate M-axis maintenance job. | Deleting, archiving, merging, or demoting memories without review/backups. |

## Minimal vs Production

| Implementation | What You Get Immediately | What You Still Need |
|---|---|---|
| **Minimal (`src/lmc5/`)** | SQLite store, typed relations, live recall, two-hop graph expansion, patrol checks, CLI demos. | A scheduler and any background jobs you want. Minimal mode is intentionally explicit. |
| **Production (`extras/pgvector_backend/`)** | Reference hooks, vector backend, `dream_runner`, hippocampus, Z audit, E helpers, deployment examples. | Real callbacks, API keys, database credentials, cron/systemd timers, logs, backups, review policy. |

## Deployment Acceptance Checklist

Do not call a deployment complete until these are true:

- Raw events are being recorded without requiring a model call.
- `consolidate` runs and produces reviewable chunks.
- `hippocampus` dry-run shows proposed memories and relation plans.
- `hippocampus --apply` or your production equivalent writes only safe relation
  types automatically.
- Review relation types are queued, not used as default live graph edges.
- `DreamSchedule()` or your timer resolves to the intended local quiet-hour
  schedule.
- Y graph verification shows non-empty safe edges and two-hop recall works.
- Z audit creates pending rows without silently superseding facts.
- Patrol runs read-only and reports relation/lifecycle hazards.
- Backups or swap snapshots exist before any scheduled write job.

## Common Failure Modes

- **"I wrote memories but Y is empty."** You wrote curated rows but did not run
  the nightly hippocampus relation-build pass.
- **"Two-hop recall returns nothing."** Either `memory_relations` is empty, the
  endpoints are not live, the relation type is review-only, or the edge strength
  is below the safe threshold.
- **"Z changed a fact I still needed."** Supersession was applied without a
  reviewed verdict. Keep Z audit and Z apply as separate phases.
- **"M cleaned too much."** Patrol should report first. Archival, deletion, and
  condensation need backups and a rollback plan.

See also:

- [`Y_RELATIONS.md`](Y_RELATIONS.md) for relation types and graph-build details.
- [`IMPLEMENTATION_ORDER.md`](IMPLEMENTATION_ORDER.md) for staged build order.
- [`DEPLOYMENT.md`](DEPLOYMENT.md) for cron/systemd wiring.
