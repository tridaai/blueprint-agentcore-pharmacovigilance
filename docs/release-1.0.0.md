# Release notes — 1.0.0

Pharmacovigilance Adverse Event Case Agent on AWS Bedrock
AgentCore (Trida AI Blueprint #17), for the fictional sponsor
Northbridge Therapeutics. This is the frozen twenty-round
build: every gate re-run on the finished tree at release (see
CHANGELOG.md for the round-by-round account and the README's
Honest status for the per-round evidence).

## What a deployer gets

A governed pharmacovigilance case agent: the system a
drug-safety team needs between its intake channels and its
medical reviewers. Adverse-event reports arrive; the agent
passes each through a validity gate in code, assembles the
valid ones into evidence-backed case files, and stops at the
medical-reviewer gate. It ends at an approved, exportable
case file — it never submits anything to a regulator or a
safety database of record. In the box:

- The full case lifecycle: intake through the validity gate
  (a report missing any of the four minimum criteria becomes
  a follow-up task naming what is missing, never a case) →
  hash-chained evidence → de-duplication by a scored rule
  pack with human link decisions (links, never merges) →
  dictionary coding as a lookup in code → seriousness and
  expectedness assessed against versioned packs, with
  regulatory clocks computed from an unmoving day zero →
  the follow-up workflow → dual-control medical review (two
  named reviewers, two roles, never the same person) →
  sealing under a named act and a byte-deterministic export
  package an offline verifier re-performs from the file
  alone.
- The governance layers: an injection quarantine over the
  free text that enters a case, held text absent to every
  consumer until a named release; the AgentCore Memory,
  Gateway and Policy ports with their packs of record; an
  access pack judging every principal the surfaces carry,
  with existence-hidden case reads and an insert-only denial
  log; a governed model gateway with a per-case cost ledger
  and budgets that refuse before the model is touched; and
  PHI minimisation judged at that door.
- The oversight layer: periodic safety reports computed only
  from artefacts, every rate carrying its denominator;
  signal counts over the coded book with undefined measures
  stated, never faked; dictionary stewardship under dual
  control; the safety-reviewer console — one composed read
  that computes no verdict of its own; and draft-vs-final
  review measurement, so a review process that merely
  rubber-stamps shows up in the book's verbatim rate.
- The Postgres production profile, graded by a standing
  parity harness: one deterministic walk over the whole
  service graph whose figure sheets must agree field for
  field across the SQLite and Postgres stores.
- The gates that grade it all: the pytest suite, an eval
  gate with zero-tolerance counters, three red-team passes
  recorded in `docs/red-team.md` (the third standing as a
  check script), seven standing check scripts that plant
  forgeries, tampers and trapped text against the record,
  and a demo that walks the planted feed end to end.

## How to run it

```bash
make install          # uv sync --extra dev (pinned, frozen constraints)
make demo             # the planted feed end to end, offline
make test             # pytest (SQLite by default)
make evals            # the eval gate (zero-tolerance counters)
make reports-check    # the oversight report check (re-performs a stored report)
make model-ledger-check # the model ledger check (plants a tamper)
make signals-check    # the signal counts check (re-performs the tables)
make steward-check    # the dictionary stewardship check (re-performs the worklist)
make console-check    # the console check (re-performs the composition)
make draft-final-check # the draft-vs-final measurement check (plants a book)
make red-team-check   # the third red team check (plants the RT-42 identity attacks)
make postgres-check   # the parity check (needs PV_POSTGRES_URL, a UTF-8 database)
make serve            # the AgentCore Runtime app on :8080
make api              # the case API on :8000
make invoke           # local in-process invoke of the agent step
```

No `uv`? `make install-pip` is the two-step fallback: install
the frozen `constraints.txt`, then the project with
`--no-deps -e .`.

**Postgres:** set `DATABASE_URL` and the same code runs on
PostgreSQL — migrations 0001–0014 ship in both dialects
through the store's runner, and the parity harness above is
the standing proof that the two dialects agree. The supported
profile is a UTF-8 database; the check reads the server's
encoding and refuses to grade a non-UTF-8 database as the
production profile.

**Configuration** is environment-only; see `.env.example`. The
offline deterministic model profile is the default; the
Bedrock profile refuses loudly until a region and model id
are configured — it never falls back silently.

`docker compose up` brings up the agent, Postgres+pgvector and
the SeaweedFS S3 gateway in the portfolio's standard shape;
the Dockerfile is multi-stage and non-root, and AgentCore
Runtime deploys want an ARM64 image
(`docker buildx build --platform linux/arm64`).

## The freeze

Every gate below was run on the release tree at 1.0.0; the
figures are the gate outputs, not summaries of earlier rounds.

- **pytest:** 431 passed — the full suite with the Postgres
  parity leg live (`PV_TEST_DATABASE_URL` set against a real
  UTF-8 PostgreSQL database, so the parity tests ran inside
  the suite, not skipped), run per test file to completed
  logs. 431 is Round 19's total unchanged: this round built
  no new tests.
- **Postgres parity check:** PASS — all fourteen migrations
  stand on Postgres, the walk serves end to end there, its
  export verifies, and the two dialects' figure sheets agree
  field for field: 63,543 canonical bytes, sha256
  `bbf727eb6d9a64f8…` on both dialects — the same hash as
  Round 19's freeze, because this round changed no walk
  behaviour.
- **Eval gate:** PASS — 117 zero-tolerance counters, every
  one at 0.
- **Checks:** reports, model ledger, signals, steward,
  console, draft-final and red-team checks — each PASS, each
  grading its claims from planted records (forgeries, a
  tamper, trapped text, the RT-42 identity attacks) rather
  than from the surfaces under test.
- **Demo:** exit 0 — the planted feed walked end to end.

## The honest limits

- **No live AWS call of any kind has been made from this
  build.** No live AgentCore, Bedrock or Gateway call: the
  Runtime entrypoint is the real `BedrockAgentCoreApp` from
  the installed `bedrock-agentcore` SDK, verified over its
  HTTP contract in tests, and everything beyond it is a port,
  a local double, or a pack waiting on a deployment. The
  Memory, Gateway and Policy surfaces (Round 8) are ports
  with local doubles — the port contracts, the policy
  enforcement and the retrieval records are real and graded;
  the AWS-side services behind them are the
  account-integration step.
- **No SAR is ever filed, and nothing is submitted to a
  regulator — in any round.** The system ends at an approved,
  exportable case file; what a sponsor does with it is the
  sponsor's regulated process. The regulatory clocks state an
  obligation; they do not discharge one.
- **The dictionary is a repo-built extract, not licensed
  MedDRA** — a reduced, original terminology written for this
  blueprint (12 event entries, 3 product entries at version
  2026.10.1). Coding against the real dictionary is a
  licensing and integration step this blueprint deliberately
  does not fake.
- **The reference safety information is a repo-built
  stand-in** — a small original extract, not the products'
  real labels. Expectedness against a real label is a
  licensing and integration step this blueprint deliberately
  does not fake. Causality assessment is not part of this
  blueprint's verdicts; seriousness and expectedness are the
  computed judgements.
- **Live-model drafting is stub-tested until a working key
  exists.** The offline deterministic profile is the graded
  path; the hosted profiles are stub-tested and fail-closed
  through the governed gateway — a hosted profile that is not
  configured refuses as an error record, and nothing proceeds
  unpriced, unrecorded or by silent fallback.
- **Adverse media runs on a reference corpus.** The
  literature channel is exercised against the blueprint's
  planted reference feed and corpus doubles; screening lists
  are stand-ins. Connecting live literature and screening
  sources is deployment work.
- **Online evaluator configs are not in 1.0.0.** AgentCore
  Evaluations configuration was deferred to an infra round
  the release did not take. The repo's own deterministic eval
  gate is primary and binding; wiring online evaluators
  (correctness, faithfulness, harm) sampled on live traffic
  remains deployment work.
- **OpenTelemetry spans are not in 1.0.0.** Observability at
  release is structured JSON logs with session/case ids
  (`pv_cases/logging_config.py`); the spans and the CloudWatch
  GenAI dashboards they feed were deferred with the same
  infra round and remain deployment work.
- **The measurement layer has a stated boundary.** Round 18's
  draft-vs-final measurement diffs the assembler's stored
  summaries only: a narrative edited outside the assembler
  leaves no artefact and so no trace — the same limit the
  review layer already carries. Statement identity is exact
  text plus citation list under the pack; a one-character
  reword counts as dropped + added by design, stated on the
  pack's face rather than smoothed over.
- **Insert-only is a service discipline, not a storage
  guarantee (RT-50).** A stored payload replaced wholesale —
  fabricated figures under a correctly recomputed content
  address — is identity-valid by construction; only the
  figure re-performance names it. That re-performance (the
  verifiers, the verify endpoints, the standing checks) is
  the defence the system claims, and it is the same trust
  line every artefact in the system stands on. The boundary
  is recorded in `docs/red-team.md` as a decision, not
  smoothed over.
- **Access enforcement governs calls that carry a
  principal.** Principal-less calls (the CLI, the local
  harness) remain the trusted-local operations path they have
  been since Round 1; in production, AgentCore Identity
  terminates caller identity in front of these surfaces and
  every call carries one. The pack governs authorisation; who
  a principal id belongs to is the identity layer's question.
- **Model costs are pack-stated, not provider-billed.** The
  ledger's tokens derive from stored sizes by the model
  pack's stated byte rule and its rates are stated reference
  rates (the offline profile at zero). A deployment states
  its own contracted rates by superseding the pack.
- **The Docker image build is reviewed statically**, not
  run — the build sandbox has no Docker daemon; the
  Dockerfile (multi-stage, non-root, ARM64) and compose
  contracts are graded by `tests/test_docker_contract.py`.
- **The README's work-in-progress notice stays on.** The
  automated gates are green; hands-on developer testing and
  the companion blog post are still outstanding, and the
  notice comes off when both are done — not before.

## What a deployment still has to do

- Wire an AWS account: configure the Bedrock profile (region
  + model id), build the ARM64 image (the build itself, not
  the static review), and deploy the Runtime entrypoint.
- Stand up the AgentCore services behind their ports: Memory
  (the production path behind the Memory port), a Gateway
  with the safety-database, product-dictionary and literature
  targets behind the tool catalogue's contracts, and the
  Cedar Policy pack attached to that Gateway in ENFORCE mode.
- Terminate caller identity (AgentCore Identity) in front of
  the API and Runtime surfaces so every call carries a
  principal, and wire the outbound credential vault shapes
  (access-token / API-key) the Identity row of the
  architecture mapping describes.
- License and integrate the real dictionary and the products'
  real reference safety information in place of the
  repo-built extract and stand-in; connect live screening
  lists and literature sources in place of the stand-ins and
  the reference corpus.
- Configure AgentCore Evaluations (online evaluators sampled
  on live traffic) and OpenTelemetry spans per case step with
  the CloudWatch GenAI dashboards — the two pieces deferred
  to an infra round that 1.0.0 does not contain.
- Point `DATABASE_URL` at a UTF-8 PostgreSQL 16 database if
  that is the chosen store, and run the parity check against
  it before trusting the profile.
- State contracted model rates by superseding the model pack,
  and set the per-case call and cost budgets to the
  operation's own numbers.
- Supply a working hosted-model key to take the drafting
  profiles live; until then the offline deterministic profile
  is the graded path and the hosted profiles fail closed.
