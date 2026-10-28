<p align="center">
  <img src="https://trida.ai/logos-rounded/trida-rounded.png" alt="Trida AI" width="96">
</p>

# Blueprint — Pharmacovigilance Adverse Event Case Agent (AWS Bedrock AgentCore)

A working prototype of the agent a drug-safety team needs between
its intake channels and its medical reviewers: adverse-event reports
arrive from patients, clinicians, call centres and literature, and
the agent **passes each one through a validity gate in code,
assembles the valid ones into evidence-backed case files, and
stops at the medical-reviewer gate**: two named reviewers — a
medical reviewer and a safety reviewer, never the same person —
must each accept the assembled record before the case counts as
reviewed. The system ends at an approved, exportable case file —
**it never submits anything to a regulator or a safety database
of record**.

The blueprint's thesis is the **AWS Bedrock AgentCore-native path**:
the agent runs behind the real `BedrockAgentCoreApp` from the
`bedrock-agentcore` SDK (the Runtime contract — `POST /invocations`,
`GET /ping`, port 8080, ARM64 container), its case work is a
Strands-shaped agent step behind a model port (offline deterministic
profile by default; a Bedrock profile that refuses loudly until an
AWS account is wired), and the AgentCore services it will grow into
— Memory, Gateway, Policy, Identity, Evaluations — are mapped
honestly in `docs/architecture.md`, as ports and local doubles where
they are not yet live.

The promise is precise, and it is enforced in code, not in prompts:

- **Validity before everything.** A case exists only when the four
  minimum criteria hold — identifiable patient, identifiable
  reporter, suspect product, adverse event. A report missing any of
  them becomes a follow-up task naming what is missing. It is never
  a case.
- **Every summary statement carries a citation** to a specific
  evidence item and field, and a citation that does not resolve is
  refused in code — the whole summary, not just the sentence.
- **Evidence is immutable and hash-chained** per case: each item's
  hash covers its payload and its predecessor, so altered history
  fails verification mechanically.
- **Possible duplicates are flagged, never merged.** Two reports
  sharing the exact mechanical key (patient + product + event +
  onset) are stored as two cases with the suspicion recorded;
  de-duplication with human review is Round 2.

This is an independent Trida AI blueprint built around a fictional
sponsor, Northbridge Therapeutics. It is **not affiliated with or
endorsed by Northbridge, Amazon Web Services, or any regulator**;
it shows how a forward-deployed team would put a pharmacovigilance
case agent into production on AgentCore inside a drug-safety
programme.

> **Work in progress** — this blueprint is built and passing its automated test and eval gates, but it has not yet finished hands-on local testing, and its companion blog post is not yet published. Expect changes. This notice comes off when both are done.

---

## What Round 1 builds

- **Case/report domain + the validity gate** (`pv_cases/validity.py`):
  the four minimum criteria as a pure, deterministic function;
  placeholder values ("unknown", "n/a") never identify anyone.
- **Intake port + reference feed**: a planted feed for the fictional
  Northbridge Therapeutics (3 products) — valid clinician and
  patient reports, a report with no identifiable reporter, one with
  no identifiable patient, and a duplicate-looking pair sharing an
  exact key.
- **Hash-chained evidence store** per case over an S3-shaped object
  port (local directory double; Amazon S3 in production), with a
  `verify_chain` that detects altered payloads.
- **Store port**: SQLite local default (`./data/pv.db`), PostgreSQL
  dialect behind the same port (`DATABASE_URL`), numbered migration
  `0001` + idempotent runner; payloads are JSON text in both
  dialects.
- **AgentCore Runtime entrypoint** (`pv_cases/runtime_app.py`): the
  real `BedrockAgentCoreApp` from `bedrock-agentcore==1.25.1`
  (installed and import-verified in the build sandbox), with
  `assemble_summary` and `ingest_and_assemble` invocation actions,
  plus `pv-agent-invoke`, a local harness that runs the same handler
  in-process for offline development.
- **The agent step**: a Strands-shaped assembler behind a model
  port. The offline profile composes the case summary from stored
  evidence; every statement cites an evidence item + field; the
  citation resolver refuses unresolvable citations wholesale. The
  `bedrock` profile exists and refuses until configured — no silent
  fallback anywhere.
- **Case API** (FastAPI): intake, cases, follow-up queue, evidence,
  chain verification, summary assembly, `/health` + `/readiness`.
- **Eval gate** (`evals/run_evals.py`): zero-tolerance counters —
  invalid reports becoming cases, missing valid cases, missing
  follow-ups, missed duplicate suspicion, uncited statements,
  unresolvable citations, chain failures.

## What Round 2 adds

- **Real de-duplication** (`pv_cases/dedup.py`), replacing the
  exact-key suspicion flag: a versioned rule pack (`2026.10.1`,
  data in code) scores every candidate pair on patient identity,
  product, event terms, onset dates and reporter — deterministic
  scoring with every point a named signal — and three gates
  (patient identity unproven, event terms unrelated, onset too far
  apart, plus a hard date-of-birth contradiction) fix the band at
  *not a duplicate* regardless of score. Bands: definite /
  probable / not a duplicate.
- **Links, never merges.** A definite or probable finding creates
  a link between the two cases and routes it to a named human
  queue; the later case is marked `duplicate_linked` and stays
  fully readable. A named human confirms — the later case closes
  as a duplicate, by recorded act on its own evidence chain — or
  rejects, and the case proceeds. There is no merge and no delete
  anywhere: each report's provenance is itself evidence.
- **Planted near-miss traps**, graded in tests and evals: same
  patient + product with a different event (a genuine second
  case), a namesake whose date of birth contradicts the match, the
  same event recurring a year later, and a probable pair that only
  a human may settle.
- **Patient/product history views** (`pv_cases/history.py`):
  derived in code from stored artefacts only, content-addressed
  (rebuilding over unchanged inputs returns the stored view),
  with identity facts carrying their source case and **conflicts
  shown, never merged** — two ages on record both stand. The R1
  assembler consumes a built patient view: it is captured on the
  case's evidence chain and its counts enter the summary as cited
  statements, under the same citation discipline as everything
  else.
- **API + CLI parity** for dedup candidates, the decision queue,
  link confirm/reject, and patient/product history views;
  migration `0002` in both dialects.

## What Round 3 adds

- **A terminology extract, built for this repo** (`pv_cases/coding.py`):
  a versioned dictionary artefact (`2026.10.1`) of reporter
  verbatims → preferred terms grouped under system organ classes,
  plus a product dictionary (reported name → coded product and
  substance). It is a reduced, original extract written for this
  blueprint — **not licensed MedDRA**, and nothing here connects
  to the real dictionary.
- **Coding as a lookup in code, never a guess**: three match
  paths in stated precedence — exact (the verbatim as reported),
  normalised (case/punctuation folding), synonym (a recorded
  synonym of the entry). There is deliberately **no fuzzy or
  string-similarity path**: a verbatim that merely resembles a
  preferred term is not coded to it. Every coding is a stored
  `CodingDecision` pinned to (case, dictionary version, verbatim
  as reported) with the match path recorded.
- **Uncoded is a first-class outcome**: a verbatim with no entry
  stays uncoded — verbatim preserved, reason named
  (`no_dictionary_entry`, or `ambiguous_match` with the competing
  terms recorded when a verbatim genuinely maps to two) — and
  raises a task in the coding-review queue. A named human coder
  resolves it by choosing a term that exists in the dictionary;
  the resolution is a new manual decision superseding the uncoded
  one by link, recorded on the case's evidence chain. Automatic
  coding never supersedes a human's decision.
- **Dictionary additions are new versions, never edits**: a coder
  may propose an addition, and it takes effect only as a new
  dictionary version. Dictionary versions and coding decisions
  are insert-only artefacts in the store — there is no update or
  delete for them — so a decision always means what it meant
  under the version it pinned. Additions that would rewrite an
  existing mapping or manufacture an ambiguity are refused.
- **Consumers**: the history views count events by preferred
  term where coded, with uncoded verbatims counted separately and
  named; the assembler captures each case's coding on its
  evidence chain and the summary presents coded terms with their
  labels and uncoded terms as uncoded — a summary that presents
  an uncoded term as coded is refused in code, wholesale, on the
  same terms as an unresolvable citation.
- **Planted coding fixtures**, graded in tests and evals: one
  case per match path, the exact-beats-synonym precedence case,
  a near-miss that stays uncoded, an ambiguous synonym only a
  coder may settle, and an uncoded product name.
- **API + CLI parity** for coding reads, the uncoded queue,
  coder resolutions, recodes, dictionary version reads and
  additions; migration `0003` in both dialects.

## What Round 4 adds

- **A seriousness engine over a versioned criteria pack**
  (`pv_cases/assessment.py`): the six ICH seriousness criteria —
  death, life-threatening, hospitalisation, disability,
  congenital anomaly, other medically important — evaluated in
  code against the case's coded facts and outcome fields. Every
  criterion is recorded **fired**, **not fired**, or
  **cannot assess**, and each non-silent verdict names the fact
  that decided it. A criterion whose deciding fact is absent is
  `cannot_assess` naming the missing fact — **silence is never
  read as safety**, so a case with any unassessable criterion and
  none fired is itself `cannot_assess`, never "non-serious".
- **Expectedness against reference safety information**: a
  versioned RSI extract, per product, of the labelled event
  terms at their stated specificity. **The RSI is a repo-built
  stand-in for the products' real labels** — a small original
  extract written for this blueprint, not a real label and not
  licensed data. A coded event is `expected` when it matches a
  labelled term, and `unexpected` when it is not in the label —
  **including the classic trap**: an event coded to a term more
  severe or more specific than the labelled term (a vesicular
  rash, where the label lists "Rash") is unexpected even though
  its base term is labelled. An uncoded event, or an event on an
  unidentified product, is `cannot_assess`, named.
- **Regulatory clocks, computed in code**: from the case's day
  zero — **the first valid receipt date, which never moves** —
  plus seriousness and expectedness, a versioned clock pack
  computes the obligation class: 7-day expedited for serious +
  unexpected with death or life-threatening fired, 15-day
  expedited for other serious + unexpected, periodic otherwise,
  and `awaiting_assessment` while either verdict is unassessable
  (no obligation is computed from unassessed facts). The
  deadline **date** is computed and stored on the artefact;
  clock *state* (within clock / due today / breached, days
  remaining) is derived at read time from an explicit `as_of`,
  never stored.
- **Assessments are stored artefacts, insert-only**: each
  `CaseAssessment` is pinned to the case, the report fingerprint
  it read, the current coding decision ids, and the three pack
  versions (criteria, RSI, clock). Re-assessing over unchanged
  inputs writes nothing; when a case's facts move — a coder
  settles an uncoded term, say — the new assessment **supersedes
  by link** with the change recorded (verdicts and obligation,
  before → after) and day zero unchanged. Nothing is ever
  submitted to a regulator: the clock states an obligation; the
  sponsor's regulated process discharges it.
- **Consumers**: intake assesses every case after coding; the
  assembler captures the current assessment on the evidence
  chain and the summary presents seriousness, expectedness and
  the clock with citations to the deciding facts — a summary
  stating a verdict, obligation class or deadline different
  from the current artefact is **refused in code**, on the same
  wholesale terms as an unresolvable citation.
- **Planted assessment fixtures**, graded in tests and evals:
  one case per criterion shape, the specificity trap, uncoded
  and unidentified-product `cannot_assess` cases, and clock
  fixtures graded to the day (day zero + 7 / + 15, breach
  detection at an `as_of` past the deadline).
- **API + CLI parity** for the per-case assessment read and
  trail, the re-assessment act, the clock board and the RSI
  reads; migration `0004` in both dialects.

## What Round 5 adds

- **Follow-up requests, raised in code** (`pv_cases/followup.py`):
  every named absence Rounds 1–4 produce becomes tracked work.
  The validity gate's missing criteria, an uncoded event, and the
  facts an assessment records as *cannot assess* each raise a
  request whose items name **exactly** what a follow-up report
  must supply — the field, and what would satisfy it (the
  reporter's name/email/phone; the verbatim as reported and its
  uncoded reason; `events.0.outcome`, blocking the death,
  hospitalisation and disability criteria). Absences the coding
  workflow already owns raise no second request. A request is
  priced a due date by a **versioned follow-up pack** (days per
  missing class — a missing reporter is 14 days, a missing
  patient identity or a codable wording 30 — the slowest class
  governs) and belongs to a case, or to the report that will
  become one.
- **Follow-up reports extend the case — never a parallel one.**
  An answer is a new intake linked to the request: for an
  existing case it joins the case's evidence chain and folds
  into the **report of record** (`pv_cases/reporting.py`) under a
  stated merge rule (a follow-up amends facts; it does not
  re-identity or re-date the case), and the case is recoded and
  reassessed over the folded facts. For a validity request the
  follow-up merges cumulatively over the original report, and
  the merged report, once valid, **founds the case** through the
  ordinary intake pipeline, with the original and the follow-up
  both on its chain as provenance.
- **Cycles and escalation, recorded as acts.** Requests and
  their acts are insert-only artefacts; state is derived, never
  written back. An answer that lands the named fact records a
  **resolution** (a validity resolution names the case it
  founded); one that does not records an **attempt** and rolls
  the due date into the next cycle; an attempt failing at the
  pack's cycle cap (3) records an **escalation** — the request
  stands in the named `escalated` state on the board, takes no
  further reports, and is never silently dropped or duplicated
  by a later raising scan.
- **Day zero does not move — and the breach is stated.** A
  seriousness fact landing on day 20 supersedes the assessment
  with the change recorded, on a 7-day clock from the original
  day zero whose deadline is already past: the clock board reads
  **breached**, and the case summary states the real deadline.
  A follow-up that matches *another* case under the dedup rule
  pack links its own case to that case through the usual human
  queue — flagged, never merged.
- **Surfaces**: the follow-up board (escalated first, then
  most-overdue — ageing derived at read time, like the clocks),
  per-case requests, the request detail with its acts, and the
  follow-up intake in the API and CLI; the demo walks the
  day-20 case end to end. Migration `0005` in both dialects.
  Follow-up requests are internal task artefacts — nothing is
  sent to any reporter, and nothing is ever submitted to a
  regulator.

## What Round 6 adds

- **The medical-reviewer gate — dual control, in code**
  (`pv_cases/review.py`): everything before this round prepares
  the case; this round is where named humans own it. A case is
  `medically_reviewed` only when two reviewers — one in the
  **medical reviewer** role, one in the **safety reviewer**
  role, **two distinct people** — have each recorded an
  accepting decision. The same person attempting the second
  role is refused at recording time, with the refusal named;
  one role's accept alone leaves the case visibly
  `awaiting_second_review` on every read.
- **Review acts bind the record they decided**: each act is an
  insert-only artefact (stored, and recorded on the case's
  evidence chain) naming the reviewer, role, decision and
  rationale, and binding the exact artefact versions reviewed —
  the current assessment, the current coding decisions, and the
  assembled summary — by id and hash. (A summary's version is
  its content hash: summary ids are per-assembly sequence
  numbers, so identical text re-assembled is the same
  narrative; changed text is a new one.)
- **Staleness is derived, never declared**: when a bound
  artefact is superseded — a follow-up folds new facts in, a
  coder settles a term — every act bound to the old version
  stops counting toward the gate. The acts stay on the trail,
  labelled stale with the superseding artefacts named; the
  case falls back to awaiting review. Nothing is deleted and
  no act is ever edited.
- **A reject holds the case**: while a current reject stands,
  the case is in `review_hold` with the reason on its face —
  an accept over the same record does not clear it. The hold
  clears the way drug safety actually clears it: the record
  changes, the stale reject falls away with the version it
  bound, and a fresh dual review decides the new record.
- **The review queue**: holds first, then cases one decision
  from done, then stale reviews, then first reviews — each
  entry carrying the case id, its seriousness, expectedness,
  clock position and review state, and **nothing that
  identifies the patient or the reporter**. PHI minimisation
  is structural, in the entry model itself. A versioned
  **review pack** (`2026.10.1`) states the roles, the
  decisions, and the completion / disagreement / staleness
  rules every act is recorded under.
- **Surfaces**: the pack, the queue, the per-case review
  position and trail, and the act intake in the API and CLI;
  the demo walks a dual review, a hold, and the hold's
  clearance by a landed follow-up. Migration `0006` in both
  dialects. `medically_reviewed` is an internal state of this
  system — it is not a submission, and nothing is ever
  submitted to a regulator.

## What Round 7 adds

- **Case sealing — the file closes under a named act**
  (`pv_cases/sealing.py`): once a case stands
  `medically_reviewed`, a named sealer can seal its file. The
  seal artefact binds the case's **complete artefact
  inventory** — every artefact id with its content hash, by
  kind: the case, its coding decisions with their dictionary
  versions, its assessments, its summaries, the review acts,
  the follow-up requests and their acts, and every evidence
  item by its chain hash — plus the evidence chain's **head
  hash at seal time**, the dual-control review bindings the
  gate was satisfied over, and the pack versions in force. A
  case that is not medically reviewed — awaiting a role, on a
  review hold, or stale after a post-review supersession —
  cannot be sealed, and the refusal names the blocker. A case
  seals once. Rules live in a versioned **sealing pack**
  (`2026.10.1`).
- **A sealed case is a closed record**: after sealing, every
  writing service refuses new artefacts for the case —
  follow-ups, recodes, coder resolutions, new assessments,
  new summaries, new review acts — with the seal named in the
  refusal. There is **no unseal surface anywhere**: if a sealed
  case ever needs correcting, the correction is a new case.
  That is deliberate, and it is how regulated case files work.
- **A deterministic export package, assembled only from the
  seal**: a single JSON document (`pv-case-export/1`) carrying
  exactly the sealed inventory — case facts as recorded (the
  report of record with its follow-ups folded in), coding
  decisions, assessments with their clock states, review acts,
  follow-up history, evidence payloads, and the seal itself —
  plus a plain-text rendering. The package carries no export
  timestamp: exporting the same sealed case twice yields
  **byte-identical bytes and the same package hash**.
- **Every serving is recorded**: an export is itself an
  artefact — who exported, when, and the hash of the exact
  bytes served, recorded in the store and on the case's
  evidence chain. There is no export button for an unsealed
  case; the attempt is refused, naming the seal requirement.
- **An offline verifier that re-performs the package**
  (`pv_cases/verify.py`, also `pv-agent-verify-export`): it
  takes only a package file — no database, no network — and
  checks, each named: the package's shape; **every artefact
  hash** recomputing from the payloads carried; the **evidence
  chain linkage** re-linking from the genesis hash to the
  seal's head; the **seal inventory** matching the package
  exactly, nothing missing, nothing extra; the **review
  gate** — two distinct people, one per role, accepting over
  bindings that resolve to the sealed artefacts at their bound
  hashes; and the **clock arithmetic** re-performing from the
  sealed facts (day zero, obligation class, deadline). Tamper
  with an altered figure, a dropped artefact, swapped review
  acts or a relinked chain and the verdict fails with the
  broken checks named. One honest limit: a package is
  self-contained, so a forger who recomputes *every* hash —
  including the seal's — defeats package-only checks; that is
  what the export record's hash, held in the store and on the
  chain, anchors against. The verifier reports the hash it
  computed over the package as presented, never one the
  package claims.
- **Surfaces**: seal / seal-read / export / export-list /
  text-export / verify in the API, `seal`, `seal-case`,
  `export-case`, `verify-export` and `sealing-pack` in the
  CLI, and a demo that seals the reviewed case, refuses a
  further artefact in the seal's name, serves the package
  twice byte-identically, passes it through the verifier check
  by check, and fails a package whose clock deadline was
  altered. Migration `0007` in both dialects. And still: **the
  package is a reviewable case file, not a submission —
  nothing is ever submitted to a regulator.**

## What Round 8 adds

- **Injection quarantine — Law 7 made mechanical**
  (`pv_cases/quarantine.py`): every free-text field that enters
  a case — a report's narrative and its events' descriptions,
  at intake and in every follow-up report — is screened in code
  against a versioned **screening pack** (`2026.10.1`:
  instruction directives, system-channel and role claims,
  verdict directives aimed at the case's own seriousness or
  closure, regulator-authority impersonation, long encoded
  runs). A field with findings earns a **quarantine record** —
  content-addressed over (case, evidence item, field, text
  hash), insert-only, recorded on the chain, naming every rule
  that fired with the matched span — and is **held**: the
  assembler's facts exclude it, so the text reaches neither a
  summary statement nor any model profile's prompt. The source
  payload is never edited; it stands on the chain as reported.
  A hold ends only by a **named reviewer's release act** with a
  stated rationale, after which the field is usable again as
  data — quoted, cited, never obeyed. A sealed case's holds
  cannot be released. The benign planted book earns no records:
  screening's false positives are an eval counter, at zero.
- **The AgentCore Memory port** (`pv_cases/memory.py`): a
  `MemoryPort` with a labelled local double — AgentCore Memory
  is the production path — and a **memory pack** (`2026.10.1`)
  stating the strategies (same patient, same product + coded
  term, coding precedent), the minimisation rule and the
  retention rule. A recall is a recorded **retrieval**: the
  matches are *references* — case ids, the artefact ids that
  evidence the match, the coded facts it rests on — never a
  copy of another case's facts, never a patient or reporter
  field; the subject is carried as a sha256 fingerprint, not
  the key. Recall **never merges**: neither case's report or
  chain changes, beyond the retrieval's own note on the
  requesting case's chain. Retrievals expire at the pack's 365
  days as **tombstones** — kept, counted, never served, never
  deleted. A sealed case refuses recall: its file is closed.
- **The Gateway + Policy ports** (`pv_cases/gateway.py`,
  `infra/policies/pv-gateway-policy.cedar`): the tool catalogue
  (case record, patient history, dictionary lookup — each with
  its PHI family) sits behind an enforcing gateway judged by a
  versioned **policy pack** (`2026.10.1`), whose pack of record
  is the Cedar artefact in `infra/`; a test grades that the
  artefact and the executable pack carry exactly the same rule
  ids. Evaluation is first-match deny, default deny: unnamed
  principals, unknown roles and tools, the external auditor
  reaching for PHI, and cross-case patient history for any role
  but the medical reviewer are denied before the permit list is
  consulted. A denial is the platform's own shape — **HTTP 200
  carrying a JSON-RPC error, code −32002**, never a 403 — and
  the denied tool **never executes** (graded with a counting
  double). Every call, allow or deny, is recorded insert-only
  with the rule that decided it: the record is how a denial is
  surfaced, since the wire answer is a bland 200.
- **Surfaces**: quarantine views + release, memory recall +
  retrievals + retention + coding precedents, and gateway
  pack / tools / invoke / calls in the API; `quarantine`,
  `release-quarantine`, `memory-recall`, `memory-retrievals`,
  `memory-retention`, `memory-precedents`, `gateway-invoke`,
  `gateway-calls` and the three pack reads in the CLI; and a
  demo that holds a planted instruction out of a summary,
  releases it under a named act, recalls the duplicate pair's
  other half by reference, and watches an auditor's call denied
  in the platform's shape. Migration `0008` in both dialects.
  And still: **nothing is ever submitted to a regulator.**

## What Round 9 adds

- **The red team** (`docs/red-team.md`, `tests/test_redteam.py`):
  a deliberate attack pass over every trust boundary Rounds
  1–8 built — reporter and follow-up free text, dictionary
  versions, memory recall, gateway/policy decisions, the
  seal/export artefacts, the clocks and the dual-control gate.
  Twenty-seven attacks, each with the defence it should meet
  and a verdict. Four were **real defects**, fixed in this
  round and kept as regression tests plus zero-tolerance eval
  counters:
  - **Held text decided verdicts (RT-01).** The quarantine
    held a field out of the assembler's facts — but the
    assessment engine reads event *descriptions* directly as
    deciding facts, so a held description carrying "admitted
    to hospital… resuscitated", plus an instruction, fired two
    seriousness criteria and judged the case serious. Now a
    held description is **absent to the assessment** too (the
    criterion goes `cannot_assess` where that text was the
    deciding fact), the held set rides in the assessment's
    input fingerprint, and a release supersedes the assessment
    — day zero unmoved — before the released text decides as
    data.
  - **A follow-up could re-identify its case (RT-23).** The
    merge lets a follow-up's present fields win, so a report
    about a *different patient* silently rewrote the case's
    date of birth and patient id — and the patient key,
    history and recall followed. A follow-up whose hard
    identifiers contradict the record's is now **refused
    before anything lands**, with the field named; completing
    a missing identity is unaffected.
  - **A backdated follow-up moved day zero before first
    receipt (RT-20).** A validity follow-up claiming a receipt
    date earlier than the original report's founded a case
    whose clock started before the sponsor had received
    anything. The founding receipt is now the completion date
    **bounded below by the original report's receipt**.
  - **Dual control fell to a name variant (RT-25).** "Dr A.
    Rao" holding the medical accept, "dr a rao" could record
    the safety accept — reviewer identity was a raw string.
    Identity is now compared under the repo's
    `normalise_text` folding at recording time, in the derived
    review state, and in the offline verifier's review gate.
  The rest held — memory poisoning, tombstone serving,
  denial-envelope confusion, mixed-inventory export forgery,
  sealing over a standing hold, re-judging a sealed case
  under a newer dictionary — and are graded as traps. Two
  findings are documented open in `docs/red-team.md`:
  quarantine release has no role separation (there is no role
  registry to check against yet — the access-control work),
  and pack integrity rests on the packs being code (a per-call
  content fingerprint is noted for release hardening).
- **Eval depth**: the gate now grades the boundary values it
  previously only approached — dedup pairs scoring exactly
  **54 / 55 / 79 / 80** against the probable/definite bands,
  the onset gate at **90 vs 91 days**, the event-overlap gate
  at exactly 0.5; `clock_state` on the deadline day itself
  (`due_today`), the day before and the day after
  (`breached`, −1); the 7-day clock taken exactly on a
  fatal/life-threatening criterion; a **dictionary transition
  mid-case** (automatic decisions migrate to the new version
  on recode, a human's manual decision stays pinned to its
  version, the assessment supersedes); and a follow-up that
  resolves on its **third cycle** — the cap escalates failed
  attempts, not late successes. Eight new zero-tolerance
  counters, all at zero. No migration this round — the fixes
  are behaviour at existing boundaries, not new artefacts.
  And still: **nothing is ever submitted to a regulator.**

## What Round 10 adds

- **Access control + existence hiding** (`src/pv_cases/access.py`,
  migration 0009): until this round every surface took actor
  strings on trust and a case id was a capability — anyone who
  could guess or learn one could read the file. The surfaces
  now carry a **principal** (an id + a role: the
  `X-Principal-Id` / `X-Principal-Role` headers on the API, a
  `principal` object on a Runtime payload, a `principal=`
  argument on the act-recording services), judged in code
  against a **versioned access pack** (2026.10.1):
  - **Six roles, stated scopes.** *Intake processors* submit
    reports and follow-ups and read their work queue — never
    case files. The *safety scientist* works the book (coding
    resolutions, dedup decisions, follow-up answers, releases,
    recall). The *safety reviewer* and *medical reviewer* hold
    the review acts — the medical reviewer also seals and
    exports. The *quality auditor* reads **everything**,
    including the denial log, and performs **nothing**. The
    *case admin* tends reference data (dictionary additions)
    and reads the system record — not case files. Administering
    the dictionary is not a licence to read patients' cases.
  - **Existence hiding.** A principal whose role has no case
    scope gets, for any case id on every case-scoped read
    surface, exactly the response a non-existent case earns —
    byte for byte; list surfaces filter to empty rather than
    leak a count. A case id cannot be probed for existence by
    anyone the pack does not entitle to see it. Graded across
    all sixteen case-scoped surfaces.
  - **A carried principal binds the act's human.** Being
    authenticated as yourself does not let you sign someone
    else's review, release or seal: the principal's id must be
    the actor the act names, compared under the same
    `normalise_text` folding dual control uses.
  - **Dual control is role-bound.** A review act's role *is* a
    pack role, so where a principal is carried, the medical
    accept must come from a principal holding the medical
    reviewer role and the safety accept from a second principal
    holding the safety role — two distinct normalised
    identities (Round 9) *and* two pack-authorised roles.
  - **Every refusal is an artefact.** Denials land in an
    insert-only denial log (migration 0009) *before* the error
    propagates — principal, role, surface, target, reason, pack
    version — and the log is itself a guarded surface (auditor
    + case admin). A denial over a hidden case records only the
    id the requester supplied; nothing in it says whether the
    case exists.
  - **Scope limit, stated plainly:** enforcement governs calls
    that *carry* a principal. Calls carrying none — the CLI,
    the local harness, the pre-R10 test paths — remain the
    trusted local operations path, exactly as since Round 1.
    In a deployment, AgentCore Identity terminates caller
    identity in front of these surfaces and every call presents
    a principal.
- **RT-07 closed** (the red team's medium open finding):
  quarantine release had no role separation because no role
  registry existed to check against. The release now belongs to
  the safety scientist and the two review roles — not intake,
  not admin, not the read-only auditor — and a release may not
  come from the held text's **own reporter of record** where
  the record names one (the report payload on the chain names
  its reporter; a releaser folding to that name is refused,
  principal-carried or not). Where the report names no
  reporter, the record cannot tell, and the check does not
  fire — the limit is stated in the code, not papered over.
- **RT-15 closed** (the low open finding): every gateway call
  record now carries a **content fingerprint** of the policy
  pack it was judged under — sha256 over the pack's canonical
  JSON — so an in-place rule edit without a version bump is
  detectable after the fact by recomputing and comparing. The
  recorded `pack_version` remains the pack's self-declaration;
  the fingerprint is its content. Seven new zero-tolerance
  eval counters grade the whole layer: existence leaks,
  cross-scope reads, denied acts executing, wrong-role and
  self releases, unrecorded denials, fingerprint mismatches.
  With both findings closed, `docs/red-team.md` carries no
  open findings. And still: **nothing is ever submitted to a
  regulator.**
- **Postgres profile boot fix** (found by this round's parity
  leg): the migration runner's ledger insert spoke SQLite's
  `?` placeholder to psycopg connections, so a
  `DATABASE_URL` deployment could never finish booting — the
  store's own queries translate placeholders, the raw migration
  connection had no translator. The runner now speaks each
  dialect's own placeholder, with a regression test; the full
  service graph was exercised on the Postgres dialect for the
  parity leg (migrations 0001–0009, the planted feed, the
  denial log and gateway fingerprints round-tripped through a
  reopened store).

## Quick start

```bash
make install     # uv sync --extra dev
make demo        # the planted feed end to end, offline
make test        # pytest
make evals       # the eval gate
make api         # case API on http://localhost:8000
make serve       # AgentCore Runtime app on http://localhost:8080
make invoke      # local in-process invoke of the agent step
```

No AWS account, keys or network are needed for any of the above:
the default model profile is offline and deterministic.

## Honest status

- **Round 10 of 20.** Built and gate-green: everything in
  Rounds 1–9, plus access control — a versioned access pack
  judging every principal the surfaces carry (six roles with
  stated read surfaces and acts), existence hiding on every
  case-scoped read (a role with no case scope is answered
  exactly as for a non-existent case, on all sixteen surfaces),
  list filtering that never leaks a count, act gating with the
  principal bound to the human its act names, dual control
  bound to two pack-authorised roles where a principal is
  carried, and an insert-only denial log (migration 0009) that
  records every refusal before it propagates. The round closes
  the red team's two open findings: quarantine release has
  role separation now, and never from the held text's own
  reporter of record where the record names one (RT-07); and
  every gateway call records a content fingerprint of the
  policy pack it was judged under, so in-place pack edits are
  detectable after the fact (RT-15). Scope limit, stated
  plainly: enforcement governs calls that carry a principal;
  principal-less calls remain the trusted local path, as they
  have been since Round 1. Round 9 shipped: the red team (docs/red-team.md): twenty-seven
  attacks over the earlier rounds' trust boundaries, four real
  defects found and fixed — held text no longer decides
  verdicts (a held description is absent to the assessment
  until released, and the release supersedes it), follow-ups
  can no longer re-identify a case (contradicting hard
  identifiers are refused before anything lands), a backdated
  validity follow-up can no longer found a case before first
  receipt, and dual control is judged on normalised reviewer
  identity in the service and the offline verifier alike —
  with the defences that held (memory poisoning, tombstones,
  denial envelopes, mixed inventories, dictionary re-judging)
  kept as regression traps and zero-tolerance eval counters,
  and the gate deepened at the dedup band, clock and
  dictionary-transition boundaries. Round 8 shipped: the injection quarantine (report text screened in
  code at intake and follow-up against a versioned pack; held
  fields reach no fact, summary or prompt until a named
  reviewer's release act; sealed holds stay held), the
  AgentCore Memory port (recall recorded as retrievals of
  references — case and artefact ids, coded facts, a
  fingerprinted subject — never merges, expiring as tombstones
  under a 365-day retention pack), and the Gateway + Policy
  ports (a Cedar pack of record in `infra/` with its executable
  mirror in code, first-match/default deny, denials as HTTP 200
  + JSON-RPC −32002 with the denied tool never executing, every
  call recorded); migration 0008, and API/CLI parity for all of
  it. Round 7 shipped: case sealing and export — a named seal act taken
  only from `medically_reviewed`, binding the full artefact
  inventory by content hash, the evidence-chain head and the
  pack versions in force; post-seal immutability enforced at
  every writing service with the seal named and no unseal
  surface anywhere; a byte-deterministic export package
  assembled only from the sealed inventory, with every serving
  recorded in the store and on the evidence chain; and an
  offline verifier that re-performs the package's hashes,
  chain linkage, inventory exactness, dual-control review gate
  and clock arithmetic from the package alone, failing tampered
  packages with the broken checks named; migration 0007, and
  API/CLI parity for all of it. Round 6 shipped: the
  medical-reviewer gate — dual control enforced in
  code (two roles, two distinct named people, the same-person
  second role refused at recording time), review acts as
  insert-only artefacts binding the assessment, coding decisions
  and summary by id and hash and recorded on the evidence chain,
  staleness derived from supersession with stale acts labelled
  and counted for nothing, rejects holding a case until the
  record they bound is superseded and a fresh dual review
  decides the new one, the PHI-minimised review queue, the
  versioned review pack, migration 0006, and API/CLI parity for
  all of it. Round 5 shipped: the follow-up workflow — requests raised in code from
  the gate's, dictionary's and assessment engine's named
  absences, each item naming the exact field a follow-up report
  must supply, due dates priced by the versioned follow-up pack;
  follow-up reports folding into the report of record and
  founding cases through ordinary intake; resolutions, rolled
  attempts and cap escalations as insert-only acts with state
  derived in code; day zero unmoved with breaches stated out
  loud; migration 0005, and API/CLI parity for all of it.
  Round 4 shipped: the assessment layer — the versioned
  seriousness criteria pack with
  cannot-assess-never-assumed-negative semantics, expectedness
  against the RSI stand-in with the specificity trap graded, the
  clock pack computing obligation classes and deadline dates
  from an unmoving day zero, insert-only `CaseAssessment`
  artefacts superseding by link with the change recorded,
  summaries presenting the verdicts under a code-enforced
  presentation check, migration 0004, and API/CLI parity for all
  of it. Round 3 shipped: dictionary
  coding — the tiered lookup with recorded match paths, pinned
  `CodingDecision` artefacts, the uncoded review queue with named
  coder resolutions, dictionary additions as new versions only,
  history views and summaries consuming the coding layer,
  migration 0003. Round 2 shipped: the dedup rule pack with
  human link decisions, patient and product history views
  consumed by the assembler, migration 0002. Round 1 shipped:
  models + validity gate, intake + feed, evidence chain, store +
  migration 0001, Runtime entrypoint, agent step with cited
  summaries, API, demo, evals.
- **The dictionary is a repo-built extract, not licensed
  MedDRA** — a reduced, original terminology written for this
  blueprint (12 event entries, 3 product entries at version
  `2026.10.1`). Coding against the real dictionary is a licensing
  and integration step this blueprint deliberately does not fake.
- **The reference safety information is a repo-built stand-in**
  — a small original extract written for this blueprint (three
  products at version `2026.10.1`), **not the products' real
  labels**. Expectedness against a real label is a licensing and
  integration step this blueprint deliberately does not fake.
  Causality assessment is not part of this blueprint's verdicts;
  seriousness and expectedness are the computed judgements.
- **No regulator submission ever** — not in this round, not in any
  round. The system ends at an approved, exportable case file; what
  a sponsor does with it is the sponsor's regulated process.
- **Live AWS calls are stub-tested until an account is wired**: the
  `bedrock` model profile refuses loudly without region + model id,
  and the AgentCore Memory / Gateway / Policy surfaces (Round 8)
  are ports with local doubles — the port contracts, the policy
  enforcement and the retrieval records are real and graded; the
  AWS-side services behind them are the account-integration step.
  The Runtime entrypoint itself
  is the real SDK app, verified over its HTTP contract in tests.
- **Docker image build was not run** in the build sandbox (no
  daemon); the Dockerfile (multi-stage, non-root, ARM64) and the
  compose profile are graded statically by the test suite.

## Layout

```
src/pv_cases/     models, validity gate, intake, evidence, store,
                  agent, Runtime entrypoint, case API, demo, CLI
migrations/       numbered SQL migrations (sqlite/ and postgres/)
evals/            the deterministic eval gate
tests/            pytest suite incl. the Runtime contract tests
docs/             architecture.md — the AgentCore mapping, honestly
Dockerfile        ARM64 Runtime image (port 8080)
docker-compose.yml  Postgres+pgvector, SeaweedFS S3 (service: minio)
```

Apache 2.0. Built by Trida AI as an open blueprint for
forward-deployed agent engineering.
