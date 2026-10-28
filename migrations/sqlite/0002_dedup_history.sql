-- 0002: de-duplication assessments + links, history views.
-- Payloads are JSON text, exactly as with case summaries — one
-- contract across stores. Statuses and bands are also columns so
-- queues and candidate lists are plain indexed reads.
CREATE TABLE IF NOT EXISTS dedup_assessments (
    assessment_id TEXT PRIMARY KEY,
    case_a_id TEXT NOT NULL,
    case_b_id TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    band TEXT NOT NULL,
    score INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedup_assessments_pair ON dedup_assessments (case_a_id, case_b_id);

CREATE TABLE IF NOT EXISTS dedup_links (
    link_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL,
    case_a_id TEXT NOT NULL,
    case_b_id TEXT NOT NULL,
    band TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dedup_links_status ON dedup_links (status);
CREATE INDEX IF NOT EXISTS idx_dedup_links_case_b ON dedup_links (case_b_id);

CREATE TABLE IF NOT EXISTS history_views (
    view_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_views_subject ON history_views (kind, subject);
