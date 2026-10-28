-- 0007: sealing + export — the closed case file.
-- Payloads are JSON text, exactly as with case summaries — one
-- contract across stores. Both tables are insert-only by design
-- (the store port grows no update or delete for them): a seal is
-- taken once over a case's file and never edited, and every
-- export serving is its own record on top of the seal.
CREATE TABLE IF NOT EXISTS case_seals (
    seal_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    sealed_by TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_seals_case ON case_seals (case_id);
CREATE TABLE IF NOT EXISTS case_exports (
    export_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    seal_id TEXT NOT NULL,
    exported_by TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_exports_case ON case_exports (case_id);
