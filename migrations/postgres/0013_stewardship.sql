-- 0013: Round 14 — dictionary stewardship.
-- Payloads are JSON text, exactly as with the earlier tables —
-- one contract across stores. All three artefacts are
-- insert-only by design (the store port grows no update or
-- delete for them): a worklist over a moved record is a NEW
-- worklist that supersedes the old one by link, a candidate
-- stands exactly as proposed, and a candidate's decision is
-- its own artefact — a decided candidate's state is derived
-- from the decision artefacts, never written back. A worklist
-- is aggregate-only — reported wordings, counts and dates, no
-- case ids, no patient or reporter field — and nothing here
-- is a regulatory submission.
CREATE TABLE IF NOT EXISTS steward_worklists (
    worklist_id TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steward_candidates (
    candidate_id TEXT PRIMARY KEY,
    worklist_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steward_candidates_worklist ON steward_candidates (worklist_id);
CREATE TABLE IF NOT EXISTS steward_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steward_decisions_candidate ON steward_decisions (candidate_id);
