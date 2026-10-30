-- 0012: Round 13 — signal tables.
-- Payloads are JSON text, exactly as with the earlier tables —
-- one contract across stores. The table is insert-only by
-- design (the store port grows no update or delete for it): a
-- table computed over a moved record is a NEW table that
-- supersedes the old one by link — the old figures stand on
-- the record, never edited. A table is an internal signal
-- detection artefact — aggregate counts only, no case ids, no
-- patient or reporter field — and it is never a regulatory
-- submission.
CREATE TABLE IF NOT EXISTS signal_tables (
    table_id TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    as_of TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_tables_window ON signal_tables (window_start, window_end);
