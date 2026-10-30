# Red team — Round 9

A deliberate attack pass over the trust boundaries Rounds 1–8
built: reporter free text, follow-up text, dictionary versions,
reference-safety extracts, the model drafting path, memory
recall, gateway/policy decisions, and the seal/export artefacts.
Each attack names the boundary it crosses, the defence it should
meet, and the verdict. Attacks that exposed real defects were
fixed in this round and are kept as regression tests
(`tests/test_redteam.py`) and zero-tolerance eval counters
(the `redteam_*` counters in `evals/run_evals.py`) — the trap
stays forever. Design-level problems that cannot be fixed inside
this round's scope are recorded as open findings with severity.

Verdicts: **held** (the defence met the attack), **defect**
(real, fixed in this round), **open finding** (real, documented,
not fixed here).

## (a) Quarantine laundering

| id | attack | verdict |
|----|--------|---------|
| RT-01 | A held event description carrying clinical wording ("admitted to hospital", "resuscitated") plus an instruction still fires the seriousness criteria and starts an expedited clock — the assembler excludes the held field, but the assessment engine reads the folded report's descriptions directly. | **defect** — fixed: the assessment treats a held description as absent (the criterion goes `cannot_assess` where that text was the deciding fact) until a named release; the held set is part of the assessment's input fingerprint, so a release supersedes the assessment. |
| RT-02 | Instruction text in the report `narrative` reaching the summary or a model prompt. | held — the narrative is never an assembler fact (R8); the summary and the prompt are built from facts only. |
| RT-03 | Held text leaking through the dedup views. | held — dedup scores verbatim terms, onsets, product and identity fields; it never reads narrative or descriptions. |
| RT-04 | Held text leaking through patient/product history views. | held — history views carry counts and coded terms only; no free-text field exists in the view model. |
| RT-05 | Held text leaking through an export. | held — the export serves evidence payloads as the *record* (that is what a case file is); the interpreted surface — the summary — excludes held text, and the offline verifier re-performs the package. |
| RT-06 | Release by an unnamed or placeholder "reviewer". | held — refused in code (R8). |
| RT-07 | Release by the *wrong role* — any named human can release any hold; there is no role separation on the release act. | **open finding (medium)** — the release discipline is named + rationale + recorded + never-after-seal, but no role registry exists for quarantine anywhere in the repo, so there is nothing to check a role against. Properly fixing this is the access-control work (roles bound to named people), not a red-team patch. |
| RT-08 | Releasing a sealed case's hold. | held — the seal guard refuses it, naming the seal (R8). |

## (b) Memory poisoning

| id | attack | verdict |
|----|--------|---------|
| RT-09 | A recalled prior case's facts (its seriousness, its coding) bleed into the requesting case's assessment, coding or clock. | held — recall writes exactly one artefact, the retrieval record on the *requesting* case's chain; no consumer in the assessment, coding, review or assembly path reads retrieval matches as facts. Graded by `redteam_memory_poisoning`. |
| RT-10 | An expired (tombstoned) retrieval still served by a read path. | held — every serving read goes through `active_retrievals` (API and CLI alike); the tombstone stands in the record, counted, never served (R8), re-graded here against a directly planted expired retrieval. |
| RT-11 | Recall recorded on a sealed case. | held — a sealed requesting case refuses recall, naming the seal (R8). |
| RT-12 | Recall leaking patient identifiers (names, dates of birth, the patient key itself). | held — matches carry case/artefact ids and coded facts; the subject is a sha256 fingerprint. Graded: no DOB, initials or reporter name from the matched case appears in the retrieval's JSON. |

## (c) Gateway / policy bypass

| id | attack | verdict |
|----|--------|---------|
| RT-13 | Calling a tool the gateway does not front, or registering one the pack does not catalogue. | held — registration refuses uncatalogued tools at wiring time; an uncatalogued invoke is denied by `deny_unknown_tool` and the tool never runs. Note: the local API/CLI analyst surface calls service methods directly by design (it is the operator's own surface); the gateway fronts the *tool* path the agent/runtime uses, which is the boundary the pack governs. |
| RT-14 | Tampering with the Cedar artefact (`infra/policies/pv-gateway-policy.cedar`) — e.g. flipping a `forbid` to a `permit` — to change a decision without a version change. | held for decisions — the executable pack is the versioned data pack in code; the cedar file is never read at runtime, so editing it changes no decision. The drift defence is strengthened this round: the parity grade now checks each rule's *effect* (forbid/permit) in the artefact against the pack, not only that rule ids appear. |
| RT-15 | Tampering with the in-code policy pack's rules without bumping its version, so recorded calls misstate the version they were judged under. | **open finding (low)** — the pack is code; changing it is a code change, outside the runtime threat model every pack in this repo assumes. The recorded `pack_version` is the pack's self-declared version. A content fingerprint of the pack, recorded per call, would close this; noted for the release-hardening round. |
| RT-16 | A caller treating the −32002 denial envelope (HTTP 200) as success. | held — a denial envelope has no `result` key and carries `error.code == -32002`; every in-repo consumer (API, CLI, demo) branches on the envelope's shape explicitly. The trap grades the shape so a future consumer cannot mistake it silently. |

## (d) Seal / export forgery

| id | attack | verdict |
|----|--------|---------|
| RT-17 | Mixed inventory: an export package carrying another case's artefacts — swapped under the sealed case's artefact ids, or under their own foreign ids. | held — every artefact is bound by (kind, id, content hash) in the seal's inventory; a swapped payload fails `artefact_hashes`, a foreign id fails `seal_inventory_exact`. Kept as traps in `tests/test_redteam.py`. |
| RT-18 | Sealing a case with a standing quarantine hold. | held, by stated design — the seal closes the *record*, held text included as evidence; the held text never entered the summary (RT-01/RT-02), and no release is possible after sealing (RT-08), so a sealed file can never quietly absorb unreviewed text into its narrative. Pinned by the R8 eval; re-graded here with the hold on a *description* (a field that does feed facts once released). |
| RT-19 | A case sealed under dictionary version N re-judged under version N+1 at export/verify time. | held — the offline verifier is package-only: it re-performs hashes, chain linkage, the review gate and clock arithmetic, and never consults a live dictionary. The seal binds the dictionary versions its decisions pinned; a later version changes nothing in the package's verdict. |

## (e) Clock / seriousness manipulation

| id | attack | verdict |
|----|--------|---------|
| RT-20 | A backdated validity follow-up (its `received_on` earlier than the original report's) founding a case whose day zero precedes the sponsor's first receipt of anything. | **defect** — fixed: the founding report's receipt date is `max(original receipt, follow-up receipt)`. The pinned rule stands — day zero is the date the report became valid — but it can no longer precede the first receipt. |
| RT-21 | A follow-up to an existing case moving day zero. | held — the merge keeps the founding report's identity and receipt; the case's awareness date is never rewritten (R5). |
| RT-22 | An outcome upgrade ("fatal") arriving via quarantined prose. | held after RT-01 — outcomes are a structured field, amended by follow-ups by design (R5's graded case); the *prose* route to a verdict was RT-01 and is closed. |
| RT-23 | A follow-up report that re-identifies the case: different date of birth / reporter-held patient id than the record — silently rewriting whose case this is (patient key, history and recall all follow). | **defect** — fixed: a follow-up whose present hard identifiers contradict the record's present values is refused with the field named, before anything lands on the chain. The report belongs in intake as a new report, where dedup judges it. Amendments that complete or agree with the record are unaffected. |
| RT-24 | A duplicate report at intake re-identifying or merging into an existing case. | held — a matching report founds its own case, flagged `suspected_duplicate_of` and linked for a human decision; nothing merges (R1/R2). |

## (f) Dual-control erosion

| id | attack | verdict |
|----|--------|---------|
| RT-25 | The same person satisfying both review roles under a trivial name variant ("Dr A. Rao" / "dr a rao" / "Dr A Rao") — reviewer identity compared as a raw string. | **defect** — fixed: reviewer identity is compared under the repo's own `normalise_text` folding (case/punctuation/whitespace) at recording time, in the derived review state, and in the offline verifier's review-gate check. A wholly different invented name remains indistinguishable from a second person — that is the name-string identity model every act in this repo uses, and is RT-07's access-control finding, not something string folding can solve. |
| RT-26 | Recording a review act over a superseded summary reached through memory recall. | held — recall records no acts and offers no review path; acts bind the *current* record by id + hash (summary by content hash), and supersession stales them in every derivation (R6). |
| RT-27 | Same-person cross-role acts via the gateway or memory surfaces. | held — the gateway catalogue contains no write or act tools at all (three read tools), and memory records retrievals only; neither surface can record a review act in any role. |

## Open findings

1. **RT-07 (medium)** — quarantine release has no role
   separation: any named human reaching the surface can release
   any hold. Needs the access-control layer (named people bound
   to roles) before a role check means anything.
2. **RT-15 (low)** — policy-pack (and, by the same argument,
   every versioned pack) integrity rests on the pack being code;
   a per-call content fingerprint would make silent in-place
   rule edits detectable after the fact.

## Eval depth added with this round

Beyond the attack counters (`redteam_*`), the gate now grades
boundary values it previously only approached:

- **Dedup bands**: constructed pairs scoring exactly 54
  (not a duplicate), 55 (probable), 79 (probable), 80
  (definite); the onset gate at 91 days vs no gate at 90; the
  event-overlap gate at a token overlap of exactly 0.5 (points)
  vs below it (gate).
- **Clock edges**: `clock_state` on the deadline day itself
  (`due_today`, 0), the day after (`breached`, −1), the day
  before (`within_clock`, 1); the 7-day class taken exactly when
  a fatal/life-threatening criterion fired, 15-day otherwise.
- **Dictionary transitions mid-case**: after a dictionary
  addition creates a new version, automatic decisions migrate
  to it on the next coding pass, a human's manual decision
  stays pinned to its version, and the assessment supersedes
  over the new decision set.
- **Follow-up cap edge**: a resolving report on the third cycle
  resolves the request (the cap escalates *failed* attempts,
  not late successes).

## Round 10 amendments — the open findings, closed

This section is appended, not edited into the record above:
the Round 9 verdicts stand as written, including the two open
findings. Round 10 (access control + existence hiding) was the
work both findings pointed at, and it closes both.

1. **RT-07 (medium) — CLOSED by Round 10.** The role registry
   the finding said was missing now exists: the versioned
   access pack (`src/pv_cases/access.py`, 2026.10.1) names six
   roles and the acts each holds. The quarantine release act
   belongs to the safety scientist, the safety reviewer and
   the medical reviewer — not to intake processors, not to
   case admins, not to the read-only quality auditor — judged
   wherever a call carries a principal, with the refusal
   recorded in the denial log before it propagates. The round
   adds the second half of the discipline the finding implied:
   a release may not come from the held text's **own reporter
   of record** where the record can tell (the report payload
   on the chain names its reporter; a releaser whose name
   folds to it is refused, principal-carried or not). Where
   the report names no reporter — identified by a contact
   channel alone — the record cannot tell and the check does
   not fire; that limit is stated in the code. Kept as traps:
   `tests/test_access.py` (wrong-role releases denied and
   recorded, self-release refused where determinable) and the
   eval counters `access_release_wrong_role` /
   `access_self_release_accepted`, zero-tolerance.
2. **RT-15 (low) — CLOSED by Round 10.** Every `GatewayCall`
   record now carries `pack_fingerprint`: sha256 over the
   policy pack's canonical JSON, computed at invoke time from
   the live pack. A recorded call can be checked after the
   fact against the pack it claims to have been judged under;
   an in-place rule edit without a version bump changes the
   fingerprint and is detectable. (The same argument applied
   to every versioned pack in the repo; the gateway's calls
   are the recorded judgements, so the gateway pack is where
   the fingerprint binds. The access pack carries its own
   fingerprint, served at `/access/pack`.) Kept as traps:
   `tests/test_access.py` (fingerprint recorded on allow and
   deny alike; a tampered same-version copy fingerprints
   differently) and the eval counter
   `gateway_pack_fingerprint_wrong`, zero-tolerance.

No open findings remain on this page.

## Round 17 amendments — the second red team

This section is appended, not edited into the record above:
the Round 9 verdicts and the Round 10 closures stand as
written. Round 17 is a deliberate second pass, over the
surfaces Rounds 11–16 shipped after the first team went
home: oversight reporting, the governed model gateway,
signal counts, dictionary stewardship, the safety-reviewer
console and the Postgres parity harness. (The AgentCore
Policy pack itself was not a target: it shipped in Round 8
and has been graded since — RT-13/RT-14 and the per-call
fingerprint. What had never been attacked was everything
built *after* it.) Attacks are kept as regression tests in
`tests/test_redteam2.py` and, for the headline ones, as the
zero-tolerance `redteam2_*` counters in the eval gate. One
attack was a real defect, fixed in this round.

### (g) The model gateway's minimisation door (Round 12)

| id | attack | verdict |
|----|--------|---------|
| RT-28 | Direct identifiers proposed to the model gateway under variant spellings of the pack's keys and field paths — `Date_Of_Birth`, `date-of-birth`, `dateOfBirth`, `DOB`, `Patient_Name`, `patient name`, `EMAIL`, `Reporter.Name`, `patient.DOB`. | **defect** — fixed: the door matched field paths and value keys by exact string, so every variant spelling walked a direct identifier through to the port while the pack's exact lowercase snake_case was refused. The door now folds paths and keys before comparing (case and the separator spellings are not identity: `_fold_key` in `model_gateway.py`), and names the spelling as carried in the refusal. The pack data is unchanged — its fingerprint is untouched; the comparison was the defect. |
| RT-29 | An exact identifier key buried at depth inside lists and dicts in a fact's value. | held — refused, as the door's stated reach promises (any depth of a fact's value). The door's contract is structural — field paths and key names, never values: identifier *prose* inside a free-text value is not a key at any depth, and no fact the assembler builds carries narrative text (RT-02). That boundary is stated here so a future change to it is deliberate. |
| RT-30 | The refusal record itself leaking what the door refused: the identifier values appearing anywhere in the case's model-call records. | held — the refusal is recorded naming keys and fields only; a byte sweep over the case's ledger finds the key names and neither planted value, and the wrapped port's counter stays at zero. |

### (h) Oversight reporting (Round 11)

| id | attack | verdict |
|----|--------|---------|
| RT-31 | Identifier leakage as *values*: a byte sweep over a struck report's full payload for every case id, reporter name, reporter email, patient initials and date of birth in the book it was struck over. | held — nothing found. Round 11 graded a key scan over the payload shape; this grades the whole payload, values included. |
| RT-32 | A rate over a tiny population (denominator 1) smuggling identity alongside the figure. | held — every rate in the artefact is exactly `{value, numerator, denominator}`; a small population exposes its counts, never an identity field. The counts themselves are the oversight product, stated since Round 11. |

### (i) Signal counts (Round 13)

| id | attack | verdict |
|----|--------|---------|
| RT-33 | The RT-31 sweep over a struck signal table. | held — pairs and totals state counts and measures only; no identifier of the book appears as a value. |
| RT-34 | The flag rule at its exact edges: PRR exactly 2.0000 with chi-square over its floor (flags); PRR 1.9950 under a large chi-square (never flags); chi-square ~3.64 under PRR exactly 2.0000 (never flags), the same table doubled (flags); a no-comparator pair under one-sided counts (never flags). | held — the measures and the verdict are computed in code from the 2×2 and the pack's thresholds, and the edges land exactly where the pack states them. |

### (j) Dictionary stewardship (Round 14)

| id | attack | verdict |
|----|--------|---------|
| RT-35 | The value sweep over the stewardship artefacts — worklist, candidate, decision record. | held — wordings, terms and counts only; no case id, reporter or initials as a value anywhere. |
| RT-36 | Rewriting history through a steward enactment: confirm a mapping, then run a fresh coding pass over a case coded *before* the decision and make its coding of record move. | held — the original decision stands byte-identical after the pass: history is never rewritten. What the pass does is the designed, Round 9-graded migration: it appends its own decision under the new version, superseding the old by link, because a fresh coding pass is a new coding act performed after the decision date — the version governs cases *coded* on or after it. A human (manual) decision is never auto-superseded at any version, and a case arriving after the decision codes under the new version on its first pass, pinning the version on the decision. The attack's value was forcing the distinction onto the record: the artefact of record never moves; a standing *automatic* coding can be superseded by a deliberate new pass, and the supersession is itself an artefact. |

### (k) The safety-reviewer console (Round 15)

| id | attack | verdict |
|----|--------|---------|
| RT-37 | Held text surfacing in the composed read: a planted held description's marker searched across the whole console payload for two different readers. | held — the marker appears nowhere; the quarantine section states the hold and its metadata, never the text. |
| RT-38 | The console writing by reading: the store's artefact counts and the denial log compared before and after reads by three granted principals. | held — byte-identical counts; a read stores nothing and appends nothing. |
| RT-39 | Inferring the auditor-only denial log from a non-auditor's payload: plant a denial (a refused console entry), then read as a medical reviewer. | held — the section arrives as exactly `{"visible": False}`, and the denied principal's id appears nowhere else in that reader's payload, while the auditor's own payload serves the planted denial. |

### (l) Existence hiding on the post-Round-10 API surfaces

| id | attack | verdict |
|----|--------|---------|
| RT-40 | Existence oracles on the signal-counts API: a principal with no surface reads a real table id and a ghost id. | held — the surface is judged before existence is, so both answers are the identical refusal; a granted reader gets the table, and a ghost id under a granted reader is a plain 404. |

### (m) The parity harness (Round 16)

| id | attack | verdict |
|----|--------|---------|
| RT-41 | The harness grading itself shallowly: two figure sheets differing in one deeply nested scalar. | held — `compare_sheets` names the divergence at its path; identical sheets produce none. The comparator that grades dialect parity compares all the way down. |

## Open findings after Round 17

None. The round's one defect (RT-28) was fixed in the
round; every other attack met the defence it should have,
and the two stated boundaries the pass surfaced — the
door's structural contract (RT-29) and the distinction
between the coding of record and a fresh pass's migration
(RT-36) — are recorded above so they are decisions on the
page, not assumptions in the code.

## Eval depth added with this round

Six zero-tolerance counters (`redteam2_*`): the door's
variant spellings, identifier leakage across the three
aggregate artefacts, the signal flag edges, the steward
history invariant, held text in the console, and console
write-freedom — each computed over a fresh service graph
planted for the round, alongside the traps in
`tests/test_redteam2.py`.

## Round 19 amendments — the third red team

This section is appended, not edited into the record
above: the Round 9 verdicts, the Round 10 closures and
the Round 17 amendments stand as written. Round 19 is
the pre-release pass, over what was built since the
second team went home — above all Round 18's
draft-vs-final measurement, the one surface no red team
had attacked — with fresh angles on the Round 12–16
surfaces where Round 18 changed what they compose.
Attacks are kept as regression tests in
`tests/test_redteam3.py`, as the zero-tolerance
`redteam3_*` counters in the eval gate, and as the
standing `scripts/check_red_team.py`. One attack was a
real defect, fixed in this round; one boundary is
stated, not fixed (RT-50).

### (n) The draft-vs-final measurement (Round 18)

| id | attack | verdict |
|----|--------|---------|
| RT-42 | Identity forgery against the measurement layer: a row written around the service into the measurement table — a flipped `approved_verbatim`, a later `measured_on`, no chain link, an id (`DFM-0000000000000000`) that is not the content address of anything — then the standing read and the book roll-up taken. Variants: a stored payload's pack version flipped to a version that does not exist, its id rewritten, a genuine second link's `supersedes` severed, a second eligible link forked over the same predecessor, and the chain closed into a cycle. | **defect** — fixed. The verifier re-performed figures and nothing else: the pack a measurement named, the id it carried and the link it asserted were taken on trust, and standing was a date order over whatever rows the store held — so the forged row displaced the genuine standing measurement and the roll-up counted its flipped flag. Three root fixes, all in `draft_final.py`: the verifier now grades the identity claims alongside the figures (the pack must be one the system holds and the one the re-performance runs under; the id must be the content address recomputed over the artefact's own stated fields; the link must name a held measurement of the same case, never the artefact itself, on a chain that reaches a root); the case's eligible artefacts must form the single chain `measure()` builds, so a severed link (two roots) or a fork (one predecessor, two successors) is named as a broken chain; and standing is decided over chain-eligible artefacts only and requires that single chain — a severed or forked set has *no* standing measurement, rather than a head picked by date from rows whose order the chain does not state. The forgery, every variant, and the untouched genuine measurements are graded in the traps, the `redteam3_measurement_identity` counter and `make red-team-check`. |
| RT-43 | Measure a case whose review completed and whose record then moved — quarantine release and re-assembly, no fresh review. | held — refused by name: the completing acts are stale against the moved record, the review no longer stands as completed, and there is no approved summary to measure against. |
| RT-44 | Identical re-assembly after a completed review — the same narrative struck again under a new summary id. | held — a summary's version is its content hash, so the re-assembly stales nothing; the measurement reads verbatim, verifies clean on both paths, and counts assemblies by the pack's stated rule (up to and including the bound final — the later identical assembly postdates the review and is not one of them). |
| RT-45 | A third current accept — a second medical reviewer accepting the same record — driven through the measurement: do the builder's derived view and the verifier's raw walk name the same completing set? | held — both paths count all three acts, the fingerprint agrees, and the measurement verifies clean. The two selection paths were written to differ in derivation, not in answer; this is the case most likely to split them, and it does not. |
| RT-46 | The diff's statement identity, gamed: citations swapped between two same-text statements; a statement carried three times in the draft and once in the final; one statement's citations reordered. | held — a statement is its text and its citations, matched as a multiset: the swap leaves the multiset identical and reads unchanged (nothing in either statement's content moved), the duplicates pair one-for-one and drop two, and the reorder reads citation-changed — and the review layer's version hash agrees, treating reordered citations as a new version too, so no artefact can read verbatim under one identity and changed under the other. |
| RT-47 | Existence oracles on the new surface's act: an intake principal strikes a measurement over a real case and over a ghost. | held — the surface is judged before existence is (RT-40's rule on the round's own act): identical 403s, the refusal on the denial log; a granted scientist over a ghost is a plain 422 naming the absence. |
| RT-48 | The measurement reaching sideways: the store's artefact census (cases, summaries, acts, assessments, coding decisions, evidence, denials) compared across a strike, and the neighbouring surfaces read for the new artefact — the console (Round 15) composes no measurement section, and the signal counts (Round 13) read no measurement. | held — the census is byte-identical but for the measurement itself; the seal grading of Round 18 (a strike over a sealed case moves no package hash) stands, and no R12–R16 surface folds the new artefact into a figure of its own, so there is no second place for a measurement to be wrong in. |
| RT-49 | The old measurement over a moved record, re-performed after the case is re-reviewed and re-measured: it must be named, not crash and not pass. | held — the re-performance names the moved figures (and, where the record no longer supports the old selection, says so in those words). A measurement is evidence about the record as it stood at its strike; supersession, not silent continued belief, is the designed answer. |
| RT-50 | A stored measurement's payload replaced wholesale: fabricated figures (verbatim flipped, counts rewritten) under a correctly recomputed content address, same chain position. | **bounded limit** — stated, not fixed. The replacement is identity-valid by construction: its id *is* its address, its pack is real, its link is the genuine one — identity grading has nothing to grade. What names it is the figure re-performance (`approved_verbatim` and the rewritten counts are each named against the record), which is the defence the system claims: the verifier, the verify endpoint and the standing checks re-perform, and a measurement is believed where it is re-performed. This is the same trust line every artefact in the system stands on — the store's insert-only discipline is a service property, not a storage guarantee — and it is recorded here so the boundary is a decision on the page: below the service layer, figures are graded by re-performance, never by the row's own claims about itself. |

### (o) The parity harness over the new surface (Round 16, revisited)

| id | attack | verdict |
|----|--------|---------|
| RT-51 | The Round 16 walk grading its Round 18 extension shallowly — the RT-41 question asked of the new section: does the sheet carry the measurements' figures, or only their ids? | held — the sheet carries both cases' full measurement figures (statement counts, the diff split, the changes by hash, the verified mark and its problems) and the book roll-up, so a dialect divergence in any figure diverges the sheet and is named at its path by `compare_sheets`. |

## Open findings after Round 19

None standing as defects. The round's one defect
(RT-42) was fixed in the round. One boundary is stated
as a limit with its reasoning (RT-50): a wholesale
payload replacement that recomputes its own content
address is identity-valid, and only the figure
re-performance names it — the trust line the whole
system already stands on, recorded so it is a decision,
not an assumption. A second boundary the pass re-confirmed and did not
reopen: the measurement diffs the assembler's *stored*
summaries only — a narrative edited outside the
assembler leaves no artefact and so no trace in any
measurement, the same limit the review layer has
always carried, since it too decides the stored
record.

## Eval depth added with this round

Three zero-tolerance counters (`redteam3_*`): the
measurement-identity family (displacement, pack and id
forgery, the severed link, the fork), the review
layer's currency through the measurement (the moved
record refused, identical re-assembly and the third act
agreed by both paths, the stale measurement named), and
the diff's multiset games — each computed over fresh
service graphs planted for the round, alongside the
traps in `tests/test_redteam3.py` and the standing
`scripts/check_red_team.py` (`make red-team-check`).
