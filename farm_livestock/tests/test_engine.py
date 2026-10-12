from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services import biological_engine as eng

TODAY = date(2025, 6, 1)


@pytest.mark.parametrize("species,days", [("Cattle", 283), ("Swine", 114), ("Sheep", 147), ("Goat", 150), ("Rabbit", 31)])
def test_gestation_lengths(species, days):
    assert eng.expected_birth_date(species, TODAY) == TODAY + timedelta(days=days)


def test_timeline_cattle():
    tl = eng.build_timeline("Cattle", TODAY - timedelta(days=200), TODAY)
    assert tl.due_date == TODAY + timedelta(days=83)
    assert tl.days_remaining == 83
    assert tl.steaming_start == tl.due_date - timedelta(days=56)
    assert tl.dry_off_date == tl.due_date - timedelta(days=60)
    assert (tl.heat_check_start - tl.service_date).days == 18 and (tl.heat_check_end - tl.service_date).days == 24


def test_timeline_pig_and_rabbit():
    pig = eng.build_timeline("Swine", TODAY, TODAY)
    assert pig.steaming_start == pig.due_date - timedelta(days=21) and pig.dry_off_date is None
    rb = eng.build_timeline("Rabbit", TODAY, TODAY)
    assert rb.nest_box_date == TODAY + timedelta(days=27)


def test_next_heat_rolls_forward_and_handles_missing():
    assert eng.next_heat_date("Cattle", None, TODAY) is None
    assert eng.next_heat_date("Cattle", TODAY - timedelta(days=17), TODAY) == TODAY + timedelta(days=4)
    assert eng.next_heat_date("Sheep", TODAY - timedelta(days=40), TODAY) == TODAY - timedelta(days=40) + timedelta(days=51)


def test_unknown_birthdate_is_graceful():
    assert eng.age_in_days(None, TODAY) is None
    assert eng.format_age(None) == "Unknown"


def test_pedigree_flags_parent_sibling_and_cousins():
    # 1 = sire of 3 and 2 ; 3 = daughter of 1 and 4
    pm = {3: (4, 1), 2: (5, 1), 6: (7, 8), 9: (None, 8)}
    assert any("parent" in w for w in eng.assess_inbreeding(3, 1, pm))
    assert any("half siblings" in w for w in eng.assess_inbreeding(3, 2, pm))
    assert eng.assess_inbreeding(3, 6, pm) == []


def test_adg():
    pts = [(date(2025, 1, 1), 10.0), (date(2025, 1, 11), 15.0)]
    assert eng.compute_adg(pts) == 0.5
    assert eng.compute_adg(pts[:1]) is None


def _animal(**kw):
    base = dict(id=1, species="Swine", sex="Male", status="Active", production_stage="Piglet", birth_date=None,
                enrolled_date=TODAY, planned_breeding_date=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_piglet_age_tasks():
    a = _animal(birth_date=TODAY - timedelta(days=1))
    specs = {s.template_key: s for s in eng.generate_task_specs(a, [], [], TODAY)}
    assert {"teeth_clipping", "tail_docking", "iron_injection", "castration", "creep_feed", "weaning"} <= set(specs)
    assert specs["iron_injection"].due_date == a.birth_date + timedelta(days=3)


def test_no_age_tasks_when_birthdate_unknown_or_old():
    assert [s for s in eng.generate_task_specs(_animal(), [], [], TODAY) if s.source_key.startswith("age:")] == []
    old = _animal(birth_date=TODAY - timedelta(days=500))
    assert [s for s in eng.generate_task_specs(old, [], [], TODAY) if s.source_key.startswith("age:")] == []


def test_generation_is_idempotent():
    a = _animal(birth_date=TODAY - timedelta(days=1))
    first = eng.generate_task_specs(a, [], [], TODAY)
    existing = [SimpleNamespace(source_key=s.source_key, template_key=s.template_key, status="Pending",
                                completed_date=None, due_date=s.due_date) for s in first]
    assert eng.generate_task_specs(a, [], existing, TODAY) == []


def test_repro_tasks_for_served_goat():
    a = _animal(species="Goat", sex="Female", production_stage="Doe", birth_date=TODAY - timedelta(days=900))
    b = SimpleNamespace(id=5, status="Served", service_date=TODAY)
    keys = {s.template_key for s in eng.generate_task_specs(a, [b], [], TODAY)}
    assert {"repeat_heat", "pd", "steaming_up", "maternity_move", "pre_birth_booster"} <= keys


def test_flushing_task_from_planned_breeding():
    a = _animal(species="Sheep", sex="Female", production_stage="Ewe", birth_date=TODAY - timedelta(days=900),
                planned_breeding_date=TODAY + timedelta(days=20))
    spec = [s for s in eng.generate_task_specs(a, [], [], TODAY) if s.template_key == "flushing"][0]
    assert spec.due_date == a.planned_breeding_date - timedelta(days=28)
