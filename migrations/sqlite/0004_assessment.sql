-- 0004: seriousness + expectedness + regulatory clocks — the
-- reference safety information and case assessments. Payloads are
-- JSON text, exactly as with case summaries — one contract across
-- stores. Both tables are insert-only by design (the store port
-- grows no update or delete for them): a verdict that changes
-- arrives as a new assessment superseding the old one by link.
CREATE TABLE IF NOT EXISTS reference_safety_info (
    version TEXT PRIMARY KEY,
    created_on TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_assessments (
    assessment_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    seriousness TEXT NOT NULL,
    expectedness TEXT NOT NULL,
    obligation_class TEXT NOT NULL,
    deadline TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_assessments_case ON case_assessments (case_id);
