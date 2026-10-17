-- 0001: labeling queue, labels (with double-label passes), eval-run history.
CREATE TABLE IF NOT EXISTS label_items (
    item_id     text PRIMARY KEY,
    source      text NOT NULL,              -- p1 | helpsteer2 | ...
    kind        text NOT NULL CHECK (kind IN ('general', 'rag')),
    task        text NOT NULL,
    response    text NOT NULL,
    reference   text,
    context     text,
    meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS labels (
    id          bigserial PRIMARY KEY,
    item_id     text NOT NULL REFERENCES label_items(item_id) ON DELETE CASCADE,
    labeler_id  text NOT NULL CHECK (labeler_id ~ '^[A-Za-z0-9_.-]{2,40}$'),
    pass_no     smallint NOT NULL CHECK (pass_no IN (1, 2)),   -- 2 = blind re-label for self-agreement
    scores      jsonb NOT NULL,
    note        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, labeler_id, pass_no)
);
CREATE INDEX IF NOT EXISTS labels_labeler_idx ON labels (labeler_id, pass_no, created_at);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id               uuid PRIMARY KEY,
    kind                 text NOT NULL,       -- release_gate | calibration
    created_at           timestamptz NOT NULL,
    git_commit           text NOT NULL DEFAULT '',
    prompt_sha256        text NOT NULL DEFAULT '',
    judge_config_sha256  text NOT NULL DEFAULT '',
    judge_model          text NOT NULL DEFAULT '',
    rubric_version       text NOT NULL DEFAULT '',
    summary              jsonb NOT NULL,
    n_items              integer NOT NULL,
    usd                  numeric(12, 6) NOT NULL DEFAULT 0,
    record               jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS eval_runs_kind_time_idx ON eval_runs (kind, created_at);

CREATE TABLE IF NOT EXISTS eval_run_items (
    run_id   uuid NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    item_id  text NOT NULL,
    scores   jsonb NOT NULL,
    PRIMARY KEY (run_id, item_id)
);
