-- 0001: cases, follow-up tasks, evidence chain, case summaries.
-- Payloads are JSON text, exactly as in the SQLite dialect — one
-- contract across stores. `seq` gives the evidence chain its
-- append order (SQLite uses its implicit rowid for the same job).
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    source TEXT NOT NULL,
    received_on TEXT NOT NULL,
    awareness_date TEXT NOT NULL,
    product_code TEXT,
    status TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    suspected_duplicate_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_dedup ON cases (dedup_key);

CREATE TABLE IF NOT EXISTS follow_up_tasks (
    task_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    missing_criteria TEXT NOT NULL,
    status TEXT NOT NULL,
    created_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_items (
    seq BIGINT GENERATED ALWAYS AS IDENTITY,
    item_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    recorded_on TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence_items (case_id);

CREATE TABLE IF NOT EXISTS case_summaries (
    summary_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    model_profile TEXT NOT NULL,
    payload TEXT NOT NULL
);
