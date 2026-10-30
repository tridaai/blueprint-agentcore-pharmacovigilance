-- 0014: Round 18 — draft-vs-final review measurements.
-- One insert-only table: a measurement is struck over a
-- case's record of record — its first assembled summary
-- against the summary its dual-control review approved —
-- and is never edited. A moved record yields a NEW
-- measurement that supersedes the old one by link.
-- Payloads are JSON text, exactly as with the earlier
-- tables — one contract across stores. A measurement is
-- an oversight artefact about a case: it writes nothing
-- to the case itself, binds no part of any seal
-- inventory, and carries statement content hashes and
-- counts, never statement text.
CREATE TABLE IF NOT EXISTS draft_final_measurements (
    measurement_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_draft_final_case ON draft_final_measurements (case_id);
