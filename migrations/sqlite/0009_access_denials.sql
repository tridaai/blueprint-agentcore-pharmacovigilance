-- 0009: Round 10 — the access denial log.
-- Payloads are JSON text, exactly as with the Round 8 tables —
-- one contract across stores. The table is insert-only by
-- design (the store port grows no update or delete for it): a
-- refusal is recorded before the error propagates, and the log
-- is itself a guarded surface — the auditor and the case admin
-- read it, nobody edits it. `target` carries only the
-- identifier the requester supplied, so a denial over a hidden
-- case never states whether the case exists.
CREATE TABLE IF NOT EXISTS access_denials (
    denial_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    principal_id TEXT NOT NULL,
    role TEXT NOT NULL,
    surface TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_denials_seq ON access_denials (seq);
