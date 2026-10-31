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
  beyond the exact key, the de-duplication rule pack (Round 2)
  scores candidate pairs and routes them to a named human
  queue, whose decision links the cases — it never merges
  them.

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

## What Round 11 adds

- **Safety oversight reporting** (`src/pv_cases/oversight.py`,
  migration 0010): until this round the system could work a
  case end to end but could not answer the periodic questions
  a safety function is actually run by — how much came in,
  how the seriousness mix moved, whether the expedited clocks
  were met, how old the follow-up queue is, how much of the
  dictionary coding is automatic, what the quarantine is
  holding, and how deep the review queue stands. Round 11
  adds that view as an artefact discipline, not a dashboard
  over mutable state.
- **A versioned report pack** (`2026.10.1`) states, per
  section, the population, the window rule, and the measures:
  intake volume by state, seriousness mix, clock compliance
  per obligation class (due in window / met on time /
  completed late / breached and still open / open within the
  clock, with a case counting as met only when it stands
  medically reviewed by its deadline), follow-up ageing by
  cycle, dictionary coding coverage, quarantine position, and
  review queue position. Figures are computed **only from
  artefacts** — cases, tasks, decisions of record,
  assessments, review acts of record, follow-up states,
  quarantine records — never from mutable summaries.
- **Every rate carries its denominator on the figure**: a
  rate is `{value, numerator, denominator}`; counts carry
  nulls in the rate fields by construction. A class with no
  cases in window reads `no_cases` — a report never prints 0%
  or 100% over an empty population.
- **Reports are insert-only artefacts**. A report states its
  window, its as-of, who generated it, the pack version, and
  the fingerprint of the record it was computed over.
  Re-running the same window over a moved record produces a
  **superseding** report linked to the one it replaces; the
  old report stands byte-identical. Re-running over an
  unmoved record returns the standing artefact.
- **Independent re-performance**: `OversightService.verify`
  recomputes every figure from raw store reads by a second
  path — its own supersession walks, its own review-state
  derivation — and the report's `verified` mark is set at
  generation only when the two paths agree.
  `scripts/check_reports.py` (or `make reports-check`) runs
  the same check over a fresh service graph and refuses a
  tampered figure by name.
- **Access**: generation is the safety scientist's named act
  under the Round 10 pack (`generate_oversight_report`,
  judged, denials recorded); report reads belong to the
  safety scientist, the safety reviewer, the medical
  reviewer, and the quality auditor — the auditor reads every
  report and generates none. Intake and case admin have no
  report surface.
- **Aggregate-only, stated**: the pack takes the safer branch
  of the drill-down choice — **no case ids appear anywhere in
  a report**, and no patient or reporter field exists in the
  payload shape; a key scan grades that on every artefact.
  Drill-down stays where it already lives: the case surfaces,
  under the roles that may read cases.
- The demo's closing beat generates the October 2026 report
  over the planted book: the expedited breaches stand open in
  the figures — the report shows them; it does not soften
  them. A report is an internal oversight artefact. It is
  **never a regulatory submission**, and the artefact says so
  on its face. Seven new zero-tolerance eval counters grade
  the layer; the full service graph was exercised on the
  Postgres dialect for the parity leg (migrations 0001–0010,
  the planted feed, a verified report read back byte-equal
  through a reopened store).

## What Round 12 adds

- **A governed model gateway** (`src/pv_cases/model_gateway.py`,
  migration 0011): the Round 8 Gateway fronts tools; until
  this round nothing fronted the model itself. The
  assembler's one call — the summary proposal — now routes
  through a single door that wraps the configured profile,
  and every judged call lands on a per-case cost ledger.
- **A versioned model pack** (`2026.10.1`) states the
  measurement rules: tokens are canonical bytes ÷ 4,
  ceiling; rates are integer micro-dollars per token per
  profile (offline 0/0 — it makes no provider call; bedrock
  3 in / 15 out, a stated reference). **Costs are
  pack-stated measurements, never provider-billed** — the
  ledger exists so a case's model spend can be budgeted,
  compared and re-performed, not to imitate an invoice.
- **Budgets refuse before the port is touched**: a per-case
  call budget (25) and cost budget (2,500,000 micro-USD).
  A call that would be made after either is spent is
  refused, the refusal is itself a ledger record naming the
  budget it hit, and the model is never invoked. A hosted
  profile that is not configured refuses through the
  gateway as an error record and propagates — there is no
  silent fallback to the offline composer, ever.
- **PHI minimisation is judged at the door**: the
  assembler's facts are the case's minimised form by
  construction, and the gateway does not trust the
  construction — it scans every payload for the pack's
  direct-identifier field paths and identifier keys at any
  depth, and refuses the call whole when one appears. The
  refusal record names fields and keys, never values. Held
  (quarantined) text is also dropped on the gateway path
  itself, before measurement and before the port.
- **The ledger re-performs**: `verify_ledger` re-derives
  every record's tokens and cost from its stored sizes and
  re-folds each case's totals by a second accumulation; a
  tampered record is named. `scripts/check_model_ledger.py`
  (or `make model-ledger-check`) runs the same
  reconciliation from raw store rows and grades a planted
  tamper plus both refusals. Ledger reads are a case
  surface under the Round 10 access pack (`model_ledger`):
  the case-working roles and the quality auditor read a
  case's ledger; intake and case admin are answered exactly
  as for a non-existent case, with the denial recorded.
- **Deliberately not in the oversight report**: Round 11's
  verifier grades a stored report against the pack's fixed
  section list, so adding a model-usage section would fail
  every already-struck report's re-performance. The ledger
  keeps its own guarded surface and its own check instead.

## What Round 13 adds

- **Signal counts** (`src/pv_cases/signals.py`, migration
  0012): until this round the system could report on its
  process but never on its data — which product–event pairs
  the coded book reports disproportionately was a question
  no surface could answer. A signal table answers it for a
  window, in code, over the standing coded decisions.
- **Every pair states its full 2x2** — reports with the
  product and the term (a), with the product and not the
  term (b), with the term and not the product (c), with
  neither (d) — plus the two measures the pack's criteria
  use: the proportional reporting ratio and the chi-square
  of the 2x2. A flagged pair is recomputable by hand from
  its own row. Counting is by distinct case, and cases
  closed as duplicates or duplicate-linked are out of the
  population — the primary case carries the report.
- **Undefined measures are stated, never faked**: a term
  reported with one product only has *no comparator* — its
  ratio is undefined whatever its chi-square (the demo's
  book shows a chi-square of 12.1212 failing to flag on
  exactly this rule) — and a 2x2 with an empty margin
  states an *undefined* chi-square. A pair flags only when
  it meets the pack's stated criteria (a ≥ 3, PRR ≥ 2,
  chi-square ≥ 4).
- **The population is honest on the table's face**: how
  many cases the window held, how many were attributable
  to a coded product, how many stood unattributed (an
  uncoded product decision is counted, never guessed into
  a product), and how many standing event decisions were
  uncoded. Per-product system-organ-class rows carry their
  share as a rate with its denominator on the figure.
- **A flag is a prompt, not a finding** — the table says
  so on its face: disproportionality in spontaneous
  reports is not incidence and not evidence of causation.
  Tables are insert-only artefacts that supersede by link
  when the record moves, are re-performed by a second
  aggregation path before their verified mark is set
  (`scripts/check_signals.py`, or `make signals-check`),
  carry no case ids and no patient or reporter field
  anywhere, and are generated as the safety scientist's
  named act under the access pack (`signal_counts`
  surface) — the auditor reads tables and generates none.

## What Round 14 adds

- **Dictionary stewardship** (`src/pv_cases/steward.py`,
  migration 0013): the coding layer's queue is per case —
  an uncoded verbatim raises a task on its own case — but
  nothing aggregated the book's uncoded wordings onto one
  page, so the dictionary itself could not be governed.
  The steward worklist is that page: every wording the
  book stands uncoded on, folded in code under a versioned
  steward pack (2026.10.1).
- **One ranked row per wording**: how many reports carry
  it, how many standing decisions stand uncoded on it,
  when it was first and last seen, why it is uncoded, and
  the coded products those reports arrived with —
  attribution only by the standing coded product decision
  (a report whose product stands uncoded is counted as
  unattributed, never guessed). Each row also states the
  wording's standing against the dictionary in force: a
  wording the dictionary has since learned reads
  `codes_now`, while the decisions of record that predate
  the entry stand exactly as coded.
- **A worklist is never an auto-coder**: striking one
  codes nothing, changes no case and decides no mapping,
  and it says so on its face. Worklists are insert-only
  artefacts that supersede by link when the record moves,
  are re-performed by a second aggregation path before
  their verified mark is set (`scripts/check_steward.py`,
  or `make steward-check`), and carry no case ids and no
  patient or reporter field anywhere.
- **Candidates under dual control**: a named steward
  proposes a mapping against a worklist row — a candidate
  artefact that enacts nothing — and a second named
  steward, never the proposer, confirms or rejects it.
  The decision is its own artefact; a candidate's state is
  derived, and decided work does not move. Confirmation
  enacts the mapping through the coding layer's own
  collision-checked addition, minting a new dictionary
  version that names the candidate and governs cases
  coded on or after the decision date. Cases already
  coded keep the coding of record — history is never
  rewritten. A wording that stands ambiguous takes no
  candidate (it is already a synonym of more than one
  term); it is settled per case by a coder's manual
  resolution, and the refusal names that path.
- **Stewardship is judged like every other act**: the
  surface and its three acts sit under the access pack —
  the case-working roles, the quality auditor and the
  case admin read it; the safety scientist and the case
  admin strike worklists, propose and decide; the auditor
  performs none of it, and refusals land in the denial log
  before they propagate.

## What Round 15 adds

- **The safety-reviewer console** (`src/pv_cases/console.py`,
  `src/pv_cases/static/console.html`): the design brief
  named a single-file analyst console from the first
  round; every engine surface landed before the page that
  composes them. The page is served at `GET /console` and
  renders only what `GET /console/data` returns — a static
  shell carrying no case data of its own.
- **A composition, never a second opinion**: the console
  computes no verdict and stores nothing. Its case rows
  carry the current assessment's verdicts and the clock
  board's derived state; its queue rows are the review
  service's own PHI-minimised queue and the follow-up
  board's own rows; and its aggregate sections pass
  through the standing oversight report's, signal table's
  and steward worklist's own figures — read, never
  recomputed. A console that derived its own numbers
  could disagree with the artefacts of record; this one
  cannot, because it has no numbers of its own — its
  headline counts are counted from the rows it composes,
  and `scripts/check_console.py` (or `make console-check`)
  re-performs every figure by a second route.
- **Judged like every other surface**: the console is its
  own surface in the access pack — a carried principal is
  judged on it before anything is composed, the refusal
  recorded before it propagates — and each composed
  section is additionally filtered by its own surface. A
  section the role cannot read arrives invisible: no
  rows, and no count to leak. The case-working roles and
  the quality auditor read the console; intake and case
  admin are refused at its door, and the denial log
  section is invisible even to the case-working roles —
  only the auditor reads refusals on the page.

## What Round 16 adds

- **The Postgres production profile, stood up and graded**
  (`src/pv_cases/parity.py`, `scripts/check_postgres.py`,
  `make postgres-check`): the dialect store, both migration
  trees and the `DATABASE_URL` switch existed from the
  early rounds, but fifteen rounds of gates were graded on
  SQLite only — the one Postgres defect found before this
  round (the migration runner speaking `?` to a psycopg
  connection, Round 10) was found by accident. The parity
  harness replaces the accident with a discipline: one
  deterministic walk — intake through the validity gate,
  dedup, coding, the assessment packs and their clocks,
  dual-control review, sealing, export, quarantine, the
  gateway, the model ledger, the three standing aggregates,
  the console and the denial log — runs against a store,
  and everything it produces is read back as a figure
  sheet of plain JSON values. The same walk runs on the
  other dialect and the sheets are compared field for
  field; a divergence is named by its path, never averaged
  over.
- **The verdict on the current code**: the full migration
  chain (all thirteen files) stands on a real PostgreSQL
  database, the walk serves end to end there, its export
  passes the offline verifier, and the two sheets agree
  exactly — 61,714 canonical bytes with identical hashes,
  stable across processes and hash seeds. No dialect
  divergence was found in the walk's reach; the harness,
  not the finding, is the deliverable, and it stays as the
  standing gate (`tests/test_postgres_parity.py` pins the
  walk on SQLite in the suite, and on Postgres wherever
  `PV_TEST_DATABASE_URL` provides a database).
- **The supported profile, stated**: a UTF-8 PostgreSQL
  database — the repo's JSON-text contract assumes it end
  to end, the check reads the server's encoding and
  refuses to grade a non-UTF-8 database as the production
  profile. The walk runs in its own schema inside the
  provided database and drops it on success, so the check
  never touches standing tables.

## What Round 17 adds

- **The second red team** (`tests/test_redteam2.py`,
  `docs/red-team.md`): Round 9's pass covered the trust
  boundaries Rounds 1–8 built. Rounds 11–16 then shipped
  six new surfaces — oversight reporting, the governed
  model gateway, signal counts, dictionary stewardship,
  the console and the parity harness — and none of them
  had a deliberate attack pass of its own. This round is
  that pass: fourteen attacks in Round 9's idiom, each
  kept forever as a regression trap, the headline ones
  also as zero-tolerance `redteam2_*` counters in the
  eval gate.
- **One real defect, fixed**: the model gateway's
  minimisation door (Round 12) matched the pack's
  direct-identifier field paths and value keys by exact
  string. `patient.DOB`, `Reporter.Name`, a nested
  `dateOfBirth` or `date-of-birth` were not the pack's
  exact lowercase snake_case, so they were waved through
  to the port. The door now folds paths and keys before
  comparing — case and the separator spellings are not
  identity — and names the spelling as carried in the
  refusal. The pack data, and with it the per-call
  fingerprint discipline, is unchanged; the comparison
  was the defect.
- **What held, and is now trapped**: identifier keys at
  any depth of a fact's value; refusal records that name
  keys and never values; the three aggregate artefacts
  (the oversight report, the signal table, the steward
  worklist and its candidate and decision) swept for the
  book's identifiers at the level of *values*, not only
  key names — nothing found; every rate figure carrying
  exactly its value, numerator and denominator; the
  signal flag rule at its exact edges (PRR on the
  threshold, a hair under it, chi-square a hair under
  its floor, the no-comparator pair); a steward
  enactment attacked for rewriting history — the coding
  of record stands byte-identical, a fresh pass appends
  its migration by link, exactly as designed since
  Round 9, and a post-decision case codes under the new
  version pinning it; held text nowhere in the console
  payload; a console read that writes nothing; the
  auditor-only denial section neither served to nor
  inferable from another reader's payload; existence on
  the signal-counts API judged behind the surface, so a
  surfaceless reader cannot tell a real table id from a
  ghost; and the parity comparator itself, attacked with
  a divergence buried in one deeply nested scalar.
- **Stated boundaries, on the record**: the door's
  contract is structural (field paths and key names);
  identifier prose inside a free-text value is outside
  it, and no assembler fact carries narrative text. And
  the migration distinction: the artefact of record
  never moves, while a standing automatic coding can be
  superseded by a deliberate new pass — the supersession
  is itself an artefact. No open findings remain.

## What Round 18 adds

- **Draft-vs-final review measurement** (`draft_final.py`,
  migration 0014): the design brief's eighth law — *draft-
  vs-final diffs are recorded per case* — was the last of
  its ten laws with no machinery behind it. The review
  gate has always stored its narrative drafts as
  content-addressed summaries and bound its accept acts
  to one of them; this round measures the distance
  between the two. A measurement (`DFM-`) is an
  insert-only artefact struck per case once its
  dual-control review completes: the *draft* is the
  first narrative the assembler produced, the *final* is
  the summary content the completing accept acts
  actually bound — taken from the acts' own bindings and
  checked against the summary's version (a summary's
  version is its content hash), never from whichever
  narrative happens to be latest.
- **The diff is computed, under a pack**: statement-level
  comparison under the draft-final pack (2026.10.1), its
  rules carried as data — a statement is its text plus
  its citations; matching is a multiset, so identical
  content reads unchanged, the same words under changed
  citations read citation-changed, and leftovers read
  dropped (draft side) or added (final side); a reworded
  statement therefore reads as a drop plus an add, stated
  on the pack's face rather than smoothed over, and
  ordering is not identity. The artefact carries counts
  and statement hashes — never statement text — plus the
  assemblies walked (every stored summary up to and
  including the bound final) and how many of them were
  distinct drafts, so a rubber stamp (one assembly,
  approved unchanged) and a quiet re-assembly are
  distinguishable at a glance.
- **The book's figure, with its denominator**: the
  verbatim rate — of the cases standing measured, how
  many the gate approved with the draft untouched —
  folds at read time from the standing measurements,
  every rate stated as value, numerator and denominator
  (an empty book states `no_cases`, never a bare 0). A
  measurement over a record that later moves is struck
  again as a new artefact superseding by link; the
  standing measurement of a case is the head of that
  chain. Refusals are by name: a case whose review has
  not completed cannot be measured (the standing state is
  named), and a review that bound no summary cannot
  either. Every measurement is re-performed by a second
  path over the raw record before its verified mark, and
  the verifier names any stored figure the record does
  not re-perform, field by field.
- **An oversight artefact, not a case artefact**: a
  measurement writes nothing to the case it measures and
  is no part of any seal's inventory — striking one over
  a sealed case leaves the case's export package hash
  exactly where it stood, graded in the tests, the eval
  gate and `scripts/check_draft_final.py` (`make
  draft-final-check`). The act is the safety scientist's
  named act under a new access surface (`draft_final`),
  with the case-working roles and the auditor reading;
  reads are existence-hidden like the ledger's. The
  parity walk measures two of its cases on both stores,
  so the figure sheets grade the surface on Postgres too
  (the sheet now runs 63,543 canonical bytes, identical
  hashes on both dialects). Six new zero-tolerance
  `draft_final_*` counters join the eval gate.

## What Round 19 adds

- **Red Team III** (`tests/test_redteam3.py`, the record
  in `docs/red-team.md`): the pre-release attack pass, in
  Rounds 9 and 17's pattern — this time over what was
  built since the second team went home, above all Round
  18's draft-vs-final measurement, with fresh angles on
  the model gateway, signals, stewardship, console and
  parity surfaces where Round 18 changed what they
  compose. Ten attacks (RT-42–RT-51): one real defect,
  eight held, one boundary stated rather than fixed.
- **The defect (RT-42): a measurement's identity was
  taken on trust.** The verifier re-performed a
  measurement's *figures* and nothing else — the pack it
  named, the id it carried and the supersedes link it
  asserted were never graded, and standing was a date
  order over whatever rows the store held. A row written
  around the service with a flipped verbatim flag and a
  later date therefore displaced the genuine standing
  measurement and poisoned the book's roll-up: the
  oversight figure of Round 18 could be rewritten by
  anyone who could write the table. Fixed at the root in
  `draft_final.py`: the verifier now grades the identity
  claims alongside the figures (the pack must be one the
  system holds and the one the re-performance runs
  under; the id must be the content address recomputed
  over the artefact's own stated fields; the link must
  name a held measurement of the same case on a chain
  that reaches a root); the case's eligible artefacts
  must form the single chain `measure()` builds, so a
  severed link or a fork is named as a broken chain; and
  standing requires that single chain over eligible
  artefacts only — a severed or forked set has no
  standing measurement, rather than a head picked by
  date from rows whose order the chain does not state.
- **What held**: measuring over a record that moved
  after its review (refused — the gate no longer stands
  over the current narrative); identical re-assembly
  after review (a summary's version is its content hash,
  and both selection paths agree); a third current
  accept act (the builder's derived view and the
  verifier's raw walk name the same completing set); the
  diff's statement identity gamed three ways (citation
  swaps, duplicates, citation order — one identity in
  the diff and in the review layer's version hash);
  existence symmetry on the measure act; the artefact
  census across a strike; a stale measurement named by
  re-performance over a moved record; and the parity
  sheet carrying the new surface's full figures, not
  just its ids.
- **The stated boundary (RT-50)**: a stored payload
  replaced wholesale — fabricated figures under a
  correctly recomputed content address — is
  identity-valid by construction; only the figure
  re-performance names it, which is the defence the
  system claims (the verifier, the verify endpoint, the
  standing checks) and the same trust line every
  artefact in the system stands on. Recorded in
  `docs/red-team.md` so the boundary is a decision on
  the page. Three `redteam3_*` counters join the eval
  gate, and `scripts/check_red_team.py` (`make
  red-team-check`) stands the identity defences as a
  check alongside the other standing checks.

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

- **Round 19 of 20.** Built and gate-green: everything in
  Rounds 1–18, plus the third red team — the pre-release
  attack pass over what Rounds 12–18 built, above all
  Round 18's measurement. Ten attacks: one real defect
  fixed in the round (RT-42 — a measurement's identity
  was taken on trust: the verifier graded figures only,
  and standing was a date order over stored rows, so a
  forged row with a flipped verbatim flag displaced the
  genuine standing measurement and poisoned the book's
  roll-up; the verifier now grades pack, content address
  and chain shape alongside the figures, and standing
  requires the single chain `measure()` builds over
  eligible artefacts), eight held, and one boundary
  stated rather than fixed (RT-50 — a wholesale payload
  replacement that recomputes its own content address is
  identity-valid, and only the figure re-performance
  names it; recorded in `docs/red-team.md`). Three
  `redteam3_*` counters join the eval gate (117 in all),
  `make red-team-check` stands the identity defences as
  a check, and no findings stand open. Round 18 shipped:
  draft-vs-final review measurement — the design brief's
  eighth law, the last of its ten without machinery. Per
  case, once the dual-control review completes, an
  insert-only measurement (`DFM-`, migration 0014)
  records the distance between the first assembled
  narrative draft and the summary content the completing
  accept acts bound: a statement-level diff computed
  under a versioned pack (2026.10.1) — unchanged,
  citation-changed, added, dropped, carried as counts
  and statement hashes, never statement text — the
  assemblies walked and the distinct drafts among them,
  and whether the gate approved the draft untouched. The
  book's verbatim rate folds at read time with its
  denominator on the figure; measurements over moved
  records supersede by link; refusals name the standing
  review state or the missing summary binding; every
  measurement is re-performed by a second path before
  its verified mark. A measurement is an oversight
  artefact about a case, not part of it: striking one
  over a sealed case leaves the export package hash
  unmoved. The parity walk measures on both stores (the
  figure sheet 63,543 canonical bytes, identical
  hashes), six `draft_final_*` counters join the eval
  gate, and `make draft-final-check` grades the planted
  book. Round 17 shipped: the second red team — a deliberate
  attack pass over the surfaces Rounds 11–16 built, in
  Round 9's pattern (traps in `tests/test_redteam2.py`,
  the `redteam2_*` eval counters, the record appended to
  `docs/red-team.md`): fourteen attacks, one real defect
  fixed in the round (the model gateway's minimisation
  door matched identifier keys by their exact spelling,
  so a case, separator or camelCase variant of a
  direct-identifier key walked through it; the door now
  folds paths and keys before comparing), the rest held,
  and no findings stand open. Round 16 shipped: the Postgres production
  profile stood up and graded — a standing parity harness
  (`scripts/check_postgres.py`, `make postgres-check`)
  that runs the demo's core walk on both stores and
  compares the resulting figure sheets field for field:
  all thirteen migrations standing on a real UTF-8
  Postgres, the walk serving end to end there, the export
  verifying, and zero divergences against the SQLite
  sheet (61,714 canonical bytes, identical hashes). The
  parity tests pin the walk on SQLite in the suite and on
  Postgres wherever a database is provided. Round 15
  shipped: the safety-reviewer console — one
  composed read over the whole book (the cases with their
  verdicts and clock positions, the review queue, the
  follow-up board, the coding queue, the quarantine
  position, and the standing oversight, signal and
  stewardship artefacts read back, never recomputed),
  served as a static page at `/console` over a data
  surface judged on its own surface in the access pack,
  with every section additionally filtered by its own
  surface so a role that cannot read a section receives
  it invisible, without even its count; the composition
  stores nothing and computes no verdict. Round 14
  shipped: dictionary stewardship — the aggregate
  worklist over the book's uncoded wordings (migration
  0013): one ranked row per wording computed in code under
  a versioned steward pack (2026.10.1), stating report and
  decision counts, first/last seen, the uncoded reason and
  the coded products the reports arrived with, plus each
  wording's standing against the dictionary in force;
  worklists as insert-only artefacts superseding by link,
  re-performed by a second fold before their verified
  mark, aggregate-only with no case ids anywhere;
  candidates proposed against a worklist row and decided
  under dual control (the proposer may not decide),
  confirmation enacting the mapping as a new dictionary
  version through the coding layer's own addition,
  governing cases coded on or after the decision date
  while cases already coded keep the coding of record;
  the stewardship surface and its acts under the access
  pack. Round 13 shipped: signal counts — the disproportionality
  view over the coded book (migration 0012): every
  product–event pair's 2x2 computed in code under a
  versioned signal pack (2026.10.1) with the proportional
  reporting ratio and chi-square stated per pair, undefined
  measures stated as `no_comparator` / `undefined` rather
  than faked, flags only against the pack's stated criteria
  (a ≥ 3, PRR ≥ 2, chi-square ≥ 4), counting by distinct
  case over a duplicate-free population with unattributed
  and uncoded counts on the table's face; tables as
  insert-only artefacts superseding by link, re-performed
  by a second fold before their verified mark, aggregate-
  only with no case ids anywhere, generated as the safety
  scientist's named act under the access pack. Round 12
  shipped: the governed model gateway — every
  summary call routed through one door under a versioned
  model pack (2026.10.1), landing on an insert-only per-case
  cost ledger (migration 0011) whose tokens derive from
  stored sizes by the pack's stated byte rule and whose
  costs fold from the pack's reference rates (pack-stated
  measurements, never provider-billed); minimisation judged
  at the door against the pack's direct-identifier lists,
  with refusals recorded naming fields and keys but never
  values; per-case call and cost budgets refusing before
  the port is touched, on the record; the hosted profile
  failing closed through the gateway with no fallback; held
  text dropped on the gateway path itself; the ledger
  re-performed by a second path and read as a case surface
  under the access pack, existence-hidden like every case
  surface. Round 11 shipped: safety oversight reporting — a versioned
  report pack (2026.10.1) computing the periodic view (intake
  by state, seriousness mix, clock compliance per obligation
  class, follow-up ageing, coding coverage, quarantine
  position, review queue position) only from artefacts, every
  rate carrying its denominator on the figure and an empty
  population reading `no_cases`; reports as insert-only
  artefacts (migration 0010) stating their window, as-of and
  record fingerprint and superseding by link over a moved
  record; the verified mark set only when an independent
  re-performance from raw store reads agrees figure for
  figure; generation as the safety scientist's judged act
  under the access pack, report reads for the scientist, both
  review roles and the quality auditor; and aggregate-only
  payloads — no case ids, no patient or reporter field,
  graded by a key scan. A report is an internal oversight
  artefact, never a regulatory submission, and says so on its
  face. Round 10 shipped: access control — a versioned access pack
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
docs/             architecture.md — the AgentCore mapping, honestly;
                  release-1.0.0.md — the 1.0.0 release notes
CHANGELOG.md      the twenty rounds, one entry each
Dockerfile        ARM64 Runtime image (port 8080)
docker-compose.yml  Postgres+pgvector, SeaweedFS S3 (service: minio)
```

Apache 2.0. Built by Trida AI as an open blueprint for
forward-deployed agent engineering.
