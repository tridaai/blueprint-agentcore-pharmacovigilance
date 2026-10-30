"""Round 15: the safety-reviewer console — the whole book in
one composed read.

The design brief named a single-file analyst console from the
first round; every engine surface landed before the page that
composes them. This service is that composition, and it is
deliberately only a composition:

- **It computes no verdict.** Every row is a queue view or a
  stored artefact read back: the review queue is the review
  service's own PHI-minimised queue, the follow-up rows are
  the board's, the case rows carry the current assessment's
  verdicts and the clock board's derived state, and the
  aggregate sections pass through the struck oversight
  report's, signal table's and steward worklist's own
  figures — read, never recomputed. A console that derived
  its own numbers could disagree with the artefacts of
  record; this one cannot, because it has no numbers of its
  own. Its headline counts are counted from the rows it
  composes, in code, at read time.
- **It is judged like every other surface.** The console is
  its own surface in the access pack: a carried principal is
  judged on it before anything is composed (the refusal is
  recorded, then propagates), and each composed section is
  additionally filtered by its own surface — a section the
  role cannot read arrives `visible: False` with no rows and
  no counts, the list discipline applied to a page: a hidden
  section does not leak even how much it holds. The
  principal-less call remains the trusted local path, as on
  every surface since Round 10.
- **It stores nothing.** No artefact, no migration: the
  console is a read over the record as it stands, struck
  fresh on every call, so it can never go stale against the
  book it shows. What it shows at `as_of` is derived at
  `as_of`, like every board in this repo.

The page itself is a static shell (`static/console.html`)
that renders only what `GET /console/data` returns; it
carries no case data of its own.
"""

from __future__ import annotations

from datetime import date

from pv_cases.access import (
    SURFACE_CASE,
    SURFACE_CODING,
    SURFACE_CONSOLE,
    SURFACE_DENIAL_LOG,
    SURFACE_DICTIONARY_STEWARDSHIP,
    SURFACE_FOLLOW_UP,
    SURFACE_OVERSIGHT,
    SURFACE_QUARANTINE,
    SURFACE_REVIEW,
    SURFACE_SIGNAL_COUNTS,
    Principal,
)
from pv_cases.assessment import clock_state
from pv_cases.models import CodingReviewStatus


def _head(items, id_attr: str):
    """The standing artefact of a supersession chain: the one
    no other item supersedes (the last such, in store order).
    """
    superseded = {
        getattr(item, "supersedes")
        for item in items
        if getattr(item, "supersedes")
    }
    heads = [
        item for item in items if getattr(item, id_attr) not in superseded
    ]
    return heads[-1] if heads else None


def _figure(sections: dict, section: str, name: str):
    figure = sections.get(section, {}).get(name)
    return None if figure is None else figure.get("value")


def _by(rows: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return counts


class ConsoleService:
    """The composed console read, bound over the services it
    composes. See the module docstring for the discipline."""

    def __init__(
        self, store, access, review, followup, assessment,
        oversight, signals, steward,
    ):
        self.store = store
        self.access = access
        self.review = review
        self.followup = followup
        self.assessment = assessment
        self.oversight = oversight
        self.signals = signals
        self.steward = steward

    # ------------------------------------------------------
    # The composition
    # ------------------------------------------------------

    def data(
        self,
        as_of: date,
        principal: Principal | None = None,
        today: date | None = None,
    ) -> dict:
        """The console at `as_of`. A carried principal is
        judged on the console surface first — the refusal is
        recorded by the access layer before it propagates —
        and every section is then composed only if the
        principal's role may read that section's own surface.
        """
        if principal is not None:
            self.access.require_read(
                principal, SURFACE_CONSOLE, on=today or as_of
            )
        sections = {
            "cases": self._section(
                principal, SURFACE_CASE, lambda: self._cases(as_of)
            ),
            "review_queue": self._section(
                principal, SURFACE_REVIEW, lambda: self._review_queue(as_of)
            ),
            "follow_ups": self._section(
                principal,
                SURFACE_FOLLOW_UP,
                lambda: self._follow_ups(as_of),
            ),
            "coding_queue": self._section(
                principal, SURFACE_CODING, self._coding_queue
            ),
            "quarantine": self._section(
                principal, SURFACE_QUARANTINE, self._quarantine
            ),
            "oversight": self._section(
                principal, SURFACE_OVERSIGHT, self._oversight
            ),
            "signals": self._section(
                principal, SURFACE_SIGNAL_COUNTS, self._signals
            ),
            "stewardship": self._section(
                principal,
                SURFACE_DICTIONARY_STEWARDSHIP,
                self._stewardship,
            ),
            "denials": self._section(
                principal, SURFACE_DENIAL_LOG, self._denials
            ),
        }
        return {
            "as_of": as_of.isoformat(),
            "principal": (
                None
                if principal is None
                else {"id": principal.id, "role": principal.role}
            ),
            "access_pack_version": self.access.pack.version,
            "sections": sections,
        }

    def _section(self, principal, surface: str, compose) -> dict:
        if principal is not None and not self.access.pack.can_read(
            principal.role, surface
        ):
            return {"visible": False}
        section = compose()
        section["visible"] = True
        return section

    # ------------------------------------------------------
    # Sections — each one a read-back of an existing surface
    # ------------------------------------------------------

    def _cases(self, as_of: date) -> dict:
        sealed = {seal.case_id for seal in self.store.list_seals()}
        rows = []
        for case in sorted(
            self.store.list_cases(), key=lambda c: c.case_id
        ):
            current = self.assessment.current_assessment(case.case_id)
            state = days = None
            if current is not None:
                state, days = clock_state(current.clock, as_of)
            rows.append(
                {
                    "case_id": case.case_id,
                    "status": case.status.value,
                    "product_code": case.product_code,
                    "received_on": case.received_on.isoformat(),
                    "seriousness": (
                        current.seriousness.value if current else None
                    ),
                    "expectedness": (
                        current.expectedness.value if current else None
                    ),
                    "obligation_class": (
                        current.clock.obligation_class if current else None
                    ),
                    "deadline": (
                        current.clock.deadline.isoformat()
                        if current and current.clock.deadline
                        else None
                    ),
                    "clock_state": state,
                    "days_remaining": days,
                    "sealed": case.case_id in sealed,
                }
            )
        return {
            "counts": {
                "total": len(rows),
                "serious": sum(
                    1 for row in rows if row["seriousness"] == "serious"
                ),
                "breached": sum(
                    1 for row in rows if row["clock_state"] == "breached"
                ),
                "sealed": sum(1 for row in rows if row["sealed"]),
                "by_status": _by(rows, "status"),
            },
            "rows": rows,
        }

    def _review_queue(self, as_of: date) -> dict:
        rows = [
            entry.model_dump(mode="json")
            for entry in self.review.queue(as_of)
        ]
        return {
            "counts": {"total": len(rows), "by_bucket": _by(rows, "bucket")},
            "rows": rows,
        }

    def _follow_ups(self, as_of: date) -> dict:
        rows = [
            entry.model_dump(mode="json")
            for entry in self.followup.board(as_of)
        ]
        return {
            "counts": {
                "total": len(rows),
                "overdue": sum(
                    1 for row in rows if row["days_overdue"] > 0
                ),
                "by_state": _by(rows, "state"),
            },
            "rows": rows,
        }

    def _coding_queue(self) -> dict:
        tasks = self.store.list_coding_tasks(
            status=CodingReviewStatus.OPEN.value
        )
        rows = [
            {
                "task_id": task.task_id,
                "case_id": task.case_id,
                "subject_kind": task.subject_kind,
                "verbatim": task.verbatim,
                "reason": task.reason,
            }
            for task in sorted(tasks, key=lambda t: t.task_id)
        ]
        return {"counts": {"open": len(rows)}, "rows": rows}

    def _quarantine(self) -> dict:
        records = self.store.list_quarantine_records()
        released = {
            act.record_id for act in self.store.list_quarantine_acts()
        }
        held = [
            record
            for record in records
            if record.findings and record.record_id not in released
        ]
        rows = [
            {
                "record_id": record.record_id,
                "case_id": record.case_id,
                "field": record.field,
                "rules": sorted(
                    {finding.rule_id for finding in record.findings}
                ),
            }
            for record in sorted(held, key=lambda r: r.record_id)
        ]
        return {
            "counts": {
                "screened": len(records),
                "held": len(rows),
                "released": sum(
                    1
                    for record in records
                    if record.findings and record.record_id in released
                ),
            },
            "rows": rows,
        }

    def _oversight(self) -> dict:
        head = _head(self.oversight.reports(), "report_id")
        if head is None:
            return {"head": None}
        return {
            "head": {
                "report_id": head.report_id,
                "window_start": head.window_start.isoformat(),
                "window_end": head.window_end.isoformat(),
                "as_of": head.as_of.isoformat(),
                "verified": head.verified,
                "cases_founded": _figure(
                    head.sections, "intake", "cases_founded"
                ),
                "expedited_7_day_breached_open": _figure(
                    head.sections, "clocks", "expedited_7_day_breached_open"
                ),
                "expedited_15_day_breached_open": _figure(
                    head.sections, "clocks", "expedited_15_day_breached_open"
                ),
            }
        }

    def _signals(self) -> dict:
        head = _head(self.signals.tables(), "table_id")
        if head is None:
            return {"head": None, "flagged": []}
        flagged = [
            {
                "product_code": pair.product_code,
                "product_name": pair.product_name,
                "preferred_term": pair.preferred_term,
                "a": pair.a,
                "prr": pair.prr,
                "chi_square": pair.chi_square,
            }
            for pair in head.pairs
            if pair.meets_criteria
        ]
        return {
            "head": {
                "table_id": head.table_id,
                "as_of": head.as_of.isoformat(),
                "verified": head.verified,
                "pairs": len(head.pairs),
                "flagged": len(flagged),
            },
            "flagged": flagged,
        }

    def _stewardship(self) -> dict:
        head = _head(self.steward.worklists(), "worklist_id")
        if head is None:
            return {"head": None, "top": []}
        top = [
            {
                "reported_wording": row.reported_wording,
                "subject_kind": row.subject_kind,
                "reason": row.reason,
                "reports": row.reports,
                "dictionary_state": row.dictionary_state,
            }
            for row in head.rows[:10]
        ]
        return {
            "head": {
                "worklist_id": head.worklist_id,
                "as_of": head.as_of.isoformat(),
                "verified": head.verified,
                "base_dictionary_version": head.base_dictionary_version,
                "rows": len(head.rows),
            },
            "top": top,
        }

    def _denials(self) -> dict:
        denials = self.store.list_access_denials()
        recent = [
            {
                "denial_id": denial.denial_id,
                "principal_id": denial.principal_id,
                "role": denial.role,
                "surface": denial.surface,
                "reason": denial.reason,
                "denied_on": denial.denied_on.isoformat(),
            }
            for denial in denials[-5:][::-1]
        ]
        return {"counts": {"total": len(denials)}, "recent": recent}
