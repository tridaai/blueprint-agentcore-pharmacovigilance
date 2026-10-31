# Changelog — Pharmacovigilance Adverse Event Case Agent (AWS Bedrock AgentCore)

## 1.0.0 — 2026-10-30

The blueprint at v1.0: the full twenty-round build, frozen by a
release-hardening pass (R20) that re-ran every gate on the
finished tree — the full test suite with the Postgres parity
leg live, the Postgres parity check, the eval gate, the
reports, model-ledger, signals, steward, console, draft-final
and red-team checks, and the demo end to end. The README and
architecture docs were audited against the code as shipped;
the drift found (the architecture mapping's Evaluations and
Observability rows still promising evaluator configs and
OpenTelemetry spans to a later infra round, its round map
carrying a shifted numbering for Rounds 10–11 and one round
still open, the README intro still framing de-duplication as
a Round 2 to come, the layout listing missing the release
artefacts) was corrected in the docs. The exact freeze
figures are recorded in `docs/release-1.0.0.md`.

The gaps that remain are stated in the README's Honest status
and are unchanged by this release: the dictionary is a
repo-built extract, not licensed MedDRA, and the reference
safety information is a repo-built stand-in, not the products'
real labels; no live AgentCore, Bedrock or Gateway call has
ever been made from this build — the Runtime entrypoint is
the real SDK app, verified over its HTTP contract, and the
Memory, Gateway and Policy services are ports with local
doubles behind them; live-model SAR drafting is stub-tested
until a working key exists; no SAR is ever filed and nothing
is submitted to a regulator, in any round; the online
evaluator configs and OpenTelemetry spans deferred to an
infra round are not in 1.0.0 — they remain deployment work;
and the Docker image build is reviewed statically rather than
run (no Docker daemon in the build sandbox). The README's
work-in-progress notice stays on: hands-on developer testing
and the companion blog post are still outstanding.

### The twenty rounds

- **R1 — Core.** The case/report domain and the validity gate
  in code (the four minimum criteria as a pure function;
  placeholder values never identify anyone); intake port and
  the planted Northbridge feed (3 products — valid clinician
  and patient reports, reports missing a reporter and a
  patient, a duplicate-looking pair); the hash-chained
  evidence store over an S3-shaped object port, with
  `verify_chain` detecting altered payloads; the store port
  (SQLite default, PostgreSQL dialect, migration 0001,
  JSON-text payloads in both dialects); the real
  `BedrockAgentCoreApp` Runtime entrypoint
  (`bedrock-agentcore==1.25.1`) plus a local in-process invoke
  harness; the agent step — a Strands-shaped assembler behind
  a model port whose offline profile composes the case
  summary from stored evidence, every statement citing an
  evidence item and field, the citation resolver refusing
  unresolvable citations wholesale, the `bedrock` profile
  refusing until configured; the FastAPI case API; and the
  eval gate with its first zero-tolerance counters.
- **R2 — De-duplication.** A versioned rule pack (2026.10.1)
  scoring every candidate pair on patient identity, product,
  event terms, onset dates and reporter — every point a named
  signal — with three gates (patient identity unproven, event
  terms unrelated, onset too far apart, plus a hard
  date-of-birth contradiction) fixing the band at *not a
  duplicate* regardless of score; bands definite / probable /
  not a duplicate. Links, never merges: a definite or
  probable finding routes to a named human queue; a confirm
  closes the later case as a duplicate by recorded act on its
  own evidence chain, a reject lets it proceed. Planted
  near-miss traps graded in tests and evals. Patient/product
  history views derived from stored artefacts only,
  content-addressed, identity conflicts shown never merged,
  consumed by the assembler; migration 0002.
- **R3 — Dictionary coding.** A terminology extract built for
  this repo (`pv_cases/coding.py`, dictionary 2026.10.1) —
  reporter verbatims to preferred terms under system organ
  classes, plus a product dictionary; a reduced, original
  extract, not licensed MedDRA. Coding as a lookup in code,
  never a guess: exact, normalised and synonym match paths in
  stated precedence, and deliberately no fuzzy path. Every
  coding a stored `CodingDecision` pinned to (case,
  dictionary version, verbatim as reported) with its match
  path; uncoded a first-class outcome (reason named,
  competing terms recorded when ambiguous) raising the
  coding-review queue, resolved by a named coder's manual
  decision — automatic coding never supersedes a human's.
  Additions are new dictionary versions only, refused when
  they would rewrite a mapping or manufacture an ambiguity.
  History views and summaries consume the coding layer; a
  summary presenting an uncoded term as coded is refused
  wholesale. Migration 0003.
- **R4 — Assessment.** The seriousness engine over a
  versioned criteria pack: the six ICH criteria each recorded
  fired, not fired or cannot assess, every non-silent verdict
  naming its deciding fact — silence is never read as safety.
  Expectedness against the reference safety information
  stand-in, with the specificity trap graded (an event coded
  more severe or specific than the labelled term is
  unexpected). Regulatory clocks computed in code from day
  zero — the first valid receipt date, which never moves:
  7-day expedited (serious + unexpected, death or
  life-threatening fired), 15-day expedited (other serious +
  unexpected), periodic otherwise, `awaiting_assessment`
  while either verdict is unassessable; the deadline date
  stored, clock state derived at read. `CaseAssessment`
  artefacts insert-only, superseding by link with the change
  recorded; a summary stating a verdict, obligation or
  deadline different from the current artefact refused in
  code. Migration 0004.
- **R5 — Follow-up workflow.** Requests raised in code from
  every named absence Rounds 1–4 produce — the validity
  gate's missing criteria, an uncoded event, the facts an
  assessment records as cannot assess — each item naming
  exactly what a follow-up report must supply, due dates
  priced by a versioned follow-up pack (the slowest class
  governs). Follow-up reports fold into the report of record
  under a stated merge rule and found cases through ordinary
  intake once valid, both reports on the new case's chain as
  provenance. Resolutions, rolled attempts and cap
  escalations (3 cycles) as insert-only acts, state derived
  in code; day zero unmoved, so a seriousness fact landing on
  day 20 reads breached out loud. Follow-up requests are
  internal task artefacts — nothing is sent to any reporter.
  Migration 0005.
- **R6 — The medical-reviewer gate.** Dual control in code:
  a medical reviewer and a safety reviewer, two distinct
  named people, must each accept; the same person attempting
  the second role is refused at recording time. Review acts
  are insert-only artefacts binding the current assessment,
  the current coding decisions and the assembled summary by
  id and hash, recorded on the evidence chain; staleness is
  derived from supersession, stale acts labelled and counting
  for nothing; a reject holds the case until the record it
  bound is superseded and a fresh dual review decides the new
  one. The PHI-minimised review queue — holds first, then
  cases one decision from done, then stale, then first
  reviews — carrying no patient or reporter identifier. The
  versioned review pack states the roles and rules.
  Migration 0006.
- **R7 — Sealing and export.** A named seal act taken only
  from `medically_reviewed`, binding the complete artefact
  inventory by content hash, the evidence chain's head hash
  and the pack versions in force; a case that is not
  medically reviewed cannot seal, the refusal naming the
  blocker. Post-seal immutability enforced at every writing
  service with the seal named, and no unseal surface anywhere
  — a correction is a new case. A byte-deterministic export
  package (`pv-case-export/1`) assembled only from the sealed
  inventory, carrying no export timestamp: the same sealed
  case exports byte-identical bytes and the same package
  hash, and every serving is recorded in the store and on the
  chain. An offline verifier re-performs the package from the
  file alone — every artefact hash, chain linkage from
  genesis to the seal's head, inventory exactness, the
  dual-control gate, the clock arithmetic — failing tampered
  packages with the broken checks named. Migration 0007.
- **R8 — Quarantine, Memory, Gateway + Policy.** The
  injection quarantine (Law 7 made mechanical): every
  free-text field entering a case screened in code at intake
  and in follow-ups against a versioned screening pack; a
  field with findings is held — the assembler's facts exclude
  it, so it reaches no summary statement and no model prompt
  — until a named reviewer's release act; the source payload
  is never edited; a sealed case's holds cannot be released.
  The AgentCore Memory port with a labelled local double:
  recall recorded as retrievals of references (case and
  artefact ids, coded facts, a fingerprinted subject), never
  copies and never merges, expiring at 365 days as tombstones
  under a versioned memory pack. The Gateway + Policy ports:
  the tool catalogue behind an enforcing gateway judged by a
  versioned policy pack whose pack of record is the Cedar
  artefact in `infra/` with its executable mirror in code
  (parity graded by test); first-match deny, default deny;
  denials as HTTP 200 + JSON-RPC −32002 with the denied tool
  never executing; every call recorded insert-only. Migration
  0008.
- **R9 — Red Team I.** A deliberate attack pass over every
  trust boundary Rounds 1–8 built (`docs/red-team.md`,
  `tests/test_redteam.py`): twenty-seven attacks, four real
  defects found and fixed — held text deciding verdicts (the
  assessment read held descriptions directly; a held field is
  now absent to the assessment too, and a release supersedes
  it), a follow-up re-identifying its case (contradicting
  hard identifiers now refused before anything lands), a
  backdated follow-up founding a case before first receipt
  (the founding receipt is bounded below by the original
  report's), and dual control falling to a name variant
  (reviewer identity compared under normalised folding in the
  service, the derived state and the offline verifier). The
  defences that held are kept as regression traps; eval depth
  added at the dedup band edges (54 / 55 / 79 / 80), the
  onset gate at 90 vs 91 days, clock states on the deadline
  day itself, a mid-case dictionary transition and a
  third-cycle follow-up resolution. Two findings documented
  open for the access round: quarantine release role
  separation, and pack integrity fingerprints. No migration.
- **R10 — Access control.** A versioned access pack judging
  every principal the surfaces carry — six roles with stated
  read surfaces and acts (intake processors, the safety
  scientist, the safety and medical reviewers, the quality
  auditor who reads everything and performs nothing, the case
  admin who tends reference data and reads no case files).
  Existence hiding on every case-scoped read: a role with no
  case scope is answered exactly as for a non-existent case,
  byte-identical across all sixteen surfaces; list filtering
  that never leaks a count. A carried principal binds the
  act's human — you cannot sign someone else's review,
  release or seal; dual control bound to two pack-authorised
  roles where a principal is carried. An insert-only denial
  log (migration 0009) records every refusal before it
  propagates. The red team's two open findings closed:
  quarantine release gains role separation and may never come
  from the held text's own reporter of record (RT-07), and
  every gateway call records the content fingerprint of the
  policy pack it was judged under (RT-15). A Postgres boot
  defect found by the round's parity leg — the migration
  ledger insert speaking SQLite's placeholder to psycopg, so
  the `DATABASE_URL` profile could never finish booting —
  fixed, speaking each dialect's own placeholder, with a
  regression test. Scope limit stated plainly: enforcement
  governs calls that carry a principal; principal-less calls
  remain the trusted local path.
- **R11 — Safety oversight reporting.** A versioned report
  pack (2026.10.1) computing the periodic view — intake by
  state, seriousness mix, clock compliance per obligation
  class, follow-up ageing, coding coverage, quarantine
  position, review queue position — only from artefacts,
  every rate carried as {value, numerator, denominator} and
  an empty population reading `no_cases`, never a bare 0% or
  100%. Reports are insert-only artefacts (migration 0010)
  stating window, as-of and record fingerprint, superseding
  by link over a moved record; the verified mark is set only
  when an independent re-performance from raw store reads
  agrees figure for figure (`scripts/check_reports.py` stands
  the check). Generation is the safety scientist's judged act
  under the access pack; payloads are aggregate-only — no
  case ids, no patient or reporter field, graded by a key
  scan. A report is an internal oversight artefact, never a
  regulatory submission, and says so on its face.
- **R12 — The governed model gateway.** Every summary call
  routed through one door under a versioned model pack
  (2026.10.1), landing on an insert-only per-case cost ledger
  (migration 0011): tokens derived from stored sizes by the
  pack's stated byte rule, costs folded from the pack's
  stated reference rates — pack-stated measurements, never
  provider-billed. Per-case budgets (25 calls, 2,500,000
  micro-USD) refuse before the port is touched, the refusal
  itself recorded; a hosted profile that is not configured
  refuses through the gateway as an error record — no silent
  fallback, ever. Minimisation judged at the door against the
  pack's direct-identifier lists, refusals naming fields and
  keys, never values; held text dropped on the gateway path
  itself. The ledger re-performs by a second path
  (`scripts/check_model_ledger.py` stands the check) and is
  read as a case surface under the access pack,
  existence-hidden like every case surface. Deliberately no
  oversight-report section: the Round 11 verifier grades
  stored reports against its pack's fixed section list.
- **R13 — Signal counts.** The disproportionality view over
  the coded book (migration 0012): every product–event pair's
  full 2x2 computed in code under a versioned signal pack
  (2026.10.1), the proportional reporting ratio and
  chi-square stated per pair so a flagged pair is recomputable
  by hand from its own row. Undefined measures stated, never
  faked — a pair with no comparator can never flag (the
  demo's book shows a chi-square of 12.1212 failing to flag on
  exactly this rule), an empty margin states an undefined
  chi-square; flags only at the pack's criteria (a ≥ 3,
  PRR ≥ 2, chi-square ≥ 4). Counting by distinct case over a
  duplicate-free population, with unattributed and uncoded
  counts on the table's face; per-product organ-class shares
  as rates with denominators. Tables insert-only, superseding
  by link, re-performed by a second fold before the verified
  mark (`scripts/check_signals.py`), aggregate-only with no
  case ids, generated as the safety scientist's named act. A
  flag is a prompt, not a finding — the table says so on its
  face.
- **R14 — Dictionary stewardship.** The aggregate worklist
  over the book's uncoded wordings (migration 0013): one
  ranked row per wording computed in code under a versioned
  steward pack (2026.10.1) — report and decision counts,
  first and last seen, the uncoded reason, the coded products
  the reports arrived with, and the wording's standing
  against the dictionary in force. Worklists are insert-only
  artefacts superseding by link, re-performed by a second
  fold before the verified mark (`scripts/check_steward.py`),
  aggregate-only with no case ids; striking one codes
  nothing, and it says so on its face. Candidates proposed
  against a worklist row and decided under dual control —
  the proposer may not decide — confirmation enacting the
  mapping as a new dictionary version through the coding
  layer's own addition, governing cases coded on or after the
  decision date while cases already coded keep the coding of
  record. The stewardship surface and its acts sit under the
  access pack.
- **R15 — The safety-reviewer console.** One composed read
  over the whole book (`pv_cases/console.py`; the static page
  at `GET /console` rendering only what `GET /console/data`
  returns): case rows with their verdicts and clock
  positions, the review queue, the follow-up board, the
  coding queue, the quarantine position, and the standing
  oversight, signal and stewardship artefacts read back,
  never recomputed — the composition computes no verdict and
  stores nothing, so it cannot disagree with the artefacts of
  record. The console is its own surface in the access pack,
  judged before anything is composed, and every section is
  additionally filtered by its own surface: a section a role
  cannot read arrives invisible, without even its count; the
  denial-log section is auditor-only.
  `scripts/check_console.py` re-performs every figure by a
  second route. No migration.
- **R16 — The Postgres production profile.** A standing
  parity harness (`pv_cases/parity.py`,
  `scripts/check_postgres.py`, `make postgres-check`): one
  deterministic core walk over the whole service graph — the
  demo's flows plus the standing aggregates — read back as a
  figure sheet of plain JSON, run on both stores and compared
  field for field, every divergence named by its path. All
  thirteen migrations stand on a real PostgreSQL database,
  the walk serves end to end there, its export passes the
  offline verifier, and the two sheets agree exactly (61,714
  canonical bytes, identical hashes at the round's freeze).
  The supported profile is stated: a UTF-8 database, refused
  by name otherwise; the walk runs in its own schema and
  drops it on success. `tests/test_postgres_parity.py` pins
  the walk on SQLite in the suite and on Postgres wherever a
  database is provided. No dialect divergence was found in
  the walk's reach — the harness, not a finding, is the
  deliverable.
- **R17 — Red Team II.** A deliberate attack pass over the
  surfaces Rounds 11–16 built (fourteen attacks, RT-28 to
  RT-41): one real defect fixed — the model gateway's
  minimisation door matched identifier field paths and keys
  by exact string, so `patient.DOB`, `Reporter.Name`,
  `dateOfBirth` and `date-of-birth` walked direct identifiers
  straight to the model port; the door now folds paths and
  keys before comparing (case and separator spellings are not
  identity) and names the spelling as carried in the refusal.
  The rest held, including the three aggregate artefacts
  swept for the book's identifiers at the level of values,
  the signal flag rule at its exact edges, a steward
  enactment attacked for rewriting history, and the parity
  comparator attacked with a divergence buried in one deeply
  nested scalar. Six zero-tolerance `redteam2_*` counters join
  the eval gate. Stated boundary: the door's contract is
  structural — identifier prose inside a free-text value is
  outside it, and no assembler fact carries narrative text.
  No open findings.
- **R18 — Draft-vs-final review measurement.** The design
  brief's eighth law — draft-vs-final diffs are recorded per
  case — the last of its ten laws without machinery. Per
  case, once the dual-control review completes, an
  insert-only measurement (`DFM-`, migration 0014) under the
  versioned draft-final pack (2026.10.1) records the distance
  between the first assembled narrative draft and the summary
  content the completing accept acts actually bound, taken
  from the acts' own bindings and checked against the bound
  content hash. The diff is statement-level (statement =
  text + citations, multiset matching): unchanged,
  citation-changed, dropped, added — carried as counts and
  statement hashes, never statement text; a reword reads as
  drop + add, stated on the pack's face. The assemblies
  walked and the distinct drafts among them distinguish a
  rubber stamp from a quiet re-assembly. The book's verbatim
  rate folds at read time as {value, numerator, denominator}
  (`no_cases` for an empty book); measurements over moved
  records supersede by link, standing being the head of the
  supersession chain; refusals by name (review not completed,
  its standing state named; review bound no summary); every
  measurement re-performed by an independent raw-store path
  before its verified mark. A measurement is an oversight
  artefact about a case, not part of it — striking one over a
  sealed case leaves the export package hash unmoved, graded
  in tests, evals and `scripts/check_draft_final.py`. The
  parity walk measures on both stores; the figure sheet grows
  to 63,543 canonical bytes, identical hashes. Six
  `draft_final_*` counters join the eval gate.
- **R19 — Red Team III.** The pre-release attack pass over
  what Rounds 12–18 built, above all Round 18's measurement
  (ten attacks, RT-42 to RT-51, traps in
  `tests/test_redteam3.py`, the record in
  `docs/red-team.md`): one real defect fixed — a
  measurement's identity was taken on trust (RT-42): the
  verifier re-performed its figures only, the pack it named,
  the id it carried and the supersedes link it asserted were
  never graded, and standing was a date order over stored
  rows, so a forged row with a flipped verbatim flag and a
  later date displaced the genuine standing measurement and
  poisoned the book's roll-up. The verifier now grades
  identity alongside figures — the pack must be one the
  system holds and the one re-performance runs under, the id
  must be the content address recomputed over the artefact's
  own stated fields, the link must name a held same-case
  measurement on a chain that reaches a root; the case's
  eligible artefacts must form the single chain `measure()`
  builds, and standing requires that chain — a severed or
  forked set has no standing measurement. Eight attacks held;
  one boundary stated rather than fixed (RT-50 — a stored
  payload replaced wholesale under a correctly recomputed
  content address is identity-valid by construction; only the
  figure re-performance names it — the same trust line every
  artefact in the system stands on: insert-only is a service
  discipline, not a storage guarantee). Three `redteam3_*`
  counters join the eval gate, and `scripts/check_red_team.py`
  stands the identity defences as a check. No migration; no
  open findings.
- **R20 — Release hardening.** No new features. Version
  1.0.0 set in the package metadata, the package
  `__version__` and the lockfile root entry; this changelog
  and `docs/release-1.0.0.md` written from the round sections
  and the gate logs; the README and architecture docs audited
  against the code as shipped, with the drift corrected in
  the sweep commit (the architecture mapping's deferred rows
  now state that evaluator configs and OpenTelemetry spans
  are not in 1.0.0, its round map records the shipped
  numbering through R20 and closes, the intro's
  de-duplication line and the layout listing brought
  current); and every gate re-run on the finished tree, the
  figures recorded in the release notes. The README's
  work-in-progress notice stays on — hands-on developer
  testing and the companion blog post are still outstanding,
  and the notice comes off when both are done, not before.
