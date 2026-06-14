# Safety and Privacy Boundaries

LMC-5 is designed for agent memory, which means it will often sit close to
credentials, private conversations, infrastructure notes, and operational
incidents. The default implementation takes a conservative stance.

## Rules

- Do not store secrets unless your deployment has a clear encrypted-at-rest and
  access-control story.
- Do not print raw memory into prompts without redaction.
- Do not send raw memory to embedding or ranking services without redaction.
- Do not auto-delete or auto-supersede facts without an audit trail.
- Do not turn a contradiction candidate into supersession without a reviewed Z verdict.
- Do not let emotional or experiential metadata override verified facts.
- Do not treat one dramatic event as a permanent identity rule.

## Redaction Coverage

The bundled redactor catches common patterns:

- API keys and bearer tokens.
- Password-like fields.
- PostgreSQL DSNs.
- IP addresses and database endpoints.
- Cookie and authorization headers.
- High-risk distress and intimate expressions for low-noise prompt injection.

The redactor is not a formal DLP system. Treat it as a safety rail, not as an
excuse to shovel raw production logs into an agent. That would be engineering
with a blindfold and a kazoo.

## Patrol Is Read-Only

`lmc5 patrol` reports:

- Multiple current facts for the same fact key.
- Review backlog.
- `other` timeline thread-split candidates.
- Low-confidence high-tension candidates.

It does not mutate records. Human review remains the default for lifecycle
changes.

## Z-Axis Audit Boundary

`lmc5 z-audit` is dry-run by default. It may list same-`fact_key` conflicts and
explicit `contradicts` relation pairs, but it does not call a model, does not
write audit rows, and does not supersede facts.

`lmc5 z-audit --apply` only writes pending `z_conflict_audits` rows. It is still
not permission to mutate memory. Supersession should be a separate reviewed
lifecycle action with an audit trail.

## VPS Deployment Boundary

A 7*24 hour VPS deployment is useful because it gives the memory layer a stable
host, scheduler, and backup target. It also increases the blast radius if you
misconfigure it. Treat the SQLite database as private state:

- Run with the least filesystem permissions that still work.
- Keep `hippocampus` dry-run until the candidate output is predictable.
- Use `hippocampus --apply` only from a controlled job with logs.
- Back up the database before enabling scheduled writes.
- Never place real provider keys, DSNs, passwords, or account tokens in memory.
- Keep forge output redacted before injecting it into a new agent session.
- Keep swap snapshots before migrations, model-assisted jobs, and scheduled writes.
