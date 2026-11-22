-- Stop legacy SessionEnd writers before running this migration.
BEGIN;
LOCK TABLE lmc5_raw_events IN ACCESS EXCLUSIVE MODE;
ALTER TABLE lmc5_raw_events ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE lmc5_raw_events ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;

-- Some historical events have no usable timestamp. Keep their old date, and
-- preserve the original ingestion timestamp independently for every old row.
CREATE OR REPLACE FUNCTION pg_temp.lmc5_event_time(stamp jsonb)
RETURNS timestamptz LANGUAGE plpgsql AS $$
DECLARE
    value text := stamp #>> '{}';
    epoch numeric;
BEGIN
    IF jsonb_typeof(stamp) = 'number' THEN
        epoch := value::numeric;
        IF abs(epoch) >= 100000000000 THEN epoch := epoch / 1000; END IF;
        RETURN to_timestamp(epoch::double precision);
    ELSIF value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9:]+([.][0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
        RETURN value::timestamptz;
    END IF;
    RETURN NULL;
EXCEPTION WHEN data_exception THEN
    RETURN NULL;
END;
$$;

UPDATE lmc5_raw_events
   SET ingested_at = created_at,
       created_at = COALESCE(pg_temp.lmc5_event_time(
           COALESCE(metadata->'timestamp', metadata->'message'->'timestamp')), created_at)
 WHERE ingested_at IS NULL;
ALTER TABLE lmc5_raw_events ALTER COLUMN ingested_at SET DEFAULT NOW();
ALTER TABLE lmc5_raw_events ALTER COLUMN ingested_at SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS lmc5_raw_events_session_event_key
    ON lmc5_raw_events(session_id, event_key);
CREATE INDEX IF NOT EXISTS lmc5_raw_events_unkeyed_session
    ON lmc5_raw_events(session_id) WHERE event_key IS NULL;
ALTER TABLE lmc5_raw_events DROP CONSTRAINT IF EXISTS lmc5_raw_events_session_id_role_content_key;
COMMIT;
