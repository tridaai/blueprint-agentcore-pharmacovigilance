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
| **Memory** | Short-term per session; long-term semantic + episodic strategies for patient/product history feeding case assembly | **Port + local double (Round 8).** `MemoryPort` with the labelled local double; recall is recorded as retrievals of references under a versioned pack with a 365-day retention rule — AgentCore Memory is the production path behind the port |
| **Gateway** | Safety-database, product-dictionary and literature sources as MCP targets; every production tool call traverses it | **Port wired (Round 8).** The case record, patient history and dictionary tools sit behind the enforcing `GatewayService`; an AgentCore Gateway target is the production transport behind the same invoke contract |
| **Policy** | Cedar pack (role × tool × PHI caps) in ENFORCE mode on the Gateway; denials arrive as HTTP 200 + JSON-RPC −32002 and must be detected, recorded, surfaced | **Wired (Round 8).** Pack of record at `infra/policies/pv-gateway-policy.cedar` with its executable mirror in code (parity graded by test); denials are constructed as the −32002 envelope in one place, recorded insert-only, and the denied tool never executes |
| **Identity** | Inbound caller identity; outbound OAuth/API-key vault via `@requires_access_token` / `@requires_api_key` shapes | **Wired locally (Rounds 8 + 10).** Gateway calls carry a named principal + pack role, judged before any tool runs; and the case surfaces carry a principal judged by the versioned access pack — role-scoped reads, existence hiding, act gating, a recorded denial log. AgentCore Identity is the production terminator in front of the same contract; outbound vault shapes land with the AWS account integration |
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
10. (R6) A case is medically reviewed only by dual control:
    current accept acts from both reviewer roles (medical,
    safety), recorded by two distinct named people — the same
    person holding a current act in the other role is refused
    at recording time. Each act binds the current assessment,
    the current coding decisions and the latest summary by id
    and hash (the summary by content hash — its id is a
    per-assembly sequence number, not a version). Staleness is
    derived: a superseded artefact stales every act bound to
    it, and stale acts count for nothing while staying on the
    trail, labelled, with the superseding artefacts named. A
    current reject holds the case until the record it bound is
    superseded; the rejecting act is never edited. Review acts
    are insert-only, recorded on the evidence chain, and the
    review queue's entries carry no patient- or
    reporter-identifying field by construction.
11. (R7) A case file closes only by sealing, and only from
    `medically_reviewed`: a named sealer's act binds the full
    artefact inventory (every artefact id + content hash, by
    kind — including every evidence item by its chain hash),
    the evidence-chain head at seal time, the review bindings
    the gate was satisfied over, and the pack versions in
    force. From that moment the case is a closed record: every
    writing service refuses new artefacts for it with the seal
    named, and no unseal surface exists — a correction is a new
    case. The export package is assembled only from the sealed
    inventory and is byte-deterministic (it carries no export
    timestamp); each serving is recorded in the store and on
    the evidence chain with the hash of the exact bytes served.
    The offline verifier re-performs the package from the
    package alone — artefact hashes, chain linkage, inventory
    exactness, the two-distinct-people review gate over the
    sealed bindings, and the clock arithmetic — and reports the
    hash it computed over the package as presented, never a
    hash the package claims; a full re-hash forgery is anchored
    against only by the export record held outside the package.
    Sealing and export produce a reviewable case file, never a
    submission: nothing leaves for a regulator from this system.
12. (R8) Free text is screened before it can be drafted from:
    every report narrative and event description is screened in
    code against the versioned screening pack the moment its
    payload lands on a case's chain (intake and follow-up
    alike). A field with findings is held — excluded from the
    assembler's facts, which are the only road from source text
    to a summary statement or a model prompt — under a
    content-addressed, insert-only quarantine record naming the
    rules that fired; the source payload is never edited. A
    hold ends only by a named reviewer's release act with a
    rationale, and never on a sealed case.
13. (R8) Cross-case recall is recorded and never merges: a
    retrieval through the Memory port names its strategy, its
    requesting case and its matches by reference (case ids,
    artefact ids, coded facts), carries its subject as a sha256
    fingerprint rather than the key, and writes nothing to any
    source case — the only chain note is on the requesting
    case, which is why a sealed requesting case refuses recall.
    Retrievals expire under the memory pack's retention rule as
    tombstones: kept, counted, never served, never deleted.
14. (R8) Tool calls are policy-judged before they run: the
    gateway evaluates the versioned policy pack (first-match
    deny, default deny) over (principal, role, tool, PHI
    family), records every call insert-only with the deciding
    rule before anything executes, and answers a denial as the
    platform does — HTTP 200 with a JSON-RPC −32002 envelope —
    with the denied tool never invoked. The pack of record is
    the Cedar artefact in `infra/`; its executable mirror in
    code is graded to carry the same rule ids, roles and
    catalogue.
15. (R9) A quarantine hold binds every reader of the field,
    not just the drafter: the assessment engine reads event
    descriptions as deciding facts, so a held description is
    treated as absent there too — the criterion it alone would
    have decided goes `cannot_assess`, the held set is part of
    the assessment's input fingerprint, and a release
    supersedes the assessment before the text decides as data.
    A follow-up amends facts but cannot re-identify its case:
    a date of birth or reporter-held patient id contradicting
    the record refuses the submission before anything lands.
    A founded case's day zero is the date its report became
    valid, bounded below by the original report's receipt.
    Reviewer identity — for dual control at recording, in the
    derived review state, and in the offline verifier — is
    compared under `normalise_text` folding, so a name variant
    is the same person. The full attack list, verdicts and the
    two open findings live in `docs/red-team.md`.
16. (R10) Who is calling is judged before what they may see or
    do: the surfaces carry a principal (id + role) judged
    against the versioned access pack — per-role read surfaces
    and acts, judged as carried, with no default principal.
    A case read by a role with no case scope is answered
    exactly as a non-existent case, on every case-scoped
    surface; list surfaces filter rather than leak counts.
    A carried principal binds the human its act names, and
    dual control is two pack-authorised roles as well as two
    distinct identities. Every refusal is recorded in the
    insert-only denial log before it propagates. Enforcement
    governs calls that carry a principal; principal-less calls
    are the trusted local path. A quarantine release belongs
    to the pack's judging roles and never to the held text's
    own reporter of record where the record names one
    (RT-07, closed); and every gateway call records a content
    fingerprint of the policy pack it was judged under, so an
    in-place rule edit without a version bump is detectable
    after the fact (RT-15, closed).
17. (R11) Oversight reports are artefacts, not views. A
    periodic safety report is computed only from the record's
    artefacts under a versioned pack, states its window, its
    as-of and the fingerprint of the record it was computed
    over, and is stored insert-only: a moved record yields a
    superseding report linked to the one it replaces, never
    an edit, and an unmoved record returns the standing
    artefact. Every rate carries its denominator on the
    figure and an empty population reads `no_cases`, never a
    percentage. The verified mark is earned by independent
    re-performance — a second path over raw store reads,
    with its own supersession walks — and refused on any
    single mismatch. Reports are aggregate-only: no case ids
    and no patient or reporter field exist in the payload
    shape, graded by a key scan on every artefact; a clock
    counts as met only by a case standing medically reviewed
    at its deadline. Generation is a judged act under the
    access pack; reads belong to the pack's named report
    roles. A report is an internal oversight artefact, never
    a regulatory submission, and states so on its face.
18. (R12) Every model call passes one governed door. The
    model gateway wraps the configured profile and the
    assembler's one call routes through it: minimisation is
    judged at the door against the model pack's
    direct-identifier lists (a payload carrying one is
    refused whole, on the record, naming fields and keys but
    never values; paths and keys are compared folded —
    case and separator spellings are not identity — since
    Round 17, whose red team found the exact-spelling match
    waving variants like `patient.DOB` and `dateOfBirth`
    through), held text is dropped on the gateway path
    itself, and the pack's per-case budgets refuse before the
    port is touched — the refusal itself a ledger record. A
    hosted profile that is not configured refuses through
    the gateway and propagates; nothing falls back. Every
    judged call lands once on the insert-only per-case cost
    ledger (migration 0011): sizes measured, tokens derived
    by the pack's stated byte rule, cost folded from the
    pack's reference rates — pack-stated measurements, never
    provider-billed — and the ledger re-performs by a second
    path that re-derives every figure and names any record
    that does not recompute. The ledger is a case-read
    surface under the access pack, existence-hidden like
    every case surface. The oversight pack is deliberately
    not extended with a model-usage section: its verifier
    grades stored reports against a fixed section list, and
    a new section would fail every already-struck report's
    re-performance — so the ledger keeps its own surface and
    its own check.
19. (R13) The book is counted against itself, in code. A
    signal table states every product–event pair's 2x2 over
    the window's population — cases received in the window,
    duplicates and duplicate-linked cases excluded, counting
    by distinct case — with the proportional reporting
    ratio and chi-square computed from the four cells under
    the signal pack, and flags only against the pack's
    stated criteria. Attribution is by the standing coded
    product decision alone: an uncoded product is counted
    on the table's face as unattributed and enters no pair,
    and an uncoded event enters no pair. Where a measure is
    undefined the table states why — `no_comparator` when a
    term was reported with one product only (such a pair
    can never flag, however large its chi-square), and
    `undefined` for a chi-square on an empty margin. Tables
    are insert-only (migration 0012), content-addressed,
    superseding by link, and earn their verified mark only
    from a second fold over the raw store that regroups
    every decision by case and walks supersession by hand.
    Aggregate-only, like the oversight reports: no case id
    and no patient or reporter field exists in the payload
    shape, graded by the same key scan. Generation is the
    safety scientist's judged act under the access pack. A
    flag is a prompt for human signal evaluation — never a
    finding — and the table states so on its face.
20. (R14) The uncoded page is governed in the aggregate,
    and the dictionary grows only under dual control. A
    steward worklist folds the book's standing uncoded
    decisions into one ranked row per reported wording —
    report and decision counts, first/last seen, the
    reason, and the coded products the reports arrived
    with under the same standing-decision attribution as
    the signal counts — struck against a named dictionary
    version, whose state each row also states (`codes_now`
    where the dictionary has since learned the wording;
    the decisions of record stand regardless). Worklists
    are insert-only (migration 0013), content-addressed,
    superseding by link, and earn their verified mark only
    from a second, global fold over the raw store.
    Striking one codes nothing. A candidate mapping is
    proposed against a worklist row as its own insert-only
    artefact, and is decided by a second named steward —
    the proposer may not decide, under the repo's text
    folding — the decision itself an artefact from which
    the candidate's state is derived. Confirmation enacts
    the mapping through the coding layer's own
    collision-checked addition, minting a new dictionary
    version that names the candidate; the version governs
    cases coded on or after the decision date, and no
    stored decision is ever rewritten. An ambiguous
    wording takes no candidate — the dictionary cannot
    take a recorded synonym of two terms as a new entry —
    and is settled per case by a coder's manual
    resolution. Aggregate-only, like the signal tables:
    rows carry the reported wording (the artefact's
    subject), counts, dates and coded product labels — no
    case id, no patient or reporter field — graded by the
    same key scan. The stewardship surface and its acts
    sit under the access pack: read by the case-working
    roles, the auditor and the case admin; worked by the
    safety scientist and the case admin.
21. (R18) The gate's own editing is measured, per case.
    Once a case's dual-control review completes, the
    distance between the narrative the reviewers were
    first given and the summary content their completing
    accepts bound is itself an insert-only artefact
    (migration 0014), content-addressed over the two
    summary hashes, the record's fingerprint and the
    draft-final pack version. The final is read from the
    acts' own bindings — the last completing act's bound
    summary id, checked against the bound content hash —
    never from recency. The diff is statement-level under
    the pack's stated rules (text plus citations, matched
    as a multiset; a reword reads as a drop plus an add)
    and is carried as counts and statement hashes, never
    statement text. An unmoved record re-measures to the
    standing artefact; a moved one supersedes by link,
    and the standing measurement is the chain's head. A
    case whose review has not completed, or whose review
    bound no summary, is refused by name. The artefact is
    oversight, not casework: it writes nothing to the
    case and stands outside every seal's inventory, so a
    sealed case's export hash is unmoved by it. Its act
    and reads sit under the access pack on their own
    surface; the book's verbatim-approval rate folds from
    the standing measurements with its denominator stated.

15. **The safety-reviewer console (Round 15)** is the
    composition layer the design brief's stack named from
    the start — a single-file page over one composed read
    (`pv_cases/console.py`). It introduces no artefact, no
    table and no verdict: it reads the case list with the
    current assessments and the clock board's derived
    states, the review queue, the follow-up board, the
    coding queue, the quarantine fold, and the standing
    heads of the oversight, signal and stewardship chains,
    and its headline counts are counted from the rows it
    composes. The console joined the access pack as its
    own surface under the standing version (fingerprint-
    separated, as Rounds 11–14 joined): entry is judged on
    it, and each section is additionally filtered by its
    own surface — a section a role cannot read arrives
    invisible, without rows or counts. The page itself is
    a static shell that renders only the data surface's
    answer; the check script re-performs every figure by
    a second route and grades the shell for baked-in data.

16. **Dialect parity (Round 16)** is the production-profile
    gate. One scripted walk (`pv_cases/parity.py`) runs the
    demo's core path — intake, validity, dedup, coding,
    clocks, dual review, seal, export, and the later
    rounds' beats (quarantine, gateway, model ledger,
    oversight, signals, stewardship, console, denial log) —
    against a store and reads the result back as a figure
    sheet of plain JSON values, extracted from the stored
    artefacts rather than the services' returns. The check
    (`scripts/check_postgres.py`) verifies the server is
    the supported profile (UTF-8), that the migration
    ledger on Postgres lists exactly the dialect's files,
    and that the Postgres sheet agrees with the SQLite
    sheet field for field, divergences named by path. The
    walk runs in a throwaway schema of the provided
    database. It found no divergence in the current code —
    its standing value is that the next one cannot ship
    silently, the way the Round 10 placeholder defect
    nearly did.

17. **Draft-vs-final measurement (Round 18)** closes the
    design brief's law list: every one of its ten laws now
    has machinery. The measurement (`pv_cases/draft_final.py`)
    composes the review layer's own records — the stored
    summaries and the acts that bound them — and adds one
    table of its own; the diff rules travel as a versioned
    pack in the repo's idiom, so a future change to what
    counts as "the same statement" is a new pack version,
    and measurements struck under different versions stay
    distinguishable by the version they pin and by the
    record fingerprint in their address. The parity walk
    strikes two measurements (one verbatim, one changed)
    and folds the book roll-up, so the surface is graded
    on both dialects like every surface before it; the
    eval gate grades it over its own planted book,
    including the boundary that a measurement struck over
    a sealed record moves nothing the seal covers.

18. **A measurement's identity is graded, not assumed
    (Round 19)** — the third red team's one defect
    (RT-42) was that the verifier re-performed a
    measurement's figures while taking its pack, its id
    and its supersedes link on trust, and standing was a
    date order over whatever rows the store held: the
    oversight artefact could be displaced by a row that
    was never struck. The invariant now: an artefact is
    believed as a chain link only when its id is the
    content address over its own stated fields, its pack
    is one the system holds, and its link names a held
    measurement of the same case; a case's measurements
    form exactly one such chain or the case has no
    standing measurement; and the verifier grades those
    identity claims with the same naming discipline as
    the figures. What identity cannot grade — a
    replacement payload that recomputes its own address —
    is named by the figure re-performance, and that
    boundary is stated in the red-team record (RT-50)
    rather than smoothed over.

## Round map

The full 20-round map lives in the design brief
(`blueprint-17-agentcore-pharmacovigilance-design.md` in the goal
files). As shipped, the rounds ran: foundation (R1),
de-duplication (R2), coding dictionary (R3), seriousness +
expectedness (R4), follow-up workflow (R5), medical-reviewer
gate (R6), sealing/export (R7), the AgentCore Policy (Cedar)
pack (R8), the first red team (R9), injection quarantine
(R10), access control with existence hiding (R11), the
governed model gateway and its cost ledger (R12), signal
counts (R13), dictionary stewardship (R14), the
safety-reviewer console (R15), the Postgres profile and the
parity harness (R16), the second red team (R17),
draft-vs-final review measurement (R18) and the third
red team (R19). One round remains on the map: release
hardening (R20).
