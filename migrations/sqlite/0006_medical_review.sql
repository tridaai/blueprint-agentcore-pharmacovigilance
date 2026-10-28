-- 0006: medical review — dual-control review acts.
-- Payloads are JSON text, exactly as with case summaries — one
-- contract across stores. The table is insert-only by design
-- (the store port grows no update or delete for it): a review
-- act never changes in place — a case's review state, its holds
-- and its stale acts are all derived from the acts in code.
CREATE TABLE IF NOT EXISTS medical_review_acts (
    act_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    role TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_medical_review_acts_case ON medical_review_acts (case_id);
