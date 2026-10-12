"""Application services: validation, persistence, scheduling sync, dashboard alerts, finance, export."""
from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional, Sequence

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy import func

from constants import (
    ACTIVE_BREEDING, CLOSED_BREEDING, COST_CATEGORIES, JUVENILE_STAGES, LACTATING_STAGE, METRICS,
    OFFSPRING_STAGE, SERVICE_METHODS, SEXES, SPECIES, STAGES, STATUSES, WEANER_STAGE,
)
from models import Animal, BreedingEvent, CostRecord, HusbandryTask, ProductionLog
from services import biological_engine as eng

logger = logging.getLogger("farm.service")


class ValidationError(ValueError):
    """Raised for invalid user input (shown to the user, never a crash)."""


class InbreedingRiskError(ValidationError):
    def __init__(self, warnings: Sequence[str]):
        super().__init__("; ".join(warnings))
        self.warnings = list(warnings)


def _today(today: Optional[date]) -> date:
    return today or date.today()


# ======================================================================================
# Animals
# ======================================================================================
def get_animal(session: Session, animal_id: int) -> Animal:
    animal = session.get(Animal, animal_id)
    if animal is None:
        raise ValidationError(f"Animal id {animal_id} not found.")
    return animal


def animal_label(a: Animal) -> str:
    return f"{a.tag_id} - {a.animal_name}" if a.animal_name else a.tag_id


def build_parent_map(session: Session) -> dict[int, tuple[Optional[int], Optional[int]]]:
    return {i: (d, s) for i, d, s in session.execute(select(Animal.id, Animal.dam_id, Animal.sire_id))}


def _validate_basic(species: str, sex: str, stage: Optional[str], status: str, birth: Optional[date], today: date) -> None:
    if species not in SPECIES:
        raise ValidationError(f"Species must be one of {', '.join(SPECIES)}.")
    if sex not in SEXES:
        raise ValidationError(f"Sex must be one of {', '.join(SEXES)}.")
    if status not in STATUSES:
        raise ValidationError(f"Status must be one of {', '.join(STATUSES)}.")
    if stage and stage not in STAGES[species]:
        raise ValidationError(f"Stage '{stage}' is not valid for {species}.")
    if birth and birth > today:
        raise ValidationError("Birth date cannot be in the future.")


def _validate_parents(session: Session, species: str, animal_id: Optional[int], dam_id: Optional[int], sire_id: Optional[int]) -> None:
    if dam_id is not None:
        dam = session.get(Animal, dam_id)
        if dam is None or dam.species != species or dam.sex != "Female":
            raise ValidationError("Dam must be an existing female of the same species.")
    if sire_id is not None:
        sire = session.get(Animal, sire_id)
        if sire is None or sire.species != species or sire.sex != "Male":
            raise ValidationError("Sire must be an existing intact male of the same species.")
    if dam_id is not None and dam_id == sire_id:
        raise ValidationError("Dam and sire cannot be the same animal.")
    if animal_id is not None:
        if animal_id in (dam_id, sire_id):
            raise ValidationError("An animal cannot be its own parent.")
        pmap = build_parent_map(session)
        pmap[animal_id] = (dam_id, sire_id)
        if animal_id in eng.ancestors_with_generation(animal_id, pmap, generations=12):
            raise ValidationError("This pedigree would create a circular lineage.")


def create_animal(
    session: Session, *, tag_id: str, species: str, sex: str, animal_name: Optional[str] = None,
    breed: Optional[str] = None, production_stage: Optional[str] = None, dam_id: Optional[int] = None,
    sire_id: Optional[int] = None, birth_date: Optional[date] = None, status: str = "Active",
    enrolled_date: Optional[date] = None, planned_breeding_date: Optional[date] = None,
    last_heat_date: Optional[date] = None, notes: Optional[str] = None,
    today: Optional[date] = None, sync: bool = True,
) -> Animal:
    today = _today(today)
    tag = (tag_id or "").strip()
    if not tag:
        raise ValidationError("Tag ID is required.")
    if session.scalar(select(Animal.id).where(func.lower(Animal.tag_id) == tag.lower())) is not None:
        raise ValidationError(f"Tag ID '{tag}' already exists - tag IDs must be unique.")
    _validate_basic(species, sex, production_stage or None, status, birth_date, today)
    _validate_parents(session, species, None, dam_id, sire_id)
    if planned_breeding_date and sex != "Female":
        raise ValidationError("Planned breeding date only applies to females.")
    animal = Animal(
        tag_id=tag, animal_name=(animal_name or "").strip() or None, species=species,
        breed=(breed or "").strip() or None, sex=sex, production_stage=production_stage or None,
        dam_id=dam_id, sire_id=sire_id, birth_date=birth_date, enrolled_date=enrolled_date or today,
        status=status, planned_breeding_date=planned_breeding_date, last_heat_date=last_heat_date,
        notes=(notes or "").strip() or None,
    )
    session.add(animal)
    session.flush()
    logger.info("animal_created tag=%s species=%s birth_known=%s", tag, species, birth_date is not None)
    if sync:
        sync_animal_tasks(session, animal, today)
    return animal


_UPDATABLE = {
    "animal_name", "breed", "sex", "production_stage", "dam_id", "sire_id", "birth_date", "status",
    "planned_breeding_date", "last_heat_date", "notes",
}


def update_animal(session: Session, animal_id: int, today: Optional[date] = None, **changes: Any) -> Animal:
    today = _today(today)
    animal = get_animal(session, animal_id)
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise ValidationError(f"Cannot update field(s): {', '.join(sorted(unknown))}")
    new = {f: changes.get(f, getattr(animal, f)) for f in _UPDATABLE}
    _validate_basic(animal.species, new["sex"], new["production_stage"], new["status"], new["birth_date"], today)
    _validate_parents(session, animal.species, animal.id, new["dam_id"], new["sire_id"])
    if new["planned_breeding_date"] and new["sex"] != "Female":
        raise ValidationError("Planned breeding date only applies to females.")
    for field, value in changes.items():
        setattr(animal, field, value if value != "" else None)
    session.flush()
    if animal.status != "Active":
        _cancel_pending(session, animal_id=animal.id)
    else:
        sync_animal_tasks(session, animal, today)
    logger.info("animal_updated tag=%s fields=%s", animal.tag_id, sorted(changes))
    return animal


def list_animals(
    session: Session, *, species: Optional[str] = None, status: Optional[str] = None,
    sex: Optional[str] = None, search: Optional[str] = None,
) -> list[Animal]:
    stmt = select(Animal).order_by(Animal.species, Animal.tag_id)
    if species:
        stmt = stmt.where(Animal.species == species)
    if status:
        stmt = stmt.where(Animal.status == status)
    if sex:
        stmt = stmt.where(Animal.sex == sex)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(Animal.tag_id.ilike(like) | Animal.animal_name.ilike(like) | Animal.breed.ilike(like))
    return list(session.scalars(stmt))


def animals_df(session: Session, today: Optional[date] = None, **filters: Any) -> pd.DataFrame:
    today = _today(today)
    tags = {i: t for i, t in session.execute(select(Animal.id, Animal.tag_id))}
    rows = []
    for a in list_animals(session, **filters):
        rows.append({
            "ID": a.id, "Tag": a.tag_id, "Name": a.animal_name or "", "Species": a.species, "Breed": a.breed or "",
            "Sex": a.sex, "Stage": a.production_stage or "", "Status": a.status,
            "Birth date": a.birth_date, "Age": eng.format_age(eng.age_in_days(a.birth_date, today)),
            "Dam": tags.get(a.dam_id, ""), "Sire": tags.get(a.sire_id, ""),
            "Planned breeding": a.planned_breeding_date, "Last heat": a.last_heat_date,
        })
    cols = ["ID", "Tag", "Name", "Species", "Breed", "Sex", "Stage", "Status", "Birth date", "Age", "Dam", "Sire", "Planned breeding", "Last heat"]
    return pd.DataFrame(rows, columns=cols)


# ======================================================================================
# Breeding & reproduction
# ======================================================================================
def log_service(
    session: Session, animal_id: int, service_date: date, method: str = "Natural",
    sire_id: Optional[int] = None, sire_label: Optional[str] = None, straw_id: Optional[str] = None,
    notes: Optional[str] = None, override_inbreeding: bool = False, today: Optional[date] = None,
) -> BreedingEvent:
    today = _today(today)
    dam = get_animal(session, animal_id)
    if dam.sex != "Female":
        raise ValidationError("Only females can be served.")
    if dam.status != "Active":
        raise ValidationError("Only active animals can be served.")
    if method not in SERVICE_METHODS:
        raise ValidationError("Method must be Natural or AI.")
    if service_date > today:
        raise ValidationError("Service date cannot be in the future.")
    if dam.birth_date and service_date < dam.birth_date:
        raise ValidationError("Service date is before the animal was born.")
    active = session.scalar(select(BreedingEvent).where(
        BreedingEvent.animal_id == animal_id, BreedingEvent.status.in_(ACTIVE_BREEDING)))
    if active is not None:
        raise ValidationError(
            f"{dam.tag_id} already has an active service/pregnancy (served {active.service_date}). "
            "Set its outcome to Open/Aborted/Delivered first (e.g. when she returns to heat).")
    sire_label = (sire_label or "").strip() or None
    straw_id = (straw_id or "").strip() or None
    if sire_id is None and not sire_label and not straw_id:
        raise ValidationError("Record the sire (in-herd male, bull/boar name, or AI straw ID) for traceability.")
    if method == "AI" and not straw_id:
        raise ValidationError("AI service requires a straw ID.")

    if sire_id is not None:
        sire = get_animal(session, sire_id)
        if sire.species != dam.species or sire.sex != "Male":
            raise ValidationError("Sire must be an intact male of the same species.")
        labels = {i: t for i, t in session.execute(select(Animal.id, Animal.tag_id))}
        warnings = eng.assess_inbreeding(dam.id, sire.id, build_parent_map(session), label=lambda i: labels.get(i, str(i)))
        if warnings and not override_inbreeding:
            raise InbreedingRiskError(warnings)
        if warnings:
            logger.warning("inbreeding_override dam=%s sire=%s %s", dam.tag_id, sire.tag_id, warnings)

    event = BreedingEvent(
        animal_id=dam.id, service_date=service_date, method=method, sire_id=sire_id,
        sire_label=sire_label, straw_id=straw_id, status="Served", notes=(notes or "").strip() or None)
    session.add(event)
    dam.planned_breeding_date = None   # the planned service has happened
    session.flush()
    sync_animal_tasks(session, dam, today)
    logger.info("service_logged tag=%s date=%s method=%s", dam.tag_id, service_date, method)
    return event


def set_breeding_outcome(
    session: Session, breeding_id: int, status: str, heat_date: Optional[date] = None,
    notes: Optional[str] = None, today: Optional[date] = None,
) -> BreedingEvent:
    """Confirm pregnancy (PD+), or close a service as Open/Aborted (optionally recording the heat date)."""
    today = _today(today)
    if status not in ("Pregnant", "Open", "Aborted"):
        raise ValidationError("Outcome must be Pregnant, Open or Aborted (use record_delivery for births).")
    event = session.get(BreedingEvent, breeding_id)
    if event is None:
        raise ValidationError("Breeding record not found.")
    if event.status not in ACTIVE_BREEDING:
        raise ValidationError("This breeding record is already closed.")
    animal = get_animal(session, event.animal_id)
    event.status = status
    if notes:
        event.notes = ((event.notes + " | ") if event.notes else "") + notes.strip()
    if status in ("Open", "Aborted") and heat_date:
        animal.last_heat_date = heat_date
    session.flush()
    if status == "Pregnant":
        session.execute(update(HusbandryTask).where(
            HusbandryTask.breeding_id == event.id, HusbandryTask.status == "Pending",
            HusbandryTask.template_key.in_(["repeat_heat", "pd"]),
        ).values(status="Cancelled", notes="Superseded: pregnancy confirmed"))
    _cancel_closed_breeding_tasks(session)
    sync_animal_tasks(session, animal, today)
    logger.info("breeding_outcome tag=%s status=%s", animal.tag_id, status)
    return event


def record_delivery(
    session: Session, breeding_id: int, birth_date: date, litter_size: int,
    offspring: Optional[Sequence[dict]] = None, avg_birth_weight_kg: Optional[float] = None,
    today: Optional[date] = None,
) -> list[Animal]:
    """Close a pregnancy as Delivered, update the dam, log litter size/birth weight, register offspring.

    ``offspring``: list of dicts with ``tag_id``, ``sex`` and optional ``name``.
    """
    today = _today(today)
    event = session.get(BreedingEvent, breeding_id)
    if event is None or event.status not in ACTIVE_BREEDING:
        raise ValidationError("No active pregnancy found for this record.")
    dam = get_animal(session, event.animal_id)
    if birth_date > today:
        raise ValidationError("Birth date cannot be in the future.")
    if birth_date < event.service_date:
        raise ValidationError("Birth date cannot be before the service date.")
    if litter_size < 0:
        raise ValidationError("Litter size cannot be negative.")
    offspring = list(offspring or [])
    if len(offspring) > litter_size:
        raise ValidationError("You are registering more offspring than the litter size.")
    tags = [(o.get("tag_id") or "").strip().lower() for o in offspring]
    if len(set(tags)) != len(tags):
        raise ValidationError("Offspring tag IDs must be unique.")

    event.status, event.actual_birth_date, event.litter_size = "Delivered", birth_date, litter_size
    lactating = LACTATING_STAGE.get(dam.species)
    if lactating:
        dam.production_stage = lactating
    dam.last_heat_date = None
    session.add(ProductionLog(animal_id=dam.id, log_date=birth_date, metric="litter_size", value=float(litter_size), notes="At birth"))
    if avg_birth_weight_kg:
        session.add(ProductionLog(animal_id=dam.id, log_date=birth_date, metric="birth_weight_kg",
                                  value=float(avg_birth_weight_kg), notes="Average of litter"))
    session.flush()

    created: list[Animal] = []
    for o in offspring:
        young = create_animal(
            session, tag_id=o["tag_id"], species=dam.species, sex=o["sex"], animal_name=o.get("name"),
            breed=dam.breed, production_stage=OFFSPRING_STAGE[dam.species], dam_id=dam.id,
            sire_id=event.sire_id, birth_date=birth_date, today=today, sync=True)
        if avg_birth_weight_kg:
            session.add(ProductionLog(animal_id=young.id, log_date=birth_date, metric="birth_weight_kg", value=float(avg_birth_weight_kg)))
        created.append(young)
    _cancel_closed_breeding_tasks(session)
    sync_animal_tasks(session, dam, today)
    logger.info("delivery_recorded dam=%s litter=%s registered=%s", dam.tag_id, litter_size, len(created))
    return created


def _sire_text(session: Session, b: BreedingEvent, tags: dict[int, str]) -> str:
    parts = [tags.get(b.sire_id, "")] if b.sire_id else []
    if b.sire_label:
        parts.append(b.sire_label)
    if b.straw_id:
        parts.append(f"straw {b.straw_id}")
    return " / ".join(p for p in parts if p)


def pregnancy_df(session: Session, today: Optional[date] = None) -> pd.DataFrame:
    """Active services/pregnancies with due dates, days remaining, heat windows, PD and nutrition dates."""
    today = _today(today)
    tags = {i: t for i, t in session.execute(select(Animal.id, Animal.tag_id))}
    rows = []
    stmt = select(BreedingEvent, Animal).join(Animal, Animal.id == BreedingEvent.animal_id).where(
        BreedingEvent.status.in_(ACTIVE_BREEDING), Animal.status == "Active").order_by(BreedingEvent.service_date)
    for b, a in session.execute(stmt):
        p = eng.get_profile(a.species)
        tl = eng.build_timeline(a.species, b.service_date, today)
        served = b.status == "Served"
        rows.append({
            "Breeding ID": b.id, "Tag": a.tag_id, "Name": a.animal_name or "", "Species": a.species, "Status": b.status,
            "Service date": b.service_date, "Method": b.method, "Sire / straw": _sire_text(session, b, tags),
            "Event": p.birth_term.capitalize(), "Expected date": tl.due_date, "Days remaining": tl.days_remaining,
            "Gestation %": tl.progress_pct,
            "Heat-check window": f"{tl.heat_check_start:%d %b} - {tl.heat_check_end:%d %b}" if served else "",
            "Days to heat check": (tl.heat_check_start - today).days if served else None,
            "PD date": tl.pd_date if served else None,
            "Steaming-up from": tl.steaming_start, "Dry-off date": tl.dry_off_date if a.production_stage in ("Lactating", "Dry") else None,
        })
    cols = ["Breeding ID", "Tag", "Name", "Species", "Status", "Service date", "Method", "Sire / straw", "Event", "Expected date",
            "Days remaining", "Gestation %", "Heat-check window", "Days to heat check", "PD date", "Steaming-up from", "Dry-off date"]
    return pd.DataFrame(rows, columns=cols)


def breeding_history_df(session: Session) -> pd.DataFrame:
    tags = {i: t for i, t in session.execute(select(Animal.id, Animal.tag_id))}
    rows = []
    for b in session.scalars(select(BreedingEvent).order_by(BreedingEvent.service_date.desc())):
        rows.append({
            "Breeding ID": b.id, "Female": tags.get(b.animal_id, ""), "Service date": b.service_date, "Method": b.method,
            "Sire / straw": _sire_text(session, b, tags), "Status": b.status,
            "Birth date": b.actual_birth_date, "Litter size": b.litter_size, "Notes": b.notes or "",
        })
    return pd.DataFrame(rows, columns=["Breeding ID", "Female", "Service date", "Method", "Sire / straw", "Status", "Birth date", "Litter size", "Notes"])


def heat_tracking_df(session: Session, today: Optional[date] = None) -> pd.DataFrame:
    """Adult, non-pregnant females: last observed heat -> next expected heat (rolled forward)."""
    today = _today(today)
    busy = set(session.scalars(select(BreedingEvent.animal_id).where(BreedingEvent.status.in_(ACTIVE_BREEDING))))
    rows = []
    for a in list_animals(session, status="Active", sex="Female"):
        if a.id in busy or (a.production_stage in JUVENILE_STAGES):
            continue
        nxt = eng.next_heat_date(a.species, a.last_heat_date, today)
        p = eng.get_profile(a.species)
        rows.append({
            "ID": a.id, "Tag": a.tag_id, "Species": a.species, "Stage": a.production_stage or "",
            "Cycle (d)": p.estrus_cycle_days, "Last heat": a.last_heat_date, "Next expected": nxt,
            "Days to heat": (nxt - today).days if nxt else None, "Planned breeding": a.planned_breeding_date,
        })
    df = pd.DataFrame(rows, columns=["ID", "Tag", "Species", "Stage", "Cycle (d)", "Last heat", "Next expected", "Days to heat", "Planned breeding"])
    return df.sort_values("Days to heat", na_position="last").reset_index(drop=True)


# ======================================================================================
# Husbandry task engine
# ======================================================================================
def _cancel_pending(session: Session, animal_id: int) -> None:
    session.execute(update(HusbandryTask).where(
        HusbandryTask.animal_id == animal_id, HusbandryTask.status == "Pending",
    ).values(status="Cancelled", notes="Animal no longer active"))


def _cancel_closed_breeding_tasks(session: Session) -> None:
    closed = select(BreedingEvent.id).where(BreedingEvent.status.in_(CLOSED_BREEDING))
    session.execute(update(HusbandryTask).where(
        HusbandryTask.status == "Pending", HusbandryTask.breeding_id.in_(closed),
    ).values(status="Cancelled", notes="Breeding closed"))


def sync_animal_tasks(session: Session, animal: Animal, today: date) -> int:
    """Create any missing auto-generated tasks for one animal. Returns number created."""
    if animal.status != "Active":
        return 0
    existing = list(session.scalars(select(HusbandryTask).where(HusbandryTask.animal_id == animal.id)))
    # a changed/cleared planned breeding date invalidates pending flushing tasks
    keep = f"flush:{animal.planned_breeding_date.isoformat()}" if animal.planned_breeding_date else None
    for t in existing:
        if t.template_key == "flushing" and t.status == "Pending" and t.source_key != keep:
            t.status, t.notes = "Cancelled", "Planned breeding date changed"
    breedings = list(session.scalars(select(BreedingEvent).where(BreedingEvent.animal_id == animal.id)))
    specs = eng.generate_task_specs(animal, breedings, existing, today)
    for s in specs:
        session.add(HusbandryTask(
            animal_id=animal.id, breeding_id=s.breeding_id, template_key=s.template_key, source_key=s.source_key,
            task_type=s.task_type, category=s.category, due_date=s.due_date, status="Pending",
            auto_generated=True, notes=s.notes or None))
    if specs:
        session.flush()
    return len(specs)


def sync_tasks(session: Session, today: Optional[date] = None) -> int:
    today = _today(today)
    _cancel_closed_breeding_tasks(session)
    created = sum(sync_animal_tasks(session, a, today) for a in list_animals(session, status="Active"))
    if created:
        logger.info("tasks_synced created=%s", created)
    return created


def effective_task_status(status: str, due: date, today: date) -> str:
    return "Overdue" if status == "Pending" and due < today else status


_STAGE_ON_COMPLETE = {"weaning": WEANER_STAGE, "dry_off": "Dry"}


def complete_task(
    session: Session, task_id: int, completed_date: Optional[date] = None, cost: Optional[float] = None,
    notes: Optional[str] = None, today: Optional[date] = None,
) -> HusbandryTask:
    today = _today(today)
    task = session.get(HusbandryTask, task_id)
    if task is None:
        raise ValidationError("Task not found.")
    if task.status != "Pending":
        raise ValidationError(f"Task is already {task.status}.")
    done = completed_date or today
    if done > today:
        raise ValidationError("Completion date cannot be in the future.")
    task.status, task.completed_date = "Done", done
    if notes:
        task.notes = ((task.notes + " | ") if task.notes else "") + notes.strip()
    animal = get_animal(session, task.animal_id)
    # keep the animal record in step with what was actually done
    if task.template_key == "castration" and animal.sex == "Male":
        animal.sex = "Castrate"
    elif task.template_key in _STAGE_ON_COMPLETE and _STAGE_ON_COMPLETE[task.template_key] in STAGES[animal.species]:
        animal.production_stage = _STAGE_ON_COMPLETE[task.template_key]
    if cost and cost > 0:
        category = "Medical" if task.category == "Health" else "Other"
        session.add(CostRecord(animal_id=animal.id, record_date=done, category=category, description=task.task_type, amount=float(cost)))
    session.flush()
    sync_animal_tasks(session, animal, today)
    logger.info("task_done tag=%s task=%s", animal.tag_id, task.task_type)
    return task


def cancel_task(session: Session, task_id: int, reason: Optional[str] = None) -> HusbandryTask:
    task = session.get(HusbandryTask, task_id)
    if task is None or task.status != "Pending":
        raise ValidationError("Only pending tasks can be cancelled/skipped.")
    task.status, task.notes = "Cancelled", reason or "Skipped by user"
    return task


def tasks_df(
    session: Session, today: Optional[date] = None, *, status: Optional[str] = None,
    species: Optional[str] = None, category: Optional[str] = None, include_cancelled: bool = False,
) -> pd.DataFrame:
    today = _today(today)
    stmt = select(HusbandryTask, Animal).join(Animal, Animal.id == HusbandryTask.animal_id).order_by(HusbandryTask.due_date)
    if species:
        stmt = stmt.where(Animal.species == species)
    if category:
        stmt = stmt.where(HusbandryTask.category == category)
    rows = []
    for t, a in session.execute(stmt):
        eff = effective_task_status(t.status, t.due_date, today)
        if eff == "Cancelled" and not include_cancelled:
            continue
        if status and eff != status:
            continue
        rows.append({
            "Task ID": t.id, "Tag": a.tag_id, "Species": a.species, "Task": t.task_type, "Category": t.category,
            "Due date": t.due_date, "Days to due": (t.due_date - today).days if t.status == "Pending" else None,
            "Status": eff, "Completed": t.completed_date, "Notes": t.notes or "",
        })
    return pd.DataFrame(rows, columns=["Task ID", "Tag", "Species", "Task", "Category", "Due date", "Days to due", "Status", "Completed", "Notes"])


# ======================================================================================
# Dashboard / alert centre
# ======================================================================================
def build_dashboard(session: Session, today: Optional[date] = None) -> dict[str, Any]:
    today = _today(today)
    animals = {a.id: a for a in list_animals(session, status="Active")}
    breedings = [b for b in session.scalars(select(BreedingEvent).where(BreedingEvent.status.in_(ACTIVE_BREEDING))) if b.animal_id in animals]
    out: dict[str, list[dict]] = {k: [] for k in (
        "births_7", "births_14", "overdue_births", "heat", "steaming", "flushing", "pd", "drying", "tasks_overdue", "tasks_today", "tasks_week")}
    icon = eng.urgency_icon

    def base(a: Animal) -> dict:
        return {"Tag": a.tag_id, "Name": a.animal_name or "", "Species": a.species}

    for b in breedings:
        a = animals[b.animal_id]
        p = eng.get_profile(a.species)
        tl = eng.build_timeline(a.species, b.service_date, today)
        d = tl.days_remaining
        row = {"": icon(d), **base(a), "Event": p.birth_term.capitalize(), "Expected date": tl.due_date, "Days remaining": d, "Status": b.status}
        if d < 0:
            out["overdue_births"].append({**row, "": "🔴"})
        elif d <= 7:
            out["births_7"].append(row)
        elif d <= 14:
            out["births_14"].append(row)
        if b.status == "Served":
            start_in = (tl.heat_check_start - today).days
            end_in = (tl.heat_check_end - today).days
            if end_in >= 0 and start_in <= 7:
                out["heat"].append({"": icon(max(start_in, 0)), **base(a), "Type": f"{p.heat_label}: repeat check after service",
                                    "Window start": tl.heat_check_start, "Window end": tl.heat_check_end,
                                    "Days to window": max(start_in, 0), "State": "IN WINDOW" if start_in <= 0 else f"in {start_in} d"})
            pd_in = (tl.pd_date - today).days
            if pd_in <= 7:
                out["pd"].append({"": icon(max(pd_in, 0)), **base(a), "Service date": b.service_date, "PD date": tl.pd_date,
                                  "Days to PD": pd_in, "Method": p.pd_method})
        s_in = (tl.steaming_start - today).days
        if d >= 0 and s_in <= 7:
            out["steaming"].append({"": "🟠" if s_in <= 0 else "🟢", **base(a), "Action": "Steaming-up feed",
                                    "State": "ACTIVE NOW" if s_in <= 0 else f"starts in {s_in} d", "From": tl.steaming_start, "Until": tl.due_date})
        if tl.dry_off_date and a.production_stage == "Lactating":
            dr_in = (tl.dry_off_date - today).days
            if dr_in <= 7 and d >= 0:
                out["drying"].append({"": icon(max(dr_in, 0)), **base(a), "Dry-off date": tl.dry_off_date, "Days": dr_in,
                                      "State": "DUE NOW" if dr_in <= 0 else f"in {dr_in} d"})

    busy = {b.animal_id for b in breedings}
    for a in animals.values():
        if a.sex != "Female" or a.id in busy:
            continue
        p = eng.get_profile(a.species)
        if a.planned_breeding_date:
            start, end = eng.flushing_window(a.species, a.planned_breeding_date)
            s_in = (start - today).days
            if today <= end and s_in <= 7:
                out["flushing"].append({"": "🟠" if s_in <= 0 else "🟢", **base(a), "Action": "Flushing feed",
                                        "State": "ACTIVE NOW" if s_in <= 0 else f"starts in {s_in} d", "From": start, "Until": end})
        if a.production_stage not in JUVENILE_STAGES:
            nxt = eng.next_heat_date(a.species, a.last_heat_date, today)
            if nxt and (nxt - today).days <= 7:
                dd = (nxt - today).days
                out["heat"].append({"": icon(dd), **base(a), "Type": f"{p.heat_label}: expected (open female)",
                                    "Window start": nxt, "Window end": nxt, "Days to window": dd, "State": "TODAY" if dd == 0 else f"in {dd} d"})

    for t, a in session.execute(select(HusbandryTask, Animal).join(Animal, Animal.id == HusbandryTask.animal_id)
                                .where(HusbandryTask.status == "Pending", Animal.status == "Active").order_by(HusbandryTask.due_date)):
        dd = (t.due_date - today).days
        row = {"": "🔴" if dd < 0 else icon(dd), **base(a), "Task": t.task_type, "Category": t.category, "Due date": t.due_date, "Days": dd}
        if dd < 0:
            out["tasks_overdue"].append(row)
        elif dd == 0:
            out["tasks_today"].append(row)
        elif dd <= 7:
            out["tasks_week"].append(row)

    for k in ("heat",):
        out[k].sort(key=lambda r: r["Days to window"])
    summary = {
        "active": len(animals), "pregnant": len(breedings),
        "by_species": pd.Series([a.species for a in animals.values()]).value_counts().to_dict() if animals else {},
    }
    return {"alerts": out, "summary": summary}


# ======================================================================================
# Production
# ======================================================================================
def add_production_log(session: Session, animal_id: int, log_date: date, metric: str, value: float,
                       notes: Optional[str] = None, today: Optional[date] = None) -> ProductionLog:
    today = _today(today)
    get_animal(session, animal_id)
    if metric not in METRICS:
        raise ValidationError("Unknown metric.")
    if value is None or value < 0:
        raise ValidationError("Value must be zero or positive.")
    if log_date > today:
        raise ValidationError("Log date cannot be in the future.")
    row = ProductionLog(animal_id=animal_id, log_date=log_date, metric=metric, value=float(value), notes=(notes or "").strip() or None)
    session.add(row)
    session.flush()
    return row


def production_df(session: Session, metric: Optional[str] = None, animal_id: Optional[int] = None) -> pd.DataFrame:
    stmt = select(ProductionLog, Animal).join(Animal, Animal.id == ProductionLog.animal_id).order_by(ProductionLog.log_date.desc())
    if metric:
        stmt = stmt.where(ProductionLog.metric == metric)
    if animal_id:
        stmt = stmt.where(ProductionLog.animal_id == animal_id)
    rows = [{"Log ID": l.id, "Date": l.log_date, "Tag": a.tag_id, "Species": a.species, "Metric": METRICS[l.metric][0],
             "metric_key": l.metric, "Value": l.value, "Unit": METRICS[l.metric][1], "Notes": l.notes or ""} for l, a in session.execute(stmt)]
    return pd.DataFrame(rows, columns=["Log ID", "Date", "Tag", "Species", "Metric", "metric_key", "Value", "Unit", "Notes"])


def adg_df(session: Session) -> pd.DataFrame:
    by_animal: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for aid, d, v in session.execute(select(ProductionLog.animal_id, ProductionLog.log_date, ProductionLog.value)
                                     .where(ProductionLog.metric == "body_weight_kg")):
        by_animal[aid].append((d, v))
    rows = []
    for aid, pts in by_animal.items():
        adg = eng.compute_adg(pts)
        if adg is None:
            continue
        a = session.get(Animal, aid)
        pts.sort()
        rows.append({"Tag": a.tag_id, "Species": a.species, "First weigh": pts[0][0], "First kg": pts[0][1],
                     "Last weigh": pts[-1][0], "Last kg": pts[-1][1], "Days": (pts[-1][0] - pts[0][0]).days,
                     "ADG (kg/day)": adg, "ADG (g/day)": round(adg * 1000)})
    return pd.DataFrame(rows, columns=["Tag", "Species", "First weigh", "First kg", "Last weigh", "Last kg", "Days", "ADG (kg/day)", "ADG (g/day)"])


def milk_summary_df(session: Session, today: Optional[date] = None) -> pd.DataFrame:
    today = _today(today)
    since = today - timedelta(days=6)
    rows = []
    for a in list_animals(session, status="Active"):
        vals = list(session.scalars(select(ProductionLog.value).where(
            ProductionLog.animal_id == a.id, ProductionLog.metric == "milk_yield_l")))
        if not vals:
            continue
        last7 = list(session.scalars(select(ProductionLog.value).where(
            ProductionLog.animal_id == a.id, ProductionLog.metric == "milk_yield_l", ProductionLog.log_date >= since)))
        rows.append({"Tag": a.tag_id, "Species": a.species, "Records": len(vals), "Total (L)": round(sum(vals), 1),
                     "7-day avg (L/day)": round(sum(last7) / len(last7), 2) if last7 else None})
    return pd.DataFrame(rows, columns=["Tag", "Species", "Records", "Total (L)", "7-day avg (L/day)"])


# ======================================================================================
# Finance
# ======================================================================================
def add_cost(session: Session, *, record_date: date, category: str, amount: float, description: str = "",
             animal_id: Optional[int] = None, species: Optional[str] = None, quantity_kg: Optional[float] = None,
             today: Optional[date] = None) -> CostRecord:
    today = _today(today)
    if category not in COST_CATEGORIES:
        raise ValidationError("Unknown cost category.")
    if amount is None or amount <= 0:
        raise ValidationError("Amount must be greater than zero.")
    if record_date > today:
        raise ValidationError("Cost date cannot be in the future.")
    if animal_id is not None:
        get_animal(session, animal_id)
        species = None
    elif species not in (*SPECIES, "All"):
        raise ValidationError("Choose an animal, a species group, or 'All'.")
    row = CostRecord(animal_id=animal_id, species=species, record_date=record_date, category=category,
                     description=(description or "").strip() or None, quantity_kg=quantity_kg or None, amount=float(amount))
    session.add(row)
    session.flush()
    return row


def costs_df(session: Session) -> pd.DataFrame:
    tags = {i: t for i, t in session.execute(select(Animal.id, Animal.tag_id))}
    rows = [{"ID": c.id, "Date": c.record_date, "Applies to": tags.get(c.animal_id) if c.animal_id else f"Group: {c.species}",
             "Category": c.category, "Description": c.description or "", "Feed (kg)": c.quantity_kg, "Amount": c.amount}
            for c in session.scalars(select(CostRecord).order_by(CostRecord.record_date.desc()))]
    return pd.DataFrame(rows, columns=["ID", "Date", "Applies to", "Category", "Description", "Feed (kg)", "Amount"])


def cost_per_head_df(session: Session) -> pd.DataFrame:
    """Direct costs + equal share of species-group / farm-wide costs among currently active animals."""
    active = list_animals(session, status="Active")
    if not active:
        return pd.DataFrame(columns=["Tag", "Species", "Feed", "Medical", "Labour", "Other", "Total cost"])
    by_species: dict[str, list[Animal]] = defaultdict(list)
    for a in active:
        by_species[a.species].append(a)
    totals: dict[int, dict[str, float]] = {a.id: defaultdict(float) for a in active}
    for c in session.scalars(select(CostRecord)):
        if c.animal_id is not None:
            if c.animal_id in totals:
                totals[c.animal_id][c.category] += c.amount
        elif c.species == "All":
            for a in active:
                totals[a.id][c.category] += c.amount / len(active)
        elif c.species in by_species:
            group = by_species[c.species]
            for a in group:
                totals[a.id][c.category] += c.amount / len(group)
    rows = []
    for a in active:
        t = totals[a.id]
        rows.append({"Tag": a.tag_id, "Species": a.species, **{k: round(t.get(k, 0.0), 2) for k in COST_CATEGORIES},
                     "Total cost": round(sum(t.values()), 2)})
    return pd.DataFrame(rows)


# ======================================================================================
# Export
# ======================================================================================
def export_frames(session: Session, today: Optional[date] = None) -> dict[str, pd.DataFrame]:
    today = _today(today)
    prod = production_df(session).drop(columns=["metric_key"])
    return {
        "Animals": animals_df(session, today),
        "Breeding": breeding_history_df(session),
        "Pregnancies": pregnancy_df(session, today),
        "Tasks": tasks_df(session, today, include_cancelled=True),
        "Production": prod,
        "Costs": costs_df(session),
        "Cost per head": cost_per_head_df(session),
    }


def to_excel_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in frames.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")
