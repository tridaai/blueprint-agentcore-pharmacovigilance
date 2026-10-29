-- 0011: Round 12 — the model gateway's cost ledger.
-- Payloads are JSON text, exactly as with the earlier tables —
-- one contract across stores. The table is insert-only by
-- design (the store port grows no update or delete for it):
-- every model call the gateway judges lands here exactly
-- once, completed or refused, and a refusal is a record on
-- the same terms as a completion — the ledger is how a
-- refused call is surfaced. Costs are pack-stated
-- measurements derived from stored byte sizes, never
-- provider-billed figures.
CREATE TABLE IF NOT EXISTS model_calls (
    record_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_calls_case ON model_calls (case_id);
