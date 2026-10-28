"""The offline export verifier — Round 7.

Takes ONE export package (see :mod:`pv_cases.sealing`) and
nothing else — no store, no database, no network — and
re-performs every guarantee the package claims:

- ``package_shape`` — the package is the format it claims, and
  its seal names the same case the package does.
- ``artefact_hashes`` — every artefact's content hash recomputes
  from the payload the package carries, and matches the hash the
  seal's inventory binds for it. An altered figure fails here.
- ``chain_linkage`` — the evidence items re-link from the
  genesis hash: every payload digest recomputes, every item
  hash recomputes over its predecessor, the count and the head
  are the seal's.
- ``seal_inventory_exact`` — the package carries exactly the
  seal's inventory: nothing missing, nothing extra, and the
  inventory's own hash recomputes.
- ``review_gate`` — the gate the seal claims was satisfied by
  two distinct people in the two roles, each act's bindings are
  the seal's bindings, and every binding resolves to a sealed
  artefact at the bound hash.
- ``clock_arithmetic`` — the sealed assessment's clock
  re-performs from the sealed facts: day zero is the case's
  awareness date, the obligation class follows the verdicts and
  the fired criteria, and the deadline is day zero plus the
  class's days, to the day.

A check the package gives the verifier no way to run fails —
absence of evidence is not a pass. The verdict is ``pass`` only
when every check passes. The package hash in the report is
computed over the package as presented, never read from it:
compare it against the export record's hash (in the store, and
on the case's evidence chain) to anchor the package to the
system that served it.

Run standalone:  pv-agent-verify-export <package.json>
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from pv_cases.evidence import GENESIS_HASH, canonical_json, item_hash
from pv_cases.models import (
    PACKAGE_FORMAT,
    REVIEW_ACCEPT_DECISIONS,
    REVIEW_ROLES,
    SEAL_KIND_ASSESSMENT,
    SEAL_KIND_CASE,
    SEAL_KIND_EVIDENCE,
    SEAL_KIND_REVIEW,
    SEAL_KIND_SUMMARY,
    PackageCheck,
    VerificationReport,
    normalise_text,
)


def _content_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _summary_content_hash(payload: dict) -> str:
    """A summary's version is its content — the same rule the
    review gate binds by (pv_cases.review)."""
    return _content_hash(
        {
            "model_profile": payload.get("model_profile"),
            "statements": payload.get("statements", []),
        }
    )


def verify_package(package) -> VerificationReport:
    """Re-perform every check over one export package dict."""
    checks: list[PackageCheck] = []

    def record(name: str, passed: bool, detail: str = "") -> bool:
        checks.append(PackageCheck(name=name, passed=passed, detail=detail))
        return passed

    if not isinstance(package, dict):
        record("package_shape", False, "the package is not a JSON object")
        return VerificationReport(checks=checks)
    try:
        package_hash = hashlib.sha256(canonical_json(package)).hexdigest()
    except (TypeError, ValueError):
        package_hash = ""

    seal = package.get("seal") if isinstance(package.get("seal"), dict) else {}
    artefacts = package.get("artefacts") or []
    evidence = package.get("evidence") or []
    inventory = seal.get("inventory") or []
    inv_by_key = {
        (line.get("artefact_kind"), line.get("artefact_id")): line.get(
            "artefact_hash"
        )
        for line in inventory
        if isinstance(line, dict)
    }

    # -- package_shape ------------------------------------------
    shape_ok = (
        package.get("package_format") == PACKAGE_FORMAT
        and isinstance(package.get("case_id"), str)
        and bool(seal)
        and seal.get("case_id") == package.get("case_id")
        and isinstance(package.get("artefacts"), list)
        and isinstance(package.get("evidence"), list)
    )
    record(
        "package_shape",
        shape_ok,
        ""
        if shape_ok
        else "format, case, seal or sections missing/mismatched"
        f" (format claimed: {package.get('package_format')!r})",
    )

    # -- artefact_hashes ----------------------------------------
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in artefacts:
        key = (entry.get("kind"), entry.get("id"))
        seen.add(key)
        bound = inv_by_key.get(key)
        if bound is None:
            problems.append(f"{key[0]} {key[1]} is not bound by the seal")
            continue
        if _content_hash(entry.get("payload") or {}) != bound:
            problems.append(
                f"{key[0]} {key[1]} content does not match its sealed hash"
            )
    for entry in evidence:
        item = entry.get("item") or {}
        key = (SEAL_KIND_EVIDENCE, item.get("item_id"))
        seen.add(key)
        bound = inv_by_key.get(key)
        if bound is not None and bound != item.get("item_hash"):
            problems.append(
                f"evidence item {key[1]} chain hash differs from the seal"
            )
    record(
        "artefact_hashes",
        not problems,
        "; ".join(problems[:4])
        + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""),
    )

    # -- chain_linkage ------------------------------------------
    problems = []
    prev = GENESIS_HASH
    for entry in evidence:
        item = entry.get("item") or {}
        payload = entry.get("payload") or {}
        digest = hashlib.sha256(canonical_json(payload)).hexdigest()
        if digest != item.get("payload_sha256"):
            problems.append(
                f"{item.get('item_id')}: payload digest does not recompute"
            )
        if item.get("prev_hash") != prev:
            problems.append(
                f"{item.get('item_id')}: prev_hash does not link to its"
                " predecessor"
            )
        recomputed = item_hash(
            item.get("prev_hash", ""),
            item.get("payload_sha256", ""),
            item.get("kind", ""),
            item.get("object_key", ""),
        )
        if recomputed != item.get("item_hash"):
            problems.append(
                f"{item.get('item_id')}: item hash does not recompute"
            )
        prev = item.get("item_hash", "")
    if len(evidence) != seal.get("chain_items"):
        problems.append(
            f"chain holds {len(evidence)} items, the seal binds"
            f" {seal.get('chain_items')}"
        )
    head = evidence[-1]["item"]["item_hash"] if evidence else GENESIS_HASH
    if head != seal.get("chain_head_hash"):
        problems.append("the chain head is not the seal's head hash")
    record("chain_linkage", not problems, "; ".join(problems[:4]))

    # -- seal_inventory_exact ------------------------------------
    problems = []
    inv_keys = list(inv_by_key.keys())
    package_keys = [(kind, artefact_id) for (kind, artefact_id) in seen]
    if sorted(package_keys) != sorted(inv_keys):
        missing = sorted(set(inv_keys) - set(package_keys))
        extra = sorted(set(package_keys) - set(inv_keys))
        if missing:
            problems.append(
                "missing from the package: "
                + ", ".join(f"{k} {i}" for k, i in missing[:4])
            )
        if extra:
            problems.append(
                "not bound by the seal: "
                + ", ".join(f"{k} {i}" for k, i in extra[:4])
            )
    recomputed_inventory_hash = _content_hash({"inventory": inventory})
    if recomputed_inventory_hash != seal.get("inventory_hash"):
        problems.append("the seal's inventory hash does not recompute")
    record("seal_inventory_exact", not problems, "; ".join(problems[:4]))

    # -- review_gate ----------------------------------------------
    problems = []
    by_key = {
        (e.get("kind"), e.get("id")): e.get("payload") or {} for e in artefacts
    }
    if seal.get("review_state") != "medically_reviewed":
        problems.append(
            f"the seal was not taken from medically_reviewed"
            f" (claims {seal.get('review_state')!r})"
        )
    accepted = seal.get("accepted_acts") or {}
    if set(accepted) != set(REVIEW_ROLES):
        problems.append(
            f"accepted acts cover {sorted(accepted)}, not both roles"
        )
    bindings = {
        (b.get("artefact_kind"), b.get("artefact_id"), b.get("artefact_hash"))
        for b in seal.get("review_bindings") or []
    }
    reviewers: dict[str, str] = {}
    for role in REVIEW_ROLES:
        act_id = accepted.get(role)
        act = by_key.get((SEAL_KIND_REVIEW, act_id))
        if act is None:
            problems.append(f"the {role} accept act {act_id} is not in the package")
            continue
        if act.get("role") != role:
            problems.append(
                f"act {act_id} is recorded under role {act.get('role')!r},"
                f" not {role!r}"
            )
        if act.get("decision") not in REVIEW_ACCEPT_DECISIONS:
            problems.append(f"act {act_id} is not an accepting decision")
        if act.get("case_id") != package.get("case_id"):
            problems.append(f"act {act_id} belongs to another case")
        act_bindings = {
            (b.get("artefact_kind"), b.get("artefact_id"), b.get("artefact_hash"))
            for b in act.get("bindings") or []
        }
        if act_bindings != bindings:
            problems.append(
                f"act {act_id} binds a different record than the seal binds"
            )
        reviewers[role] = act.get("reviewer")
    if len(
        {normalise_text(str(v)) for v in reviewers.values()}
    ) != len(reviewers):
        # Compared under the same normalise_text folding the
        # review service uses (Round 9, RT-25): a name variant
        # is the same person here too.
        problems.append(
            "the two roles were accepted by the same person — dual"
            " control was not satisfied"
        )
    for kind, artefact_id, bound_hash in bindings:
        if kind == SEAL_KIND_SUMMARY:
            payload = by_key.get((kind, artefact_id))
            if payload is None or _summary_content_hash(payload) != bound_hash:
                problems.append(
                    f"binding summary {artefact_id} does not resolve at"
                    " its bound content hash"
                )
            continue
        payload = by_key.get((kind, artefact_id))
        if payload is None:
            problems.append(f"binding {kind} {artefact_id} is not in the package")
        elif _content_hash(payload) != bound_hash:
            problems.append(
                f"binding {kind} {artefact_id} does not resolve at its"
                " bound hash"
            )
    if seal.get("assessment_id") not in {
        i for (k, i, _h) in bindings if k == SEAL_KIND_ASSESSMENT
    }:
        problems.append("the seal's assessment is not among the review bindings")
    record("review_gate", not problems, "; ".join(problems[:4]))

    # -- clock_arithmetic ------------------------------------------
    problems = []
    case_payload = by_key.get((SEAL_KIND_CASE, package.get("case_id")))
    assessment = by_key.get((SEAL_KIND_ASSESSMENT, seal.get("assessment_id")))
    if case_payload is None:
        problems.append("the case artefact is not in the package")
    if assessment is None:
        problems.append("the sealed assessment is not in the package")
    if case_payload is not None and assessment is not None:
        clock = assessment.get("clock") or {}
        try:
            day_zero = date.fromisoformat(clock.get("day_zero", ""))
            awareness = date.fromisoformat(case_payload.get("awareness_date", ""))
            deadline = (
                date.fromisoformat(clock["deadline"])
                if clock.get("deadline")
                else None
            )
            if day_zero != awareness:
                problems.append(
                    f"day zero {day_zero} is not the case's awareness"
                    f" date {awareness}"
                )
            seriousness = assessment.get("seriousness")
            expectedness = assessment.get("expectedness")
            fired = {
                f.get("criterion")
                for f in assessment.get("criteria") or []
                if f.get("status") == "fired"
            }
            if seriousness == "cannot_assess" or expectedness == "cannot_assess":
                expected_class, expected_deadline = "awaiting_assessment", None
            elif seriousness == "serious" and expectedness == "unexpected":
                if "death" in fired or "life_threatening" in fired:
                    expected_class = "expedited_7_day"
                    expected_deadline = day_zero + timedelta(days=7)
                else:
                    expected_class = "expedited_15_day"
                    expected_deadline = day_zero + timedelta(days=15)
            else:
                expected_class, expected_deadline = "periodic", None
            if clock.get("obligation_class") != expected_class:
                problems.append(
                    f"obligation class {clock.get('obligation_class')!r}"
                    f" does not re-perform (expected {expected_class!r})"
                )
            if deadline != expected_deadline:
                problems.append(
                    f"deadline {deadline} does not re-perform (expected"
                    f" {expected_deadline})"
                )
        except (ValueError, TypeError) as exc:
            problems.append(f"clock fields do not parse: {exc}")
    record("clock_arithmetic", not problems, "; ".join(problems[:4]))

    verdict = (
        "pass"
        if checks and all(c.passed for c in checks)
        else "fail"
    )
    return VerificationReport(
        package_format=package.get("package_format"),
        case_id=package.get("case_id"),
        seal_id=seal.get("seal_id"),
        package_hash=package_hash,
        checks=checks,
        verdict=verdict,
    )


def render_report(report: VerificationReport) -> str:
    lines = [
        f"package: {report.case_id} seal {report.seal_id}"
        f" format {report.package_format}",
        f"package hash (computed): {report.package_hash}",
    ]
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        detail = f" — {check.detail}" if check.detail and not check.passed else ""
        lines.append(f"  [{mark}] {check.name}{detail}")
    lines.append(f"verdict: {report.verdict.upper()}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Standalone entrypoint: verify one package file, offline."""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: pv-agent-verify-export <package.json>")
        return 2
    try:
        package = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read package: {exc}")
        return 2
    report = verify_package(package)
    print(render_report(report))
    return 0 if report.verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
