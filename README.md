<p align="center">
  <img src="https://trida.ai/logos-rounded/trida-rounded.png" alt="Trida AI" width="96">
</p>

# Blueprint — Pharmacovigilance Adverse Event Case Agent (AWS Bedrock AgentCore)

A working prototype of the agent a drug-safety team needs between
its intake channels and its medical reviewers: adverse-event reports
arrive from patients, clinicians, call centres and literature, and
the agent **passes each one through a validity gate in code,
assembles the valid ones into evidence-backed case files, and
stops**. A named medical reviewer will own seriousness, causality
and every submission decision in later rounds. The system ends at
an assembled, exportable case file — **it never submits anything to
a regulator or a safety database of record**.

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

- **Round 2 of 20.** Built and gate-green: everything in Round 1,
  plus the dedup rule pack with human link decisions, patient and
  product history views consumed by the assembler, migration 0002,
  and API/CLI parity for both. Round 1 shipped: models + validity
  gate, intake + feed, evidence chain, store + migration 0001,
  Runtime entrypoint, agent step with cited summaries, API, demo,
  evals.
- **Dictionary coding is Round 3** — verbatim terms are stored and
  carried into summaries as reported; no MedDRA-style coding exists
  yet, and the dictionary this blueprint will use is a repo-built
  extract, never the licensed dictionary itself.
- **The seriousness engine is Round 4** — R1 makes no seriousness,
  expectedness or causality judgement of any kind.
- **No regulator submission ever** — not in this round, not in any
  round. The system ends at an approved, exportable case file; what
  a sponsor does with it is the sponsor's regulated process.
- **Live AWS calls are stub-tested until an account is wired**: the
  `bedrock` model profile refuses loudly without region + model id,
  and the AgentCore Memory / Gateway / Policy surfaces are ports
  with local doubles (Rounds 11–13). The Runtime entrypoint itself
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
