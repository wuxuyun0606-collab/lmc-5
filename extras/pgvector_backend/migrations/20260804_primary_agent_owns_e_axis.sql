-- E-axis ownership correction
--
-- The primary/user-facing agent authors E content and chooses its initial
-- priority. Housekeeper/scorer output goes to a proposal queue only.

ALTER TABLE lmc5_curated_memories
    ADD COLUMN IF NOT EXISTS e_authored_by TEXT;

ALTER TABLE lmc5_curated_memories
    ADD COLUMN IF NOT EXISTS e_initial_priority INTEGER;

ALTER TABLE lmc5_curated_memories
    DROP CONSTRAINT IF EXISTS lmc5_e_initial_priority_range;

ALTER TABLE lmc5_curated_memories
    ADD CONSTRAINT lmc5_e_initial_priority_range
    CHECK (e_initial_priority IS NULL OR e_initial_priority BETWEEN 1 AND 100);

ALTER TABLE lmc5_curated_memories
    DROP CONSTRAINT IF EXISTS lmc5_e_primary_authorship;

-- NOT VALID preserves legacy rows for explicit review while enforcing the
-- contract for new writes and future updates.
ALTER TABLE lmc5_curated_memories
    ADD CONSTRAINT lmc5_e_primary_authorship CHECK (
        (
            valence IS NULL AND arousal IS NULL AND tension IS NULL
            AND response_tendency IS NULL AND growth_delta IS NULL
            AND mood_icon IS NULL
        )
        OR (
            NULLIF(BTRIM(e_authored_by), '') IS NOT NULL
            AND e_initial_priority IS NOT NULL
        )
    ) NOT VALID;

CREATE TABLE IF NOT EXISTS lmc5_e_axis_proposals (
    id              BIGSERIAL PRIMARY KEY,
    memory_id       BIGINT NOT NULL REFERENCES lmc5_curated_memories(id) ON DELETE CASCADE,
    valence         REAL,
    arousal         REAL,
    tension         REAL,
    response_tendency TEXT,
    growth_delta    TEXT,
    confidence      REAL,
    proposer        TEXT,
    rubric_version  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS lmc5_e_axis_proposals_pending_idx
    ON lmc5_e_axis_proposals (status, created_at DESC);

COMMENT ON COLUMN lmc5_curated_memories.e_authored_by IS
    'Primary/user-facing agent that personally authored this E-axis content';
COMMENT ON COLUMN lmc5_curated_memories.e_initial_priority IS
    'Initial E order chosen by the primary agent; automation manages only afterward';
COMMENT ON TABLE lmc5_e_axis_proposals IS
    'Non-authoritative housekeeper/scorer suggestions awaiting primary-agent review';

CREATE OR REPLACE FUNCTION lmc5_guard_e_axis_origin()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.e_authored_by IS DISTINCT FROM OLD.e_authored_by
       OR NEW.e_initial_priority IS DISTINCT FROM OLD.e_initial_priority
       OR NEW.valence IS DISTINCT FROM OLD.valence
       OR NEW.arousal IS DISTINCT FROM OLD.arousal
       OR NEW.tension IS DISTINCT FROM OLD.tension
       OR NEW.response_tendency IS DISTINCT FROM OLD.response_tendency
       OR NEW.growth_delta IS DISTINCT FROM OLD.growth_delta
       OR NEW.mood_icon IS DISTINCT FROM OLD.mood_icon THEN
        RAISE EXCEPTION
            'E-axis authored content and initial priority are immutable; create a successor record';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lmc5_guard_e_axis_origin_trigger ON lmc5_curated_memories;
CREATE TRIGGER lmc5_guard_e_axis_origin_trigger
BEFORE UPDATE OF e_authored_by, e_initial_priority, valence, arousal, tension,
                 response_tendency, growth_delta, mood_icon
ON lmc5_curated_memories
FOR EACH ROW EXECUTE FUNCTION lmc5_guard_e_axis_origin();
