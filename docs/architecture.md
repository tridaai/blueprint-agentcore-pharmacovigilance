# Architecture — Pharmacovigilance Case Agent on Bedrock AgentCore

## The shape

```
 intake sources ──► IntakePort ──► validity gate (code) ──┬─► Case ──► evidence chain
 (portal, call      (reference                            └─► FollowUpTask
  centre, feed        feed in R1)                                │
  literature)                                                    ▼
                                          agent step (Strands-shaped,
                                          model port) ──► cited CaseSummary
                                                ▲
                          AgentCore Runtime entrypoint (BedrockAgentCoreApp)
                          POST /invocations · GET /ping · port 8080
```

The domain core (models, validity, evidence, store) has no AWS in
it at all. AWS enters at exactly two edges in R1: the Runtime
entrypoint (the real SDK) and the model port's `bedrock` profile
(refusing until configured). Everything else AgentCore is a port
with a local double, scheduled in the round map — this document
says, per service, what is real today and what is a double.

## AgentCore mapping — honest status

| AgentCore service | Production role in this blueprint | R1 status |
|---|---|---|
| **Runtime** | Hosts the agent: ARM64 container, `POST /invocations` + `GET /ping` on 8080; one session = one case-working session | **Real.** `BedrockAgentCoreApp` from `bedrock-agentcore==1.25.1`; contract exercised over ASGI transport in `tests/test_runtime_contract.py`; `app.run(port=8080)` is the container entrypoint |
| **Memory** | Short-term per session; long-term semantic + episodic strategies for patient/product history feeding case assembly | **Port + local double (Round 11).** R1 assembly reads the case's own evidence only |
| **Gateway** | Safety-database, product-dictionary and literature sources as MCP targets; every production tool call traverses it | **Not wired (Round 12).** R1 tools are in-process evidence reads |
| **Policy** | Cedar pack (role × tool × PHI caps) in ENFORCE mode on the Gateway; denials arrive as HTTP 200 + JSON-RPC −32002 and must be detected, recorded, surfaced | **Not wired (Round 12).** The denial-shape rule is recorded here so the port is designed for it from the start |
| **Identity** | Inbound caller identity; outbound OAuth/API-key vault via `@requires_access_token` / `@requires_api_key` shapes | **Not wired (Round 12).** No outbound calls exist in R1 |
| **Evaluations** | Online evaluators (correctness, faithfulness, harm) sampled on live traffic | **Config later (Round 20 infra).** The repo's own deterministic eval gate (`evals/run_evals.py`) is primary and already binding |
| **Observability** | OpenTelemetry spans per case step; CloudWatch GenAI dashboards | **Partial.** Structured JSON logs with session/case ids (`pv_cases/logging_config.py`); spans land with the infra round |

## The agent framework choice

Strands Agents SDK is the AWS-native framework and this blueprint's
production agent loop: `Agent(model=BedrockModel(...), tools=[...])`
wrapped by the Runtime entrypoint, the Agent lazy-initialised per
session (actor/session ids exist only in invoke headers). R1's
assembler keeps that shape — system-prompt-shaped composition, plain
functions as tools (`read_case_facts`), a model behind a port — with
a deterministic offline profile implementing the port, so the whole
pipeline is graded offline. LangGraph is deliberately **not** used
here: the thesis is the AgentCore-native path, and the portfolio's
other blueprints already cover the LangGraph pattern.

## Data and stores

- **Case store** behind one port: SQLite local default
  (`./data/pv.db`), PostgreSQL when `DATABASE_URL` is set
  (Aurora PostgreSQL / RDS in production; `pgvector/pgvector:pg16`
  in compose). Numbered migrations per dialect under `migrations/`,
  applied by an idempotent runner; payloads stored as JSON text in
  both dialects.
- **Evidence objects** behind an S3-shaped port: raw report payloads
  and (later) source documents. Local double = directory tree;
  production = Amazon S3 with versioning. The hash chain's records
  live in the case store; the payloads live in the object store; the
  chain covers both (payload digest is in the chained hash).
- **PHI posture**: patient data in R1 is fictional. The design rule
  (Law 6) is minimisation by role-based visibility, enforced in the
  access-control round (R17) in this API and in the Cedar pack for
  Gateway calls (R12) — policy as a platform artefact, not a prompt.

## Invariants the code holds today

1. The validity gate is the only route from report to case
   (`IntakeService.ingest` consults it before any case write).
2. A summary exists only if every statement's citations resolve
   against stored evidence payloads (`CitationResolver`).
3. Evidence is append-only; verification recomputes every payload
   digest and link (`EvidenceChain.verify_chain`).
4. Duplicate suspicion is exact-key and mechanical in R1; it flags
   and never merges.
5. (R2) A duplicate is a scored assessment plus a human decision:
   the rule pack's bands create links, named humans confirm or
   reject them, and closed-as-duplicate cases stay readable with
   their chains intact. No merge or delete path exists.
6. (R2) History views are derived from stored artefacts only and
   content-addressed; identity conflicts are shown, never merged.
7. (R3) Coding is a versioned dictionary lookup with provenance:
   decisions pin (case, dictionary version, verbatim as reported)
   and record the match path (exact / normalised / synonym /
   manual); there is no fuzzy path. Uncoded terms are named
   blockers with a human review queue, and a summary presenting
   an uncoded term as coded is refused in code. Dictionary
   versions and coding decisions are insert-only artefacts — a
   dictionary change is a new version, never an edit.
8. (R4) Seriousness, expectedness and the reporting clocks are
   computed in code from versioned packs, never narrated by the
   model: criteria fire, do not fire, or cannot be assessed
   (absence is never read as negative); expectedness grades
   against the RSI stand-in with the specificity rule stated;
   the clock's day zero is the first valid receipt date and
   never moves. Assessments are insert-only artefacts pinned to
   the coding decisions they read; a changed case supersedes by
   link with the change recorded, and a summary misstating the
   current artefact's verdicts or deadline is refused in code.
9. (R5) A deficiency a follow-up report can cure is a tracked
   request, raised in code from the gate's, dictionary's and
   assessment engine's own outputs — never a generic "more info
   needed": each item names the field and what would satisfy it,
   and the due date is the follow-up pack's price for the
   slowest class. Follow-up reports extend the case they answer
   (the report of record is the intake payload folded with the
   chain's follow-up payloads under a stated merge rule; a
   validity follow-up founds its case through the same intake
   pipeline) — there is no parallel-case path. Requests and
   acts are insert-only; state, cycle and ageing are derived
   from the acts in code; an attempt failing at the pack's
   cycle cap escalates to a named state rather than expiring,
   and decided requests take no further reports.

## Round map

The full 20-round map lives in the design brief
(`blueprint-17-agentcore-pharmacovigilance-design.md` in the goal
files): de-duplication (R2), coding dictionary (R3), seriousness +
expectedness (R4), follow-up workflow (R5), medical-reviewer gate
(R6), sealing/export (R7), clocks (R8–R9), quarantine (R10), Memory
(R11), Gateway + Policy (R12), model gateway + cost ledger (R13),
console (R14), traps (R15), Postgres profile + prod smoke (R16),
access control (R17), signal counts (R18), red team (R19), release
hardening + infra (R20).
