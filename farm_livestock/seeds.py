"""Realistic demo data. All dates are relative to "today" so alerts always fire on the dashboard.

Run:  python seeds.py            (seed only if the database is empty)
      python seeds.py --reset    (wipe everything and re-seed)
"""
from __future__ import annotations

import argparse
import logging
import random
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import setup_logging
from database import init_db, reset_db, session_scope
from models import Animal, BreedingEvent, CostRecord, HusbandryTask, ProductionLog
from services import biological_engine as eng
from services import farm_service as svc

logger = logging.getLogger("farm.seed")

# tag, name, species, breed, sex, stage, birth(days ago), dam, sire, status, last_heat(days ago), planned_breeding(days ahead)
ANIMALS = [
    # ---- cattle (sires first)
    ("C010", "Jambo", "Cattle", "Friesian", "Male", "Bull", 3600, None, None, "Active", None, None),
    ("C006", "Simba", "Cattle", "Boran", "Male", "Bull", 1800, None, "C010", "Active", None, None),
    ("C001", "Daisy", "Cattle", "Friesian", "Female", "Dry", 2400, None, "C010", "Active", None, None),
    ("C002", "Malkia", "Cattle", "Ayrshire", "Female", "Dry", 2000, None, None, "Active", None, None),
    ("C003", "Zawadi", "Cattle", "Friesian", "Female", "Heifer", 900, "C001", "C010", "Active", None, None),
    ("C004", "Nyeusi", "Cattle", "Boran", "Female", "Lactating", 2600, None, None, "Active", 17, 10),
    ("C005", "Pendo", "Cattle", "Sahiwal cross", "Female", "Lactating", 2900, None, None, "Active", None, None),
    ("C011", "Pili", "Cattle", "Friesian", "Female", "Lactating", 2200, None, None, "Active", None, None),
    ("C009", "Mama Ng'ombe", "Cattle", "Zebu cross", "Female", "Cow", 4200, None, None, "Sold", None, None),
    # ---- pigs
    ("P003", "Dume", "Swine", "Duroc", "Male", "Boar", 600, None, None, "Active", None, None),
    ("P001", "Nguruwe", "Swine", "Large White", "Female", "Sow", 700, None, None, "Active", None, None),
    ("P002", "Gilt Rose", "Swine", "Landrace", "Female", "Gilt", 260, None, None, "Active", None, 8),
    ("P004", "Mama Lulu", "Swine", "Landrace", "Female", "Lactating Sow", 900, None, None, "Active", None, None),
    ("P005", "Mama Pinky", "Swine", "Large White", "Female", "Lactating Sow", 1000, None, None, "Active", None, None),
    ("P006", "Bella", "Swine", "Landrace", "Female", "Sow", 500, None, None, "Active", None, None),
    # ---- sheep
    ("S004", "Kondoo Dume", "Sheep", "Dorper", "Male", "Ram", 1100, None, None, "Active", None, None),
    ("S001", "Ewe Rahma", "Sheep", "Dorper", "Female", "Ewe", 900, None, None, "Active", None, None),
    ("S002", "Ewe Neema", "Sheep", "Red Maasai", "Female", "Ewe", 800, None, None, "Active", None, None),
    ("S003", "Ewe Amani", "Sheep", "Dorper", "Female", "Ewe", 700, None, None, "Active", None, 14),
    ("S006", "Ewe Zuri", "Sheep", "Dorper", "Female", "Lactating Ewe", 1000, None, None, "Active", None, None),
    ("S007", "Ewe Old", "Sheep", "Red Maasai", "Female", "Ewe", 2000, None, None, "Deceased", None, None),
    # ---- goats
    ("G004", "Kibuyu", "Goat", "Boer", "Male", "Buck", 900, None, None, "Active", None, None),
    ("G001", "Maziwa", "Goat", "Saanen", "Female", "Lactating Doe", 1200, None, None, "Active", None, None),
    ("G002", "Galla Wanjiru", "Goat", "Galla", "Female", "Doe", 700, None, None, "Active", None, None),
    ("G003", "Toggy", "Goat", "Toggenburg", "Female", "Lactating Doe", 1000, None, None, "Active", None, None),
    ("G006", "Mbuzi Njema", "Goat", "Small East African", "Female", "Lactating Doe", 800, None, None, "Active", None, None),
    # ---- rabbits
    ("R003", "Buck Bunny", "Rabbit", "New Zealand White", "Male", "Buck", 250, None, None, "Active", None, None),
    ("R001", "Sungura 1", "Rabbit", "New Zealand White", "Female", "Doe", 300, None, None, "Active", None, None),
    ("R002", "Sungura 2", "Rabbit", "California White", "Female", "Doe", 280, None, None, "Active", None, None),
    ("R004", "Sungura 4", "Rabbit", "Flemish Giant", "Female", "Doe", 260, None, None, "Active", None, 3),
]

# dam tag, service (days ago), status, sire tag, sire label, straw, method, birth (days ago), litter
BREEDINGS = [
    ("C001", 270, "Pregnant", None, "Friesian bull 'Kilimo'", "FR-8841", "AI", None, None),
    ("C002", 278, "Pregnant", "C006", None, None, "Natural", None, None),
    ("C003", 35, "Served", None, "Jersey bull 'Tajiri'", "JE-2210", "AI", None, None),
    ("C011", 226, "Pregnant", None, "Friesian bull 'Kilimo'", "FR-7712", "AI", None, None),
    ("C005", 293, "Delivered", "C006", None, None, "Natural", 10, 1),
    ("C004", 358, "Delivered", "C006", None, None, "Natural", 75, 1),
    ("P001", 105, "Pregnant", "P003", None, None, "Natural", None, None),
    ("P004", 116, "Delivered", "P003", None, None, "Natural", 2, 6),
    ("P005", 144, "Delivered", "P003", None, None, "Natural", 30, 4),
    ("P006", 10, "Served", "P003", None, None, "Natural", None, None),
    ("S001", 117, "Pregnant", "S004", None, None, "Natural", None, None),
    ("S002", 10, "Served", "S004", None, None, "Natural", None, None),
    ("S006", 153, "Delivered", "S004", None, None, "Natural", 6, 2),
    ("G001", 137, "Pregnant", "G004", None, None, "Natural", None, None),
    ("G002", 19, "Served", "G004", None, None, "Natural", None, None),
    ("G003", 130, "Pregnant", "G004", None, None, "Natural", None, None),
    ("G006", 170, "Delivered", "G004", None, None, "Natural", 20, 2),
    ("R001", 12, "Served", "R003", None, None, "Natural", None, None),
    ("R002", 27, "Pregnant", "R003", None, None, "Natural", None, None),
]

# dam tag -> [(tag, name, sex)] ; offspring of the "Delivered" events above
OFFSPRING = {
    "C005": [("C007", "Tumaini", "Female")],
    "C004": [("C008", "Tembo", "Male")],
    "P004": [(f"PG00{i}", None, "Female" if i % 2 else "Male") for i in range(1, 7)],
    "P005": [(f"PG10{i}", None, "Female" if i % 2 else "Male") for i in range(1, 5)],
    "S006": [("S101", None, "Male"), ("S102", None, "Female")],
    "G006": [("K101", None, "Male"), ("K102", None, "Female")],
}


def _ago(today: date, days: Optional[int]) -> Optional[date]:
    return today - timedelta(days=days) if days is not None else None


def seed_demo_data(session: Session, today: Optional[date] = None, reset: bool = False) -> dict:
    today = today or date.today()
    if session.scalar(select(Animal.id).limit(1)) is not None and not reset:
        logger.info("seed_skipped database_not_empty")
        return {"skipped": True}
    rng = random.Random(42)
    by_tag: dict[str, Animal] = {}

    # ---- animals
    for tag, name, species, breed, sex, stage, born, dam, sire, status, heat, plan in ANIMALS:
        by_tag[tag] = svc.create_animal(
            session, tag_id=tag, animal_name=name, species=species, breed=breed, sex=sex, production_stage=stage,
            dam_id=by_tag[dam].id if dam else None, sire_id=by_tag[sire].id if sire else None,
            birth_date=_ago(today, born), status=status, enrolled_date=today - timedelta(days=3),
            last_heat_date=_ago(today, heat), planned_breeding_date=today + timedelta(days=plan) if plan else None,
            today=today, sync=False)
    # one animal with an UNKNOWN birth date to show graceful handling
    by_tag["G007"] = svc.create_animal(session, tag_id="G007", animal_name="Mystery Doe", species="Goat", breed="Galla",
                                       sex="Female", production_stage="Doe", birth_date=None, today=today, sync=False)

    # ---- breeding events (+ offspring for deliveries)
    for dam, svc_ago, status, sire, label, straw, method, born_ago, litter in BREEDINGS:
        event = BreedingEvent(
            animal_id=by_tag[dam].id, service_date=today - timedelta(days=svc_ago), method=method,
            sire_id=by_tag[sire].id if sire else None, sire_label=label, straw_id=straw, status=status,
            actual_birth_date=_ago(today, born_ago), litter_size=litter)
        session.add(event)
        session.flush()
        if status == "Delivered":
            bday = today - timedelta(days=born_ago)
            session.add(ProductionLog(animal_id=by_tag[dam].id, log_date=bday, metric="litter_size", value=float(litter)))
            for tag, name, sex in OFFSPRING[dam]:
                young = svc.create_animal(
                    session, tag_id=tag, animal_name=name, species=by_tag[dam].species, sex=sex, breed=by_tag[dam].breed,
                    production_stage=eng_offspring_stage(by_tag[dam].species), dam_id=by_tag[dam].id,
                    sire_id=by_tag[sire].id if sire else None, birth_date=bday, today=today, sync=False)
                by_tag[tag] = young

    # ---- historical (completed) recurring tasks so the schedule looks lived-in
    for a in list(by_tag.values()):
        if a.status != "Active":
            continue
        age = (today - a.birth_date).days if a.birth_date else 10_000   # unknown birth date => treat as adult
        for tpl in eng.templates_for(a.species):
            if tpl.kind != "recurring" or not tpl.applies_to(a) or age < tpl.min_age_days + 5 or rng.random() > 0.9:
                continue
            done = today - timedelta(days=rng.randint(2, max(3, int(tpl.interval_days * 1.03))))
            session.add(HusbandryTask(
                animal_id=a.id, template_key=tpl.key, source_key=f"rec:{tpl.key}:{done.isoformat()}", task_type=tpl.name,
                category=tpl.category, due_date=done, status="Done", completed_date=done, auto_generated=True))
    session.flush()

    # ---- production logs
    def milk(tag: str, start: float, end: float, days: int = 21) -> None:
        for i in range(days):
            d = today - timedelta(days=days - 1 - i)
            val = start + (end - start) * i / (days - 1) + rng.uniform(-0.8, 0.8) * (start / 12)
            session.add(ProductionLog(animal_id=by_tag[tag].id, log_date=d, metric="milk_yield_l", value=round(max(val, 0.2), 1)))

    milk("C005", 13, 21)      # fresh cow, rising to peak
    milk("C004", 11, 10)
    milk("C011", 19, 17)
    milk("G001", 3.2, 3.0)
    milk("G003", 2.8, 2.4)
    milk("G006", 1.3, 1.2)

    def weights(tag: str, pts: list[tuple[int, float]]) -> None:
        for days_ago, kg in pts:
            session.add(ProductionLog(animal_id=by_tag[tag].id, log_date=today - timedelta(days=days_ago), metric="body_weight_kg", value=kg))

    for i, tag in enumerate(["PG101", "PG102", "PG103", "PG104"]):
        weights(tag, [(30, round(1.4 + i * 0.05, 2)), (16, round(3.4 + i * 0.1, 2)), (2, round(6.9 + i * 0.2, 2))])
    weights("C008", [(75, 35.0), (30, 62.0), (1, 91.0)])
    weights("C007", [(10, 32.0), (1, 38.5)])
    weights("S101", [(6, 3.9), (1, 5.0)])
    weights("K101", [(20, 3.1), (8, 5.8), (1, 7.4)])
    session.add(ProductionLog(animal_id=by_tag["P004"].id, log_date=today - timedelta(days=2), metric="birth_weight_kg", value=1.5, notes="Average of litter"))

    # ---- costs (group feed weekly for 8 weeks, a few medical, labour)
    weekly = {"Cattle": (14500, 420), "Swine": (9800, 280), "Sheep": (3200, 110), "Goat": (3800, 120), "Rabbit": (900, 25)}
    for w in range(8):
        d = today - timedelta(days=7 * w + 1)
        for sp, (amt, kg) in weekly.items():
            f = rng.uniform(0.92, 1.08)
            session.add(CostRecord(species=sp, record_date=d, category="Feed", description=f"Weekly {sp.lower()} feed",
                                   quantity_kg=round(kg * f, 1), amount=round(amt * f, 0)))
    for tag, amt, desc, ago in [("C005", 4500, "Vet: calving assistance", 10), ("P004", 600, "Oxytocin & vitamins", 2),
                                ("C003", 1800, "Pregnancy check & AI", 35), ("G002", 450, "Hoof trimming kit", 18)]:
        session.add(CostRecord(animal_id=by_tag[tag].id, record_date=today - timedelta(days=ago), category="Medical", description=desc, amount=float(amt)))
    for m in range(2):
        session.add(CostRecord(species="All", record_date=today - timedelta(days=30 * m + 3), category="Labour",
                               description="Farm hand wages", amount=12000.0))
    session.flush()

    created = svc.sync_tasks(session, today)
    counts = {"animals": len(by_tag), "tasks_generated": created,
              "breedings": len(BREEDINGS)}
    logger.info("seed_complete %s", counts)
    return counts


def eng_offspring_stage(species: str) -> str:
    from constants import OFFSPRING_STAGE
    return OFFSPRING_STAGE[species]


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Seed the farm database with demo data.")
    parser.add_argument("--reset", action="store_true", help="Drop all tables first.")
    args = parser.parse_args()
    if args.reset:
        reset_db()
    else:
        init_db()
    with session_scope() as session:
        print(seed_demo_data(session, reset=args.reset))


if __name__ == "__main__":
    main()
