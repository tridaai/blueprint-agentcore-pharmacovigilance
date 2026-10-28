-- 0008: Round 8 — injection quarantine, Memory retrievals,
-- Gateway calls.
-- Payloads are JSON text, exactly as with case summaries — one
-- contract across stores. All four tables are insert-only by
-- design (the store port grows no update or delete for them):
-- a quarantine verdict is content-addressed and stands, its
-- release is a separate act, a retrieval expires as a tombstone
-- and never a deletion, and a gateway call is recorded before
-- the tool runs and is the denial's surface.
CREATE TABLE IF NOT EXISTS quarantine_records (
    record_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_item_id TEXT NOT NULL,
    field TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_records_case ON quarantine_records (case_id);
CREATE TABLE IF NOT EXISTS quarantine_acts (
    act_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_acts_record ON quarantine_acts (record_id);
CREATE TABLE IF NOT EXISTS memory_retrievals (
    retrieval_id TEXT PRIMARY KEY,
    case_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_retrievals_case ON memory_retrievals (case_id);
CREATE TABLE IF NOT EXISTS gateway_calls (
    call_id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    role TEXT NOT NULL,
    decision TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gateway_calls_tool ON gateway_calls (tool);
