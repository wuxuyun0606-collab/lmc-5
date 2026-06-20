-- Quarantine curated-memory rows created by the old heartbeat_detector shortcut.
--
-- Problem:
-- Some downstream nightly jobs inserted HeartbeatDetector raw candidates directly
-- into lmc5_curated_memories with source='heartbeat_detector'. Those rows are
-- raw dialogue evidence, not formatted heartbeat memories, and may contain long
-- transcript snippets plus noisy keyword matches.
--
-- Safety:
-- - This migration does not delete curated memory content.
-- - It archives matching rows so normal recall paths stop surfacing them.
-- - It copies the original rows to lmc5_cold_storage when available.
-- - It removes stale vector rows and closes relation edges pointing at them,
--   because vector recall may not be able to see version_status.
-- - The audit content_hash uses chr(31) (unit separator), not a NUL byte.
--   PostgreSQL text cannot contain NUL bytes.
--
-- Preview before applying:
--   SELECT id, source, category, title, left(content, 120) AS preview
--   FROM lmc5_curated_memories
--   WHERE source = 'heartbeat_detector'
--   ORDER BY id;

BEGIN;

CREATE TEMP TABLE _lmc5_heartbeat_detector_pollution ON COMMIT DROP AS
SELECT id
FROM lmc5_curated_memories
WHERE source = 'heartbeat_detector';

INSERT INTO lmc5_z_audit (
    pair_key,
    content_hash,
    verdict,
    stale_id,
    current_id,
    reason,
    evidence,
    status,
    judged_at,
    reviewed_at
)
SELECT
    'quarantine:heartbeat_detector:' || cm.id::text,
    md5(
        coalesce(cm.source, '') || chr(31) ||
        coalesce(cm.category, '') || chr(31) ||
        coalesce(cm.title, '') || chr(31) ||
        coalesce(cm.content, '')
    ),
    'archive_polluted_detector_row',
    cm.id,
    NULL,
    'heartbeat_detector emitted raw evidence; raw detector output must pass hippocampus/NightDream review before curated storage',
    left(cm.content, 220),
    'done',
    NOW(),
    NOW()
FROM lmc5_curated_memories cm
JOIN _lmc5_heartbeat_detector_pollution bad ON bad.id = cm.id
WHERE NOT EXISTS (
    SELECT 1
    FROM lmc5_z_audit za
    WHERE za.pair_key = 'quarantine:heartbeat_detector:' || cm.id::text
);

INSERT INTO lmc5_cold_storage (
    original_id,
    source,
    category,
    title,
    content,
    weight,
    reason,
    archived_at
)
SELECT
    cm.id,
    cm.source,
    cm.category,
    cm.title,
    cm.content,
    cm.weight,
    'heartbeat_detector_pollution_quarantine',
    NOW()
FROM lmc5_curated_memories cm
JOIN _lmc5_heartbeat_detector_pollution bad ON bad.id = cm.id
WHERE NOT EXISTS (
    SELECT 1
    FROM lmc5_cold_storage cs
    WHERE cs.original_id = cm.id
      AND cs.reason = 'heartbeat_detector_pollution_quarantine'
);

UPDATE lmc5_curated_memories cm
SET version_status = 'archived',
    protected = FALSE,
    active_fact = FALSE,
    resolved = TRUE,
    invalid_at = coalesce(cm.invalid_at, NOW()),
    source_file = concat_ws(
        ' | ',
        nullif(cm.source_file, ''),
        'quarantined:heartbeat_detector_pollution:2026-06-20'
    )
FROM _lmc5_heartbeat_detector_pollution bad
WHERE bad.id = cm.id;

UPDATE lmc5_memory_relations rel
SET valid_until = coalesce(rel.valid_until, NOW())
WHERE (
    rel.source_id IN (SELECT id FROM _lmc5_heartbeat_detector_pollution)
    OR rel.target_id IN (SELECT id FROM _lmc5_heartbeat_detector_pollution)
)
AND rel.valid_until IS NULL;

DELETE FROM lmc5_vectors vec
WHERE vec.owner_type = 'curated'
  AND vec.owner_id IN (SELECT id FROM _lmc5_heartbeat_detector_pollution);

SELECT
    (SELECT count(*) FROM _lmc5_heartbeat_detector_pollution) AS quarantined_curated_rows,
    (SELECT count(*)
     FROM lmc5_z_audit
     WHERE pair_key LIKE 'quarantine:heartbeat_detector:%') AS quarantine_audit_rows;

COMMIT;
