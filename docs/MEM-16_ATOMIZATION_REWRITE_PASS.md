# MEM-16: Atomization Rewrite Pass

Reported by cicio on 2026-07-07 from Awen's downstream LMC-5 dry-run audit.

## Problem

The 2026-07-01 audit found candidate memories that were structurally valid but
not ready to surface in live recall:

- 8/8 sampled candidates were `too_coarse`.
- 30/64 preview atoms were `needs_rewrite`.
- The failure mode was chunk scaffolding leaking into memory text, such as
  `Event chunk ... First ... Last ... Keywords ...`.

That makes the recall pipeline look connected while the water is still muddy:
live recall would show half-processed memory blocks instead of usable memory
atoms.

## Fix

The default provider-free hippocampus path now produces event-level atoms:

- It reads the source events linked to each `event_chunk`.
- It emits one compact candidate per source event instead of one candidate per
  chunk summary.
- It keeps `source_chunk_ids` and adds `source_event_ids` for traceability.
- It keeps the legacy chunk-level proposer available behind
  `--legacy-chunk-proposer` for comparison and regression audits.

The new `atom-audit` CLI command is dry-run only. It runs hippocampus preview,
checks atom quality, and reports `too_coarse` / `needs_rewrite` ratios without
writing memories.

## Audit Commands

Use a temporary database copy. Do not point these commands at a production
memory database.

```bash
cp awen-memory.sqlite /tmp/lmc5-mem16-audit.sqlite

python -m lmc5.cli --db /tmp/lmc5-mem16-audit.sqlite atom-audit \
  --channel nightly \
  --limit-chunks 8 \
  --min-importance 1 \
  --max-promote 64 \
  --legacy-chunk-proposer

python -m lmc5.cli --db /tmp/lmc5-mem16-audit.sqlite atom-audit \
  --channel nightly \
  --limit-chunks 8 \
  --min-importance 1 \
  --max-promote 64
```

For PostgreSQL deployments, run the equivalent audit against a cloned database
or an exported SQLite fixture. The boundary is unchanged: no writes to Awen's
production memory database and no online daemon restart during the audit.

## Acceptance

- Same parameters before and after.
- Report `too_coarse` and `needs_rewrite` ratios for both legacy chunk mode and
  default atom mode.
- Sample 10 dry-run candidates from `quality.items` and manually check whether
  each line reads like a memory rather than pipeline scaffolding.
