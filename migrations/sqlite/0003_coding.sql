-- 0003: dictionary coding — dictionary versions, coding decisions,
-- coding-review tasks. Payloads are JSON text, exactly as with case
-- summaries — one contract across stores. Dictionary versions and
-- coding decisions are insert-only by design (the store port grows
-- no update or delete for them): a change is a new artefact.
CREATE TABLE IF NOT EXISTS dictionary_versions (
    version TEXT PRIMARY KEY,
    created_on TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coding_decisions (
    decision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    dictionary_version TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coding_decisions_case ON coding_decisions (case_id);

CREATE TABLE IF NOT EXISTS coding_review_tasks (
    task_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coding_tasks_status ON coding_review_tasks (status);
CREATE INDEX IF NOT EXISTS idx_coding_tasks_case ON coding_review_tasks (case_id);
