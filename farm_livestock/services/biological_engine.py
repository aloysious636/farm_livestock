"""Biological knowledge base + rules engine.

Everything in this module is *pure* (no database access) so it can be unit-tested and reused:

* ``SpeciesProfile``      - evidence-based reproductive baselines per species
* ``build_timeline``      - gestation / heat / PD / steaming-up / drying-off dates from a service date
* ``TaskTemplate``        - age-based and recurring husbandry & health schedule
* ``generate_task_specs`` - idempotent task generator (returns what *should be created*)
* pedigree helpers        - ancestor search and inbreeding warnings
* ``compute_adg``         - average daily gain

Baselines are typical textbook/extension values. They are defaults, not veterinary advice:
vaccination programmes and drug choices must be confirmed with a local veterinarian
(e.g. County Veterinary Office / KALRO guidance).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Callable, Mapping, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from models import Animal, BreedingEvent, HusbandryTask

logger = logging.getLogger("farm.engine")

STALE_GRACE_DAYS = 14        # do not create tasks that are already older than this
RECURRING_LOOKAHEAD_DAYS = 30  # recurring tasks are created when they fall due within this horizon


# --------------------------------------------------------------------------------------
# 1. Species knowledge base
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SpeciesProfile:
    species: str
    estrus_cycle_days: int
    heat_tolerance_days: int            # +/- window around the expected return to heat
    gestation_days: int
    birth_term: str                     # calving, farrowing, lambing, kidding, kindling
    flushing_days: int                  # start flushing this many days before planned service
    steaming_up_days: int               # start steaming-up this many days before expected birth
    drying_off_days: Optional[int]      # dairy cattle only
    pd_days: int                        # pregnancy diagnosis, days after service
    pd_method: str
    maternity_move_days: Optional[int]  # move to maternity pen N days before birth
    pre_birth_booster_days: Optional[int]
    nest_box_day: Optional[int]         # day of gestation to insert nest box (rabbits)
    induced_ovulator: bool
    notes: str

    @property
    def heat_label(self) -> str:
        return "Receptivity check" if self.induced_ovulator else "Heat"


PROFILES: dict[str, SpeciesProfile] = {
    "Cattle": SpeciesProfile(
        "Cattle", 21, 3, 283, "calving", 21, 56, 60, 45,
        "Ultrasound from ~28-35 d; rectal palpation from ~45 d", 7, None, None, False,
        "Steaming-up = last 6-8 weeks; flushing 2-3 weeks pre-breeding (beef/pasture); dry off ~60 d before calving.",
    ),
    "Swine": SpeciesProfile(
        "Swine", 21, 3, 114, "farrowing", 14, 21, None, 28,
        "Ultrasound ~21-28 d; check for return to oestrus at 18-24 d", 7, None, None, False,
        "Gestation 3 months, 3 weeks, 3 days. Flush 10-14 d before service; steam-up last 2-3 weeks.",
    ),
    "Sheep": SpeciesProfile(
        "Sheep", 17, 3, 147, "lambing", 28, 42, None, 45,
        "Transabdominal ultrasound ~45-60 d", 7, 28, None, False,
        "Seasonal breeder. Flush 3-4 weeks pre-mating; steam-up last 4-6 weeks; ~147 d gestation.",
    ),
    "Goat": SpeciesProfile(
        "Goat", 21, 3, 150, "kidding", 28, 42, None, 45,
        "Ultrasound ~30-45 d", 7, 28, None, False,
        "Flush 3-4 weeks pre-mating; steam-up last 4-6 weeks; ~150 d gestation.",
    ),
    "Rabbit": SpeciesProfile(
        "Rabbit", 14, 2, 31, "kindling", 7, 10, None, 12,
        "Abdominal palpation day 10-14", None, None, 27, True,
        "Induced ovulator (no strict oestrous cycle; 14-d receptivity cycle). Nest box day 27-28.",
    ),
}


def get_profile(species: str) -> SpeciesProfile:
    try:
        return PROFILES[species]
    except KeyError as exc:
        raise ValueError(f"Unsupported species: {species!r}") from exc


# --------------------------------------------------------------------------------------
# 2. Date helpers & reproductive timeline
# --------------------------------------------------------------------------------------
def age_in_days(birth_date: Optional[date], today: date) -> Optional[int]:
    """Age in days, or ``None`` when the birth date is unknown (handled gracefully everywhere)."""
    if birth_date is None:
        return None
    return (today - birth_date).days


def format_age(days: Optional[int]) -> str:
    if days is None:
        return "Unknown"
    if days < 0:
        return "Not yet born"
    if days < 60:
        return f"{days} d"
    if days < 730:
        return f"{days // 30} m"
    return f"{days // 365} y {(days % 365) // 30} m"


@dataclass(frozen=True)
class PregnancyTimeline:
    service_date: date
    due_date: date
    days_remaining: int
    gestation_day: int
    progress_pct: float
    heat_check_start: date
    heat_check_end: date
    pd_date: date
    steaming_start: date
    dry_off_date: Optional[date]
    maternity_date: Optional[date]
    booster_date: Optional[date]
    nest_box_date: Optional[date]


def expected_birth_date(species: str, service_date: date) -> date:
    return service_date + timedelta(days=get_profile(species).gestation_days)


def build_timeline(species: str, service_date: date, today: date) -> PregnancyTimeline:
    p = get_profile(species)
    due = service_date + timedelta(days=p.gestation_days)
    gday = (today - service_date).days
    progress = max(0.0, min(100.0, gday / p.gestation_days * 100.0))
    return PregnancyTimeline(
        service_date=service_date,
        due_date=due,
        days_remaining=(due - today).days,
        gestation_day=gday,
        progress_pct=round(progress, 1),
        heat_check_start=service_date + timedelta(days=p.estrus_cycle_days - p.heat_tolerance_days),
        heat_check_end=service_date + timedelta(days=p.estrus_cycle_days + p.heat_tolerance_days),
        pd_date=service_date + timedelta(days=p.pd_days),
        steaming_start=due - timedelta(days=p.steaming_up_days),
        dry_off_date=due - timedelta(days=p.drying_off_days) if p.drying_off_days else None,
        maternity_date=due - timedelta(days=p.maternity_move_days) if p.maternity_move_days else None,
        booster_date=due - timedelta(days=p.pre_birth_booster_days) if p.pre_birth_booster_days else None,
        nest_box_date=service_date + timedelta(days=p.nest_box_day) if p.nest_box_day else None,
    )


def flushing_window(species: str, planned_service: date) -> tuple[date, date]:
    return planned_service - timedelta(days=get_profile(species).flushing_days), planned_service


def next_heat_date(species: str, last_heat: Optional[date], today: date) -> Optional[date]:
    """Next expected heat on or after ``today`` by rolling the cycle forward from the last observed heat."""
    if last_heat is None:
        return None
    cycle = get_profile(species).estrus_cycle_days
    if last_heat >= today:
        return last_heat + timedelta(days=cycle)
    n = max(1, math.ceil((today - last_heat).days / cycle))
    return last_heat + timedelta(days=n * cycle)


def urgency_icon(days: Optional[int]) -> str:
    """Traffic-light indicator used by the dashboard."""
    if days is None:
        return "⚪"
    if days <= 3:
        return "🔴"
    if days <= 7:
        return "🟠"
    return "🟢"


# --------------------------------------------------------------------------------------
# 3. Husbandry & preventive-health templates
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskTemplate:
    key: str
    name: str
    category: str
    species: tuple[str, ...]
    kind: str                       # "age" (fixed day after birth) | "recurring" (interval)
    offset_days: int = 0
    interval_days: int = 0
    min_age_days: int = 0
    sexes: Optional[tuple[str, ...]] = None
    notes: str = ""

    def applies_to(self, animal: "Animal") -> bool:
        if animal.species not in self.species:
            return False
        if self.sexes and animal.sex not in self.sexes:
            return False
        return True


def _age(key, name, cat, species, offset, sexes=None, notes=""):
    return TaskTemplate(key, name, cat, tuple(species), "age", offset_days=offset, sexes=sexes, notes=notes)


def _rec(key, name, cat, species, interval, min_age=0, notes=""):
    return TaskTemplate(key, name, cat, tuple(species), "recurring", interval_days=interval, min_age_days=min_age, notes=notes)


TEMPLATES: tuple[TaskTemplate, ...] = (
    # ---- age-based: cattle
    _age("navel_care", "Navel dip & colostrum check", "Husbandry", ["Cattle"], 1, notes="Navel iodine dip; confirm calf suckled colostrum within 6 h."),
    _age("castration", "Castration (if not kept for breeding)", "Husbandry", ["Cattle"], 60, ("Male",), "Use a vet-approved method."),
    _age("ecf_itm", "ECF immunisation (infection-and-treatment) - via veterinarian", "Health", ["Cattle"], 90, notes="Only through a veterinarian."),
    _age("weaning", "Weaning", "Husbandry", ["Cattle"], 90, notes="Default 90 d; beef calves are often weaned later (6-8 months). Adjust to your system."),
    # ---- age-based: swine
    _age("teeth_clipping", "Teeth clipping", "Husbandry", ["Swine"], 1, notes="Day 1-2, only where needed."),
    _age("tail_docking", "Tail docking", "Husbandry", ["Swine"], 2, notes="Day 1-3."),
    _age("iron_injection", "Iron injection", "Health", ["Swine"], 3, notes="Prevents piglet anaemia (day 3)."),
    _age("creep_feed", "Introduce creep feed", "Nutrition", ["Swine"], 7),
    _age("castration", "Castration", "Husbandry", ["Swine"], 10, ("Male",), "Day 7-14."),
    _age("weaning", "Weaning", "Husbandry", ["Swine"], 28, notes="Day 28-35."),
    _age("deworm_weaning", "Deworming at weaning", "Health", ["Swine"], 28),
    # ---- age-based: sheep & goats
    _age("tail_docking", "Tail docking (lambs)", "Husbandry", ["Sheep"], 7, notes="First 1-2 weeks, rubber ring."),
    _age("disbudding", "Disbudding (kids)", "Husbandry", ["Goat"], 7, notes="Day 3-14 for kids to be hornless."),
    _age("castration", "Castration", "Husbandry", ["Sheep", "Goat"], 28, ("Male",), "Elastrator/rubber ring or vet-approved method."),
    _age("weaning", "Weaning", "Husbandry", ["Sheep", "Goat"], 90),
    # ---- age-based: rabbits
    _age("weaning", "Weaning kits", "Husbandry", ["Rabbit"], 35, notes="Day 28-42."),
    # ---- recurring: cattle
    _rec("deworming", "Deworming", "Health", ["Cattle"], 90, 60, "Tactical: use FAMACHA/faecal egg counts where possible."),
    _rec("dipping", "Tick control: dipping / spraying", "Health", ["Cattle"], 14, 30, "Interval depends on tick pressure and acaricide."),
    _rec("hoof_trimming", "Hoof inspection & trimming", "Husbandry", ["Cattle"], 180, 365),
    _rec("fmd_vaccination", "FMD vaccination", "Health", ["Cattle", "Swine", "Sheep", "Goat"], 180, 120, "Follow Directorate of Veterinary Services schedule for your zone."),
    _rec("anthrax_vaccination", "Anthrax vaccination", "Health", ["Cattle", "Sheep", "Goat"], 365, 120),
    _rec("lsd_vaccination", "Lumpy skin disease vaccination", "Health", ["Cattle"], 365, 120),
    # ---- recurring: sheep & goats
    _rec("dipping", "Dipping / spraying (ticks, mange, lice)", "Health", ["Sheep", "Goat"], 14, 30),
    _rec("deworming", "Deworming (strategic / FAMACHA-guided)", "Health", ["Sheep", "Goat"], 90, 60),
    _rec("hoof_trimming", "Hoof trimming", "Husbandry", ["Sheep", "Goat"], 90, 120),
    _rec("ppr_vaccination", "PPR vaccination", "Health", ["Sheep", "Goat"], 365, 90),
    _rec("ccpp_vaccination", "CCPP vaccination", "Health", ["Goat"], 180, 90),
    # ---- recurring: swine
    _rec("deworming", "Deworming (herd programme)", "Health", ["Swine"], 120, 56),
    _rec("mange_lice", "Mange / lice control (spray)", "Health", ["Swine"], 90, 56),
    # ---- recurring: rabbits
    _rec("health_check", "Health check (ears, hocks, teeth, body condition)", "Health", ["Rabbit"], 30),
    _rec("hutch_disinfection", "Hutch cleaning & disinfection", "Husbandry", ["Rabbit"], 14),
)


def templates_for(species: str) -> list[TaskTemplate]:
    return [t for t in TEMPLATES if species in t.species]


@dataclass(frozen=True)
class TaskSpec:
    template_key: str
    source_key: str
    task_type: str
    category: str
    due_date: date
    notes: str = ""
    breeding_id: Optional[int] = None


def _repro_specs(animal: "Animal", breeding: "BreedingEvent", today: date) -> list[TaskSpec]:
    p = get_profile(animal.species)
    tl = build_timeline(animal.species, breeding.service_date, today)
    bid = breeding.id
    out: list[TaskSpec] = []

    def add(key: str, name: str, cat: str, due: Optional[date], notes: str = "") -> None:
        if due is None:
            return
        out.append(TaskSpec(key, f"repro:{key}:{bid}", name, cat, due, notes, bid))

    if breeding.status == "Served":
        add("repeat_heat", f"{p.heat_label}: repeat check (return to service)", "Reproduction", tl.heat_check_start,
            f"Watch {tl.heat_check_start:%d %b} - {tl.heat_check_end:%d %b}. Return to heat means NOT pregnant.")
        add("pd", "Pregnancy diagnosis (PD)", "Reproduction", tl.pd_date, p.pd_method)
    if breeding.status in ("Served", "Pregnant"):
        add("steaming_up", "Start steaming-up (late-gestation feeding)", "Nutrition", tl.steaming_start,
            f"Increase energy/protein; expected {p.birth_term} {tl.due_date:%d %b %Y}.")
        if animal.production_stage in ("Lactating", "Dry"):
            add("dry_off", "Dry off (stop milking)", "Husbandry", tl.dry_off_date, "~60 d before calving; apply dry-cow therapy per vet advice.")
        if tl.maternity_date:
            add("maternity_move", f"Move to {p.birth_term} pen", "Husbandry", tl.maternity_date, "Clean, disinfected, bedded pen.")
        if tl.booster_date:
            add("pre_birth_booster", "Pre-birth clostridial/CDT booster & deworm (vet advice)", "Health", tl.booster_date)
        if tl.nest_box_date:
            add("nest_box", "Insert nest box", "Husbandry", tl.nest_box_date, "Day 27-28 of gestation.")
    return out


def generate_task_specs(
    animal: "Animal",
    breedings: Sequence["BreedingEvent"],
    existing: Sequence["HusbandryTask"],
    today: date,
) -> list[TaskSpec]:
    """Return the tasks that *should exist* for an animal but do not yet. Idempotent."""
    if animal.status != "Active":
        return []
    p = get_profile(animal.species)
    existing_keys = {t.source_key for t in existing}
    pending_templates = {t.template_key for t in existing if t.status == "Pending"}
    anchors: dict[str, date] = {}
    for t in existing:
        if t.source_key.startswith("rec:") and t.status in ("Done", "Cancelled"):
            d = t.completed_date or t.due_date
            if t.template_key not in anchors or d > anchors[t.template_key]:
                anchors[t.template_key] = d

    specs: list[TaskSpec] = []
    age = age_in_days(animal.birth_date, today)
    enrolled = animal.enrolled_date or today

    for tpl in templates_for(animal.species):
        if not tpl.applies_to(animal):
            continue
        if tpl.kind == "age":
            if animal.birth_date is None:
                continue  # unknown birth date: age-based events cannot be scheduled
            due = animal.birth_date + timedelta(days=tpl.offset_days)
            key = f"age:{tpl.key}"
            if key in existing_keys or (today - due).days > STALE_GRACE_DAYS:
                continue
            specs.append(TaskSpec(tpl.key, key, tpl.name, tpl.category, due, tpl.notes))
        else:  # recurring
            if tpl.key in pending_templates:
                continue
            anchor = anchors.get(tpl.key)
            due = anchor + timedelta(days=tpl.interval_days) if anchor else enrolled
            if animal.birth_date is not None:
                due = max(due, animal.birth_date + timedelta(days=tpl.min_age_days))
            if (due - today).days > RECURRING_LOOKAHEAD_DAYS:
                continue
            key = f"rec:{tpl.key}:{due.isoformat()}"
            if key in existing_keys:
                continue
            specs.append(TaskSpec(tpl.key, key, tpl.name, tpl.category, due, tpl.notes))

    for b in breedings:
        if b.status in ("Served", "Pregnant"):
            for s in _repro_specs(animal, b, today):
                if s.source_key in existing_keys or (today - s.due_date).days > STALE_GRACE_DAYS:
                    continue
                specs.append(s)

    if animal.sex == "Female" and animal.planned_breeding_date:
        has_active = any(b.status in ("Served", "Pregnant") for b in breedings)
        start, _ = flushing_window(animal.species, animal.planned_breeding_date)
        key = f"flush:{animal.planned_breeding_date.isoformat()}"
        if not has_active and key not in existing_keys and (today - start).days <= STALE_GRACE_DAYS:
            specs.append(TaskSpec(
                "flushing", key, "Start flushing (higher plane of nutrition)", "Nutrition", start,
                f"{p.flushing_days} d before planned service on {animal.planned_breeding_date:%d %b %Y}.",
            ))
    return specs


def describe_templates() -> list[dict]:
    rows = []
    for t in TEMPLATES:
        rows.append({
            "Species": ", ".join(t.species), "Task": t.name, "Category": t.category,
            "Type": "Age-based" if t.kind == "age" else "Recurring",
            "Schedule": f"day {t.offset_days} after birth" if t.kind == "age" else f"every {t.interval_days} d (from {t.min_age_days} d old)",
            "Applies to": ", ".join(t.sexes) if t.sexes else "All", "Notes": t.notes,
        })
    return rows


# --------------------------------------------------------------------------------------
# 4. Pedigree / inbreeding
# --------------------------------------------------------------------------------------
ParentMap = Mapping[int, tuple[Optional[int], Optional[int]]]


def ancestors_with_generation(animal_id: int, parent_map: ParentMap, generations: int = 3) -> dict[int, int]:
    """Map ancestor_id -> closest generation (1 = parent, 2 = grandparent...)."""
    found: dict[int, int] = {}
    frontier = [animal_id]
    for gen in range(1, generations + 1):
        nxt: list[int] = []
        for aid in frontier:
            for parent in parent_map.get(aid, (None, None)):
                if parent is not None:
                    found.setdefault(parent, gen)
                    nxt.append(parent)
        frontier = nxt
    return found


_REL = {1: "parent", 2: "grandparent", 3: "great-grandparent"}


def assess_inbreeding(
    female_id: int,
    male_id: int,
    parent_map: ParentMap,
    generations: int = 3,
    label: Optional[Callable[[int], str]] = None,
) -> list[str]:
    """Return human-readable warnings; an empty list means no relationship was found."""
    name = label or str
    if female_id == male_id:
        return ["Female and male are the same animal."]
    fa = ancestors_with_generation(female_id, parent_map, generations)
    ma = ancestors_with_generation(male_id, parent_map, generations)
    warnings: list[str] = []
    if male_id in fa:
        warnings.append(f"{name(male_id)} is the {_REL.get(fa[male_id], 'ancestor')} of {name(female_id)}.")
    if female_id in ma:
        warnings.append(f"{name(female_id)} is the {_REL.get(ma[female_id], 'ancestor')} of {name(male_id)}.")
    f_dam, f_sire = parent_map.get(female_id, (None, None))
    m_dam, m_sire = parent_map.get(male_id, (None, None))
    same_dam = f_dam is not None and f_dam == m_dam
    same_sire = f_sire is not None and f_sire == m_sire
    if same_dam and same_sire:
        warnings.append(f"{name(female_id)} and {name(male_id)} are full siblings.")
    elif same_dam or same_sire:
        warnings.append(f"{name(female_id)} and {name(male_id)} are half siblings.")
    elif not warnings:
        common = (set(fa) & set(ma)) - {female_id, male_id}
        if common:
            shown = ", ".join(sorted(name(c) for c in common))
            warnings.append(f"Share common ancestor(s) within {generations} generations: {shown}.")
    return warnings


# --------------------------------------------------------------------------------------
# 5. Production maths
# --------------------------------------------------------------------------------------
def compute_adg(weights: Sequence[tuple[date, float]]) -> Optional[float]:
    """Average daily gain (kg/day) between the first and last weighing; ``None`` if not computable."""
    pts = sorted(weights)
    if len(pts) < 2:
        return None
    days = (pts[-1][0] - pts[0][0]).days
    if days <= 0:
        return None
    return round((pts[-1][1] - pts[0][1]) / days, 3)
