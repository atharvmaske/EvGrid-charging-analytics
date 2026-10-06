
DO $$
DECLARE
    r RECORD;
    ts_col TEXT;
    row_count BIGINT;
    newest TIMESTAMP;
BEGIN
    RAISE NOTICE '%-9s %-35s %12s   %-25s', 'SCHEMA', 'TABLE', 'ROWS', 'NEWEST_TIMESTAMP';
    RAISE NOTICE '--------------------------------------------------------------------------------';

    FOR r IN
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('bronze', 'silver', 'gold')
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
    LOOP
        EXECUTE format('SELECT count(*) FROM %I.%I', r.table_schema, r.table_name)
            INTO row_count;

        SELECT column_name INTO ts_col
        FROM information_schema.columns
        WHERE table_schema = r.table_schema AND table_name = r.table_name
          AND column_name IN ('ingestion_timestamp','updated_at','created_at',
                               'last_updated','captured_at','transformation_timestamp')
        ORDER BY array_position(
            ARRAY['ingestion_timestamp','last_updated','transformation_timestamp',
                  'captured_at','updated_at','created_at'], column_name)
        LIMIT 1;

        IF ts_col IS NOT NULL THEN
            EXECUTE format('SELECT max(%I) FROM %I.%I', ts_col, r.table_schema, r.table_name)
                INTO newest;
            RAISE NOTICE '%-9s %-35s %12s   %-25s', r.table_schema, r.table_name, row_count, newest;
        ELSE
            RAISE NOTICE '%-9s %-35s %12s   (no timestamp column)', r.table_schema, r.table_name, row_count;
        END IF;
    END LOOP;
END $$;
