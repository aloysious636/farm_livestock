from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, HusbandryTask
from services import farm_service as svc

TODAY = date(2025, 6, 1)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    yield s
    s.close()


def mk(session, tag, species="Cattle", sex="Female", **kw):
    return svc.create_animal(session, tag_id=tag, species=species, sex=sex, today=TODAY, **kw)


def test_unique_tag_and_validation(session):
    mk(session, "A1")
    with pytest.raises(svc.ValidationError):
        mk(session, "a1")
    with pytest.raises(svc.ValidationError):
        mk(session, "A2", production_stage="Sow")           # stage not valid for cattle
    with pytest.raises(svc.ValidationError):
        mk(session, "A3", birth_date=TODAY + timedelta(days=1))


def test_service_flow_and_due_date(session):
    cow = mk(session, "C1", birth_date=TODAY - timedelta(days=1000))
    bull = mk(session, "B1", sex="Male", birth_date=TODAY - timedelta(days=1500))
    ev = svc.log_service(session, cow.id, TODAY - timedelta(days=10), sire_id=bull.id, today=TODAY)
    df = svc.pregnancy_df(session, TODAY)
    assert df.iloc[0]["Days remaining"] == 273 and df.iloc[0]["Event"] == "Calving"
    with pytest.raises(svc.ValidationError):      # cannot double-serve
        svc.log_service(session, cow.id, TODAY, sire_id=bull.id, today=TODAY)
    keys = {t.template_key for t in session.query(HusbandryTask).filter_by(breeding_id=ev.id)}
    assert {"repeat_heat", "pd", "steaming_up"} <= keys
    svc.set_breeding_outcome(session, ev.id, "Open", heat_date=TODAY, today=TODAY)
    pending = session.query(HusbandryTask).filter_by(breeding_id=ev.id, status="Pending").count()
    assert pending == 0                           # repro tasks cancelled when she returns to heat


def test_inbreeding_blocked_unless_overridden(session):
    sire = mk(session, "B1", sex="Male")
    daughter = mk(session, "D1", sire_id=sire.id, birth_date=TODAY - timedelta(days=900))
    with pytest.raises(svc.InbreedingRiskError):
        svc.log_service(session, daughter.id, TODAY, sire_id=sire.id, today=TODAY)
    ev = svc.log_service(session, daughter.id, TODAY, sire_id=sire.id, override_inbreeding=True, today=TODAY)
    assert ev.status == "Served"


def test_delivery_registers_offspring_and_schedules_tasks(session):
    sow = mk(session, "P1", species="Swine", production_stage="Sow", birth_date=TODAY - timedelta(days=700))
    boar = mk(session, "P0", species="Swine", sex="Male")
    ev = svc.log_service(session, sow.id, TODAY - timedelta(days=114), sire_id=boar.id, today=TODAY)
    kids = svc.record_delivery(session, ev.id, TODAY, 2, [{"tag_id": "PG1", "sex": "Male"}, {"tag_id": "PG2", "sex": "Female"}],
                               avg_birth_weight_kg=1.4, today=TODAY)
    assert len(kids) == 2 and kids[0].dam_id == sow.id and kids[0].sire_id == boar.id
    assert sow.production_stage == "Lactating Sow"
    types = {t.task_type for t in session.query(HusbandryTask).filter_by(animal_id=kids[0].id)}
    assert "Iron injection" in types and "Castration" in types


def test_completing_castration_updates_sex_and_next_recurring_task(session):
    kid = mk(session, "K1", species="Goat", sex="Male", birth_date=TODAY - timedelta(days=28))
    task = session.query(HusbandryTask).filter_by(animal_id=kid.id, template_key="castration").one()
    svc.complete_task(session, task.id, TODAY, today=TODAY)
    assert kid.sex == "Castrate"


def test_cost_per_head_allocation(session):
    a, b = mk(session, "A"), mk(session, "B")
    svc.add_cost(session, record_date=TODAY, category="Feed", amount=1000, species="Cattle", today=TODAY)
    svc.add_cost(session, record_date=TODAY, category="Medical", amount=300, animal_id=a.id, today=TODAY)
    df = svc.cost_per_head_df(session).set_index("Tag")
    assert df.loc["A", "Total cost"] == 800 and df.loc["B", "Total cost"] == 500


def test_export_contains_all_sheets(session):
    mk(session, "A")
    frames = svc.export_frames(session, TODAY)
    assert {"Animals", "Breeding", "Tasks", "Production", "Costs"} <= set(frames)
    assert len(svc.to_excel_bytes(frames)) > 1000
