"""Change plans: a reviewable, reversible description of a batch update.

A :class:`ChangePlan` records, for every entity it touches, the values it held when the
plan was built and the values the plan sets. That gives three things:

* **Review.** :meth:`ChangePlan.table` and :meth:`ChangePlan.to_markdown` show exactly
  what will change before anything is sent.
* **Rollback.** :meth:`ChangePlan.inverse` is a plan that puts the before values back.
  Saved as JSON (:meth:`ChangePlan.save`), a plan is a rollback token.
* **Integrity.** :attr:`ChangePlan.fingerprint` is a hash of the plan's content, so a
  caller that approved one fingerprint can refuse to apply anything else.

A plan holds three kinds of operation: ``update`` (before and after values), ``create``
(no before; the reverse archives the ids Amazon assigned) and ``archive`` (irreversible:
Amazon cannot re-enable an archived entity). The most useful rollback token is therefore
the one built *after* applying, :meth:`PlanResult.rollback_plan`, which reverses exactly
the items Amazon accepted.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amazon_ads.batch import BatchResult
from amazon_ads.errors import PlanDriftError

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

PLAN_FORMAT = 1


OPS = ("update", "create", "archive")


@dataclass(frozen=True)
class PlannedChange:
    kind: str
    id: str | None
    before: dict[str, Any]
    after: dict[str, Any]
    label: str | None = None
    op: str = "update"


@dataclass
class PlanResult:
    """Per-operation batch results of :meth:`ChangePlan.apply`, keyed ``"<op> <kind>"``."""

    plan: ChangePlan
    results: dict[str, BatchResult[dict[str, Any]]]

    @property
    def plan_id(self) -> str:
        return self.plan.id

    def rollback_plan(self) -> ChangePlan:
        """A plan that undoes exactly what Amazon accepted.

        Updates that succeeded are reversed to their before values; creates that succeeded
        are archived by the ids Amazon assigned; failed items are left out because they
        changed nothing. Archives cannot be reversed and are listed in ``warnings``.
        """
        changes: list[PlannedChange] = []
        warnings: list[str] = []
        for key, result in self.results.items():
            op, _, kind = key.partition(" ")
            for success in result.successes:
                change = self._changes_by_key[key][success.index]
                if op == "update":
                    inverted, warning = _invert_update(change)
                    if inverted:
                        changes.append(inverted)
                    if warning:
                        warnings.append(warning)
                elif op == "create" and success.id:
                    changes.append(
                        PlannedChange(
                            kind=kind,
                            id=success.id,
                            before={"state": change.after.get("state")},
                            after={"state": "ARCHIVED"},
                            label=change.label,
                            op="archive",
                        )
                    )
                elif op == "archive":
                    warnings.append(f"{kind} {change.id} was archived; Amazon cannot undo that")
        return ChangePlan.new(
            profile_id=self.plan.profile_id,
            country_code=self.plan.country_code,
            changes=changes,
            note=f"Rolls back applied plan {self.plan.id}",
            reverses=self.plan.id,
            warnings=warnings,
        )

    @property
    def _changes_by_key(self) -> dict[str, list[PlannedChange]]:
        return self.plan.grouped()

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results.values())

    def summary(self) -> str:
        return "; ".join(f"{kind}: {r.summary()}" for kind, r in self.results.items())

    def raise_for_errors(self) -> PlanResult:
        for result in self.results.values():
            result.raise_for_errors()
        return self


@dataclass
class ChangePlan:
    id: str
    created_at: str
    profile_id: int | None
    country_code: str | None
    changes: list[PlannedChange]
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    note: str | None = None
    reverses: str | None = None

    @classmethod
    def new(
        cls,
        *,
        profile_id: int | None,
        country_code: str | None,
        changes: Sequence[PlannedChange],
        missing: Sequence[str] = (),
        note: str | None = None,
        reverses: str | None = None,
        warnings: Sequence[str] = (),
    ) -> ChangePlan:
        return cls(
            id=uuid.uuid4().hex[:12],
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            profile_id=profile_id,
            country_code=country_code,
            changes=list(changes),
            missing=list(missing),
            warnings=list(warnings),
            note=note,
            reverses=reverses,
        )

    # --- review -------------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.changes)

    @property
    def fingerprint(self) -> str:
        """SHA-256 over the profile and the changes, independent of id and timestamps."""
        content = {
            "profile_id": self.profile_id,
            "changes": [asdict(c) for c in self.changes],
        }
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def table(self) -> list[dict[str, Any]]:
        """One row per changed field: kind, id, label, field, before, after."""
        rows = []
        for change in self.changes:
            for key, new in change.after.items():
                rows.append(
                    {
                        "op": change.op,
                        "kind": change.kind,
                        "id": change.id,
                        "label": change.label,
                        "field": key,
                        "before": change.before.get(key),
                        "after": new,
                    }
                )
        return rows

    def to_markdown(self) -> str:
        header = f"Plan {self.id} for {self.country_code or ''} profile {self.profile_id}"
        lines = [header, ""]
        if self.note:
            lines += [self.note, ""]
        lines += [
            "| op | kind | id | label | field | before | after |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in self.table():
            cells = [
                row["op"],
                row["kind"],
                row["id"] or "(new)",
                row["label"] or "",
                row["field"],
                _fmt(row["before"]),
                _fmt(row["after"]),
            ]
            lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |")
        if self.missing:
            lines += ["", f"Not found in the account, left out: {', '.join(self.missing)}"]
        for warning in self.warnings:
            lines += ["", f"Warning: {warning}"]
        lines += ["", f"Fingerprint {self.fingerprint}"]
        return "\n".join(lines)

    # --- rollback -----------------------------------------------------------------------

    def inverse(self) -> ChangePlan:
        """A plan that restores every before value.

        A field that had no value before (for example a keyword bid left to the ad group
        default) cannot be restored by an update, because Amazon's handling of an explicit
        ``null`` is undocumented. Such fields are left out and listed in ``warnings``.
        """
        changes: list[PlannedChange] = []
        warnings: list[str] = []
        for change in self.changes:
            if change.op != "update":
                warnings.append(
                    f"{change.op} of {change.kind} {change.id or change.label} is not reversed"
                    " here; use PlanResult.rollback_plan() after applying"
                )
                continue
            inverted, warning = _invert_update(change)
            if inverted:
                changes.append(inverted)
            if warning:
                warnings.append(warning)
        return ChangePlan.new(
            profile_id=self.profile_id,
            country_code=self.country_code,
            changes=changes,
            note=f"Reverses plan {self.id}",
            reverses=self.id,
            warnings=warnings,
        )

    # --- apply --------------------------------------------------------------------------

    def apply(self, client: ProfileClient, *, check_drift: bool = False) -> PlanResult:
        """Send the plan's updates through ``client``.

        ``check_drift=True`` first re-reads every entity and raises
        :class:`~amazon_ads.errors.PlanDriftError` if any field no longer holds its before
        value, so a plan reviewed an hour ago cannot silently overwrite a newer change.
        """
        if self.profile_id is not None and int(client.profile_id) != int(self.profile_id):
            raise ValueError(
                f"Plan {self.id} was built for profile {self.profile_id}, not {client.profile_id}"
            )
        groups = self.grouped()

        if check_drift:
            drifted: list[dict[str, Any]] = []
            for key, changes in groups.items():
                op, _, kind = key.partition(" ")
                if op == "create":
                    continue
                resource = client.resource(kind)
                current = resource.get_many([str(c.id) for c in changes])
                for change in changes:
                    entity = current.get(str(change.id))
                    now = entity.to_api() if entity is not None else {}
                    for key, expected in change.before.items():
                        if now.get(key) != expected:
                            drifted.append(
                                {
                                    "kind": kind,
                                    "id": change.id,
                                    "field": key,
                                    "expected": expected,
                                    "actual": now.get(key),
                                }
                            )
            if drifted:
                raise PlanDriftError(
                    f"{len(drifted)} field(s) changed since plan {self.id} was built",
                    drifted=drifted,
                )

        results: dict[str, BatchResult[dict[str, Any]]] = {}
        for key, changes in groups.items():
            op, _, kind = key.partition(" ")
            resource = client.resource(kind)
            if op == "update":
                payloads = [{resource.spec.id_field: c.id, **c.after} for c in changes]
                results[key] = resource.update(payloads)
            elif op == "create":
                results[key] = resource.create([c.after for c in changes])
            elif op == "archive":
                results[key] = resource.delete([str(c.id) for c in changes])
            else:
                raise ValueError(f"Unknown plan operation {op!r}")
        return PlanResult(plan=self, results=results)

    def grouped(self) -> dict[str, list[PlannedChange]]:
        """Changes grouped by ``"<op> <kind>"`` in first-seen order, as they are sent."""
        groups: dict[str, list[PlannedChange]] = {}
        for change in self.changes:
            if change.op not in OPS:
                raise ValueError(f"Unknown plan operation {change.op!r}")
            groups.setdefault(f"{change.op} {change.kind}", []).append(change)
        return groups

    # --- persistence --------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["format"] = PLAN_FORMAT
        data["fingerprint"] = self.fingerprint
        return data

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: str | Path) -> Path:
        """Write the plan as JSON. A directory (existing, or a path ending in a slash or
        without a file extension) gets ``plan-<id>.json`` inside it."""
        p = Path(path).expanduser()
        if p.is_dir() or str(path).endswith(("/", "\\")) or not p.suffix:
            p = p / f"plan-{self.id}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json() + "\n")
        return p

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangePlan:
        plan = cls(
            id=data["id"],
            created_at=data["created_at"],
            profile_id=data.get("profile_id"),
            country_code=data.get("country_code"),
            changes=[PlannedChange(**c) for c in data.get("changes", [])],
            missing=list(data.get("missing", [])),
            warnings=list(data.get("warnings", [])),
            note=data.get("note"),
            reverses=data.get("reverses"),
        )
        stored = data.get("fingerprint")
        if stored and stored != plan.fingerprint:
            raise ValueError(f"Plan {plan.id} was edited after it was saved (fingerprint mismatch)")
        return plan

    @classmethod
    def from_json(cls, text: str) -> ChangePlan:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> ChangePlan:
        return cls.from_json(Path(path).expanduser().read_text())


def _invert_update(change: PlannedChange) -> tuple[PlannedChange | None, str | None]:
    """Reverse one update. Fields with no previous value cannot be restored by an update,
    because Amazon's handling of an explicit ``null`` is undocumented, so they are skipped
    and reported."""
    restorable = {k: v for k, v in change.before.items() if v is not None}
    skipped = [k for k, v in change.before.items() if v is None]
    warning = (
        f"{change.kind} {change.id}: no previous value for {', '.join(skipped)}"
        if skipped
        else None
    )
    if not restorable:
        return None, warning
    return (
        PlannedChange(
            kind=change.kind,
            id=change.id,
            before={k: change.after.get(k) for k in restorable},
            after=restorable,
            label=change.label,
        ),
        warning,
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)
