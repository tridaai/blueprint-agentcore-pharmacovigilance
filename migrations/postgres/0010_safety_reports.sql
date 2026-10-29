-- 0010: Round 11 — safety oversight reports.
-- Payloads are JSON text, exactly as with the earlier tables —
-- one contract across stores. The table is insert-only by
-- design (the store port grows no update or delete for it): a
-- report computed over a moved record is a NEW report that
-- supersedes the old one by link — the old figure stands on the
-- record, never edited. A report is an internal oversight
-- artefact — aggregate counts only, no case ids, no patient or
-- reporter field — and it is never a regulatory submission.
CREATE TABLE IF NOT EXISTS safety_reports (
    report_id TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    as_of TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_safety_reports_window ON safety_reports (window_start, window_end);
