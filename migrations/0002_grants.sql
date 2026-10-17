-- 0002: least-privilege grants for the UI's runtime role (Cloud SQL). Skipped when the role does
-- not exist (local docker compose uses a single owner role).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'judgecal_app') THEN
        GRANT SELECT ON label_items TO judgecal_app;
        GRANT SELECT, INSERT, UPDATE ON labels TO judgecal_app;
        GRANT USAGE, SELECT ON SEQUENCE labels_id_seq TO judgecal_app;
        GRANT SELECT ON eval_runs, eval_run_items, schema_migrations TO judgecal_app;
    END IF;
END
$$;
