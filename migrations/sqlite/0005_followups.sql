-- 0005: the follow-up workflow — workflow tasks and their acts.
-- Payloads are JSON text, exactly as with case summaries — one
-- contract across stores. Both tables are insert-only by design
-- (the store port grows no update or delete for them): a task
-- never changes in place; what happens to it arrives as an act,
-- and its current state is derived from the acts in code.
CREATE TABLE IF NOT EXISTS follow_up_requests (
    task_id TEXT PRIMARY KEY,
    case_id TEXT,
    report_id TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_follow_up_requests_case ON follow_up_requests (case_id);

CREATE TABLE IF NOT EXISTS follow_up_acts (
    act_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_follow_up_acts_task ON follow_up_acts (task_id);
