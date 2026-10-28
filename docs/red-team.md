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
