"""Access control — principals, roles, existence hiding (R10).

Rounds 1–9 built the engines and their gates: the validity gate
decides what a case *is*, dual control decides who must *agree*,
the gateway pack decides what a *tool call* may do, the
quarantine decides what *text* may be read as data. None of them
answered the prior question — WHO is calling the case surfaces,
and what may that caller see? Every surface took actor strings
on trust: a review names its reviewer, a release its reviewer,
but nothing checked that the caller was entitled to the case in
front of them, and a case id was a capability — anyone who could
guess or learn one could read the file. This is the layer the
Round 9 red team pointed at when it left RT-07 open: quarantine
release had no role separation because *there was no role
registry to check against*. This module is that registry.

It follows the repo's one pattern: the rules are a versioned
pack, evaluated in code, and every refusal is a recorded
artefact.

- **The access pack (2026.10.1, below)** states, per role, the
  surfaces the role may read and the acts it may perform, and —
  for refusal messages — the roles each act requires. A
  permission change is a new pack version, never an edit; every
  denial names the pack version it was taken under.
- **A principal is an (id, role) pair.** Surfaces carry it
  explicitly: `X-Principal-Id` / `X-Principal-Role` headers on
  the API, a `principal` object on a Runtime payload, a
  `principal=` argument on the service methods that record acts.
  A carried principal is always authenticated against the pack:
  the pair is judged *as carried* (padded or partial pairs are
  malformed, never trimmed into validity), and an unknown role
  is refused with a named error — there is no default principal
  and no silent downgrade at this layer. **Enforcement governs
  calls that carry a principal.** The pre-R10 calls that carry
  none remain the trusted local operations path, exactly as
  they have been since Round 1 — in a deployment, AgentCore
  Identity terminates caller identity in front of these
  surfaces and every call presents a principal. The README
  states this scope plainly.
- **A carried principal binds the act's actor.** Where an act
  names its human (a review's reviewer, a release's
  `released_by`, a seal's `sealed_by`), a carried principal's id
  must be that human, compared under the repo's
  `normalise_text` folding — being authenticated as yourself
  does not let you sign someone else's name.
- **Case reads are existence-hidden.** A principal whose role
  has no read scope over cases (today: intake processors, who
  submit work, and case admins, who tend reference data) gets,
  for any case id, exactly the response a non-existent case
  earns — byte for byte, on every case-scoped read surface.
  List surfaces filter instead: a role that cannot read cases
  sees an empty list, never a count. A case id cannot be probed
  for existence by anyone the pack does not entitle to see it.
- **The auditor reads everything and does nothing.** Every
  surface — case files, gateway calls, the denial log itself —
  is readable; every act is refused, and the refusal recorded
  like any other.
- **Quarantine release is a reviewing act now (RT-07).** The
  pack grants it to the safety scientist and the two review
  roles — not to intake, not to admin, not to the auditor. And
  a release may not come from the held text's own reporter of
  record where the record can tell: the report payload on the
  chain names its reporter, and a releaser whose name folds to
  that name is refused. Where the report names no reporter,
  the record cannot tell, and the check does not fire — that
  limit is stated, not papered over.
- **Dual control is role-bound where a principal is carried.**
  A review act's role *is* a pack role (`medical_reviewer`,
  `safety_reviewer` — the vocabularies were aligned in Rounds
  6 and 8), so a principal recording a medical accept must hold
  the medical reviewer role, and the safety accept must come
  from a second principal holding the safety role: two distinct
  normalised identities (Round 9) *and* two pack-authorised
  roles.
- **Every refusal is an artefact.** A denial lands in the
  insert-only denial log (migration 0009) BEFORE the error
  propagates: principal, role, surface, target, reason, pack
  version. The log is itself a surface (auditor + case admin).
  A denial never leaks what it protects: for a hidden case the
  recorded target is the requested id string — which the
  requester supplied — and no field of the denial says whether
  the case exists.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Mapping

from pydantic import BaseModel

from pv_cases.evidence import canonical_json
from pv_cases.models import normalise_text

ACCESS_PACK_VERSION = "2026.10.1"

ROLE_INTAKE_PROCESSOR = "intake_processor"
ROLE_SAFETY_SCIENTIST = "safety_scientist"
ROLE_SAFETY_REVIEWER = "safety_reviewer"
ROLE_MEDICAL_REVIEWER = "medical_reviewer"
ROLE_QUALITY_AUDITOR = "quality_auditor"
ROLE_CASE_ADMIN = "case_admin"
ROLES = [
    ROLE_INTAKE_PROCESSOR,
    ROLE_SAFETY_SCIENTIST,
    ROLE_SAFETY_REVIEWER,
    ROLE_MEDICAL_REVIEWER,
    ROLE_QUALITY_AUDITOR,
    ROLE_CASE_ADMIN,
]

# Readable surfaces — the closed vocabulary the pack grants over.
SURFACE_CASE = "case"
SURFACE_EVIDENCE = "evidence"
SURFACE_SUMMARY = "summary"
SURFACE_CODING = "coding"
SURFACE_DICTIONARY = "dictionary"
SURFACE_ASSESSMENT = "assessment"
SURFACE_FOLLOW_UP = "follow_up"
SURFACE_REVIEW = "review"
SURFACE_SEALING = "sealing"
SURFACE_QUARANTINE = "quarantine"
SURFACE_MEMORY = "memory"
SURFACE_GATEWAY = "gateway"
SURFACE_HISTORY = "history"
SURFACE_DEDUP = "dedup"
SURFACE_DENIAL_LOG = "denial_log"
SURFACE_PACKS = "packs"
# Round 11: the safety oversight reports — aggregate-only
# artefacts, but safety data nonetheless: the case-working
# roles and the auditor read them; intake and case admin do
# not (an aggregate is not a licence either).
SURFACE_OVERSIGHT = "oversight"
# Round 12: the model gateway's cost ledger — a case-scoped
# read surface like the summary it prices: the case-working
# roles and the auditor read a case's ledger; intake and case
# admin do not, and a refused read is existence-hidden exactly
# as every other case-scoped read is.
SURFACE_MODEL_LEDGER = "model_ledger"
SURFACES = [
    SURFACE_CASE,
    SURFACE_EVIDENCE,
    SURFACE_SUMMARY,
    SURFACE_CODING,
    SURFACE_DICTIONARY,
    SURFACE_ASSESSMENT,
    SURFACE_FOLLOW_UP,
    SURFACE_REVIEW,
    SURFACE_SEALING,
    SURFACE_QUARANTINE,
    SURFACE_MEMORY,
    SURFACE_GATEWAY,
    SURFACE_HISTORY,
    SURFACE_DEDUP,
    SURFACE_DENIAL_LOG,
    SURFACE_PACKS,
    SURFACE_OVERSIGHT,
    SURFACE_MODEL_LEDGER,
]

# Acts — the named operations the pack gates. These are the
# surface-level acts; the engines keep their own gates behind
# them (the validity gate, dual control, the seal gate, the
# gateway pack).
ACT_INTAKE_REPORT = "intake_report"
ACT_SUBMIT_FOLLOW_UP = "submit_follow_up"
ACT_ASSEMBLE_SUMMARY = "assemble_summary"
ACT_CODE_CASE = "code_case"
ACT_DEDUP_SCAN = "dedup_scan"
ACT_DECIDE_DEDUP_LINK = "decide_dedup_link"
ACT_RESOLVE_CODING_TASK = "resolve_coding_task"
ACT_PROPOSE_DICTIONARY_ADDITION = "propose_dictionary_addition"
ACT_RECORD_REVIEW = "record_review"
ACT_SEAL_CASE = "seal_case"
ACT_EXPORT_CASE = "export_case"
ACT_RELEASE_QUARANTINE = "release_quarantine"
ACT_RECALL_MEMORY = "recall_memory"
# Round 11: generating an oversight report is a named act —
# the safety scientist's, the operational case-working role.
# The auditor reads reports; it generates none.
ACT_GENERATE_OVERSIGHT_REPORT = "generate_oversight_report"
ACTS = [
    ACT_INTAKE_REPORT,
    ACT_SUBMIT_FOLLOW_UP,
    ACT_ASSEMBLE_SUMMARY,
    ACT_CODE_CASE,
    ACT_DEDUP_SCAN,
    ACT_DECIDE_DEDUP_LINK,
    ACT_RESOLVE_CODING_TASK,
    ACT_PROPOSE_DICTIONARY_ADDITION,
    ACT_RECORD_REVIEW,
    ACT_SEAL_CASE,
    ACT_EXPORT_CASE,
    ACT_RELEASE_QUARANTINE,
    ACT_RECALL_MEMORY,
    ACT_GENERATE_OVERSIGHT_REPORT,
]

# Denial reasons — the closed vocabulary of the denial log.
REASON_PRINCIPAL_REQUIRED = "principal_required"
REASON_MALFORMED_PRINCIPAL = "malformed_principal"
REASON_UNKNOWN_ROLE = "unknown_role"
REASON_SURFACE_NOT_READABLE = "surface_not_readable"
REASON_ACT_NOT_PERMITTED = "act_not_permitted"
REASON_READ_ONLY_ROLE = "read_only_role"
REASON_CASE_NOT_VISIBLE = "case_not_visible"
REASON_ACTOR_MISMATCH = "actor_mismatch"
REASON_SELF_RELEASE = "self_release"

# The roles the release act takes (RT-07): the people who judge
# case text, not the people who submit or administer it.
RELEASE_ROLES = [
    ROLE_SAFETY_SCIENTIST,
    ROLE_SAFETY_REVIEWER,
    ROLE_MEDICAL_REVIEWER,
]


class AccessDeniedError(RuntimeError):
    """An access refusal — the message names the gap (the
    malformed pair, the unknown role, the role the act
    requires). The denial artefact is recorded before this is
    raised, so catching it never erases the record. ``hidden``
    marks a case-visibility refusal: the surface must answer it
    as it answers a non-existent case, never as a refusal."""

    def __init__(self, reason: str, message: str, hidden: bool = False):
        super().__init__(message)
        self.reason = reason
        self.hidden = hidden


class Principal(BaseModel):
    """A caller, as the surfaces carry it: a stable id (the
    person's own name, in this repo's name-string identity
    model) and one pack role."""

    id: str
    role: str


class RoleGrant(BaseModel):
    """One role's standing in the pack: the surfaces it may read
    and the acts it may perform."""

    role: str
    read_surfaces: list[str]
    acts: list[str]


class AccessPack(BaseModel):
    """The access pack as data: role grants, plus — for refusal
    messages that must name what an act requires — the roles
    each act takes, stated once here rather than derived at
    call sites."""

    version: str
    roles: list[RoleGrant]
    act_requirements: dict[str, list[str]]

    def grant(self, role: str) -> RoleGrant | None:
        for grant in self.roles:
            if grant.role == role:
                return grant
        return None

    def can_read(self, role: str, surface: str) -> bool:
        grant = self.grant(role)
        return grant is not None and surface in grant.read_surfaces

    def can_act(self, role: str, act: str) -> bool:
        grant = self.grant(role)
        return grant is not None and act in grant.acts

    def required_roles(self, act: str) -> list[str]:
        return list(self.act_requirements.get(act, []))


# The case-working roles read the whole case file: their work is
# the file. What separates them is which acts they may perform.
_CASEWORK_READS = [
    SURFACE_CASE,
    SURFACE_EVIDENCE,
    SURFACE_SUMMARY,
    SURFACE_CODING,
    SURFACE_DICTIONARY,
    SURFACE_ASSESSMENT,
    SURFACE_FOLLOW_UP,
    SURFACE_REVIEW,
    SURFACE_SEALING,
    SURFACE_QUARANTINE,
    SURFACE_MEMORY,
    SURFACE_GATEWAY,
    SURFACE_HISTORY,
    SURFACE_DEDUP,
    SURFACE_PACKS,
    SURFACE_OVERSIGHT,
    SURFACE_MODEL_LEDGER,
]

ACCESS_PACK_V1 = AccessPack(
    version=ACCESS_PACK_VERSION,
    roles=[
        RoleGrant(
            role=ROLE_INTAKE_PROCESSOR,
            # Intake submits reports and answers follow-ups. It
            # reads its own work queue — never case files: the
            # reports it handles are submissions, not a licence
            # to browse the book.
            read_surfaces=[SURFACE_FOLLOW_UP, SURFACE_PACKS],
            acts=[ACT_INTAKE_REPORT, ACT_SUBMIT_FOLLOW_UP],
        ),
        RoleGrant(
            role=ROLE_SAFETY_SCIENTIST,
            read_surfaces=list(_CASEWORK_READS),
            acts=[
                ACT_SUBMIT_FOLLOW_UP,
                ACT_ASSEMBLE_SUMMARY,
                ACT_CODE_CASE,
                ACT_DEDUP_SCAN,
                ACT_DECIDE_DEDUP_LINK,
                ACT_RESOLVE_CODING_TASK,
                ACT_RELEASE_QUARANTINE,
                ACT_RECALL_MEMORY,
                ACT_GENERATE_OVERSIGHT_REPORT,
            ],
        ),
        RoleGrant(
            role=ROLE_SAFETY_REVIEWER,
            read_surfaces=list(_CASEWORK_READS),
            acts=[
                ACT_RECORD_REVIEW,
                ACT_RELEASE_QUARANTINE,
                ACT_RECALL_MEMORY,
            ],
        ),
        RoleGrant(
            role=ROLE_MEDICAL_REVIEWER,
            read_surfaces=list(_CASEWORK_READS),
            acts=[
                ACT_RECORD_REVIEW,
                ACT_SEAL_CASE,
                ACT_EXPORT_CASE,
                ACT_RELEASE_QUARANTINE,
                ACT_RECALL_MEMORY,
            ],
        ),
        RoleGrant(
            role=ROLE_QUALITY_AUDITOR,
            # Everything readable, nothing performable: oversight
            # is a read. The denial log is on this list — the
            # auditor audits refusals too.
            read_surfaces=list(SURFACES),
            acts=[],
        ),
        RoleGrant(
            role=ROLE_CASE_ADMIN,
            # Reference data and system record, not case files:
            # administering the dictionary is not a licence to
            # read patients' cases (Law 6, minimisation).
            read_surfaces=[
                SURFACE_DICTIONARY,
                SURFACE_GATEWAY,
                SURFACE_DENIAL_LOG,
                SURFACE_PACKS,
            ],
            acts=[ACT_PROPOSE_DICTIONARY_ADDITION],
        ),
    ],
    act_requirements={
        ACT_INTAKE_REPORT: [ROLE_INTAKE_PROCESSOR],
        ACT_SUBMIT_FOLLOW_UP: [ROLE_INTAKE_PROCESSOR, ROLE_SAFETY_SCIENTIST],
        ACT_ASSEMBLE_SUMMARY: [ROLE_SAFETY_SCIENTIST],
        ACT_CODE_CASE: [ROLE_SAFETY_SCIENTIST],
        ACT_DEDUP_SCAN: [ROLE_SAFETY_SCIENTIST],
        ACT_DECIDE_DEDUP_LINK: [ROLE_SAFETY_SCIENTIST],
        ACT_RESOLVE_CODING_TASK: [ROLE_SAFETY_SCIENTIST],
        ACT_PROPOSE_DICTIONARY_ADDITION: [ROLE_CASE_ADMIN],
        ACT_RECORD_REVIEW: [ROLE_MEDICAL_REVIEWER, ROLE_SAFETY_REVIEWER],
        ACT_SEAL_CASE: [ROLE_MEDICAL_REVIEWER],
        ACT_EXPORT_CASE: [ROLE_MEDICAL_REVIEWER],
        ACT_RELEASE_QUARANTINE: list(RELEASE_ROLES),
        ACT_RECALL_MEMORY: [
            ROLE_SAFETY_SCIENTIST,
            ROLE_SAFETY_REVIEWER,
            ROLE_MEDICAL_REVIEWER,
        ],
        ACT_GENERATE_OVERSIGHT_REPORT: [ROLE_SAFETY_SCIENTIST],
    },
)


def load_access_pack(version: str | None = None) -> AccessPack:
    """The loader, fail-closed like every pack loader in this
    repo: an unknown version is an error, never a silent fall
    back to v1."""
    if version is None or version == ACCESS_PACK_V1.version:
        return ACCESS_PACK_V1
    raise ValueError(f"unknown access pack version: {version!r}")


def access_pack_fingerprint(pack: AccessPack | None = None) -> str:
    """A content fingerprint of the pack: sha256 over its
    canonical JSON. Two packs with the same version string and
    different content fingerprint differently — the property the
    gateway's per-call fingerprint (RT-15) relies on."""
    pack = pack or ACCESS_PACK_V1
    return hashlib.sha256(
        canonical_json(pack.model_dump(mode="json"))
    ).hexdigest()


class AccessDenial(BaseModel):
    """One recorded refusal. Insert-only: a denial is history,
    never state to rewrite. `target` is the identifier the
    requester supplied (a case id, a record id, an act name) —
    for a hidden case it is the requested string and nothing in
    the record says whether the case exists.
- **Round 11 joins this pack.** The oversight surface and the
  report-generation act were added when the surface itself
  landed (Round 11), under this same version string — the
  pack's content fingerprint (see below) is what separates
  the pack's content before and after, the same property the
  gateway's per-call fingerprint relies on (RT-15).
"""

    denial_id: str
    seq: int  # the log's append order (the count at write time)
    principal_id: str
    role: str
    surface: str
    target: str
    reason: str
    detail: str
    pack_version: str
    denied_on: date


class AccessControl:
    """The guard, bound over the store's denial log. The wiring
    holds one and binds it onto the act-recording services; the
    surfaces reach it through `svc().access`. Every check that
    refuses records its denial BEFORE raising, through the one
    `deny` path — a refusal that is not on the log does not
    exist as far as this layer is concerned."""

    def __init__(self, store, pack: AccessPack | None = None):
        self._store = store
        self.pack = pack or load_access_pack()

    # -- authentication ---------------------------------------------
    def authenticate(
        self,
        raw: Mapping[str, Any] | None,
        *,
        surface: str,
        target: str = "",
        on: date | None = None,
    ) -> Principal:
        """Validate a carried principal against the pack. A
        missing pair, a malformed pair and an unknown role are
        each refused with their named reason — there is no
        default principal and no silent downgrade. The pair is
        judged AS CARRIED: the role vocabulary is exact strings
        and an id is the caller's own identifier, not a string
        to normalise into validity."""
        if raw is None:
            self.deny(
                principal_id="",
                role="",
                surface=surface,
                target=target,
                reason=REASON_PRINCIPAL_REQUIRED,
                detail=(
                    "no principal on the call — a principal (id + role)"
                    " is required; there is no default principal"
                ),
                on=on,
            )
        principal_id = raw.get("id") if isinstance(raw, Mapping) else None
        role = raw.get("role") if isinstance(raw, Mapping) else None
        if (
            not isinstance(principal_id, str)
            or not principal_id.strip()
            or principal_id != principal_id.strip()
            or not isinstance(role, str)
            or not role.strip()
            or role != role.strip()
        ):
            self.deny(
                principal_id=str(principal_id or ""),
                role=str(role or ""),
                surface=surface,
                target=target,
                reason=REASON_MALFORMED_PRINCIPAL,
                detail=(
                    "a principal is an (id, role) pair with both parts"
                    " named exactly — no surrounding whitespace"
                ),
                on=on,
            )
        if self.pack.grant(role) is None:
            self.deny(
                principal_id=principal_id,
                role=role,
                surface=surface,
                target=target,
                reason=REASON_UNKNOWN_ROLE,
                detail=(
                    f"unknown role {role!r} — access pack"
                    f" {self.pack.version} knows: {', '.join(ROLES)}"
                ),
                on=on,
            )
        return Principal(id=principal_id, role=role)

    # -- authorisation ------------------------------------------------
    def require_read(
        self,
        principal: Principal,
        surface: str,
        *,
        target: str = "",
        on: date | None = None,
    ) -> None:
        if not self.pack.can_read(principal.role, surface):
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=target,
                reason=REASON_SURFACE_NOT_READABLE,
                detail=(
                    f"surface {surface} is not readable by role"
                    f" {principal.role}"
                ),
                on=on,
            )

    def require_case_read(
        self,
        principal: Principal,
        surface: str,
        *,
        case_id: str,
        on: date | None = None,
    ) -> None:
        """A case-scoped read. Refused as CASE_NOT_VISIBLE —
        hidden — whenever the role may not read cases at all or
        may not read this surface: the surface answers it
        exactly as it answers a non-existent case. The denial
        is still recorded (the log is not a case surface; it
        names only the id the requester supplied)."""
        if not self.pack.can_read(
            principal.role, SURFACE_CASE
        ) or not self.pack.can_read(principal.role, surface):
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=case_id,
                reason=REASON_CASE_NOT_VISIBLE,
                detail=(
                    f"case reads are not in role {principal.role}'s"
                    f" scope (surface {surface}) — answered as a"
                    " non-existent case"
                ),
                on=on,
                hidden=True,
            )

    def require_act(
        self,
        principal: Principal,
        act: str,
        *,
        surface: str,
        target: str = "",
        on: date | None = None,
    ) -> None:
        if self.pack.can_act(principal.role, act):
            return
        if principal.role == ROLE_QUALITY_AUDITOR:
            self.deny(
                principal_id=principal.id,
                role=principal.role,
                surface=surface,
                target=target,
                reason=REASON_READ_ONLY_ROLE,
                detail=(
                    f"role {ROLE_QUALITY_AUDITOR} is read-only: it"
                    f" performs no acts (attempted act: {act})"
                ),
                on=on,
            )
        required = self.pack.required_roles(act)
        self.deny(
            principal_id=principal.id,
            role=principal.role,
            surface=surface,
            target=target,
            reason=REASON_ACT_NOT_PERMITTED,
            detail=(
                f"act {act} requires role"
                f" {' or '.join(required) if required else '(none in the pack)'};"
                f" principal {principal.id} holds {principal.role}"
            ),
            on=on,
        )

    def require_actor(
        self,
        principal: Principal,
        actor: str,
        *,
        act: str,
        surface: str,
        target: str = "",
        on: date | None = None,
    ) -> None:
        """A carried principal binds the act's named human: the
        principal's id must be the actor the act names, compared
        under `normalise_text` folding — the same identity model
        dual control uses (R9, RT-25)."""
        if normalise_text(principal.id) == normalise_text(actor or ""):
            return
        self.deny(
            principal_id=principal.id,
            role=principal.role,
            surface=surface,
            target=target,
            reason=REASON_ACTOR_MISMATCH,
            detail=(
                f"act {act} names {actor!r} as its human; principal"
                f" {principal.id} may not sign another person's act"
            ),
            on=on,
        )

    # -- the one recording path ---------------------------------------
    def deny(
        self,
        *,
        principal_id: str,
        role: str,
        surface: str,
        target: str,
        reason: str,
        detail: str,
        on: date | None = None,
        hidden: bool = False,
    ) -> None:
        """Record the denial, then raise it. Always raises."""
        denied_on = on or date.today()
        seq = self._store.count_access_denials()
        blob = canonical_json(
            {
                "seq": seq,
                "principal_id": principal_id,
                "role": role,
                "surface": surface,
                "target": target,
                "reason": reason,
                "denied_on": denied_on.isoformat(),
                "pack_version": self.pack.version,
            }
        )
        denial = AccessDenial(
            denial_id="AD-" + hashlib.sha256(blob).hexdigest()[:16],
            seq=seq,
            principal_id=principal_id,
            role=role,
            surface=surface,
            target=target,
            reason=reason,
            detail=detail,
            pack_version=self.pack.version,
            denied_on=denied_on,
        )
        self._store.add_access_denial(denial)
        raise AccessDeniedError(reason, f"access denied: {detail}", hidden=hidden)

    def denials(self) -> list[AccessDenial]:
        return self._store.list_access_denials()


def control_for(service) -> AccessControl:
    """The access control a service judges with: the one the
    wiring bound onto it, or — for a service constructed bare,
    as some unit tests do — one built over its store on first
    use and kept. Either way the denial log is the service's
    own store's."""
    control = getattr(service, "access", None)
    if control is None:
        control = AccessControl(service.store)
        service.access = control
    return control


def guard_act(
    service,
    principal: Principal | None,
    act: str,
    *,
    surface: str,
    target: str = "",
    actor: str | None = None,
    on: date | None = None,
) -> None:
    """The one-line judge the act-recording services run when a
    call carries a principal: the role must hold the act, and —
    where the act names its human — the principal must be that
    human. A principal-less call is the trusted local path and
    passes untouched."""
    if principal is None:
        return
    control = control_for(service)
    control.require_act(principal, act, surface=surface, target=target, on=on)
    if actor is not None:
        control.require_actor(
            principal, actor, act=act, surface=surface, target=target, on=on
        )
