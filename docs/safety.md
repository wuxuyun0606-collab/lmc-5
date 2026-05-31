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
