"""Farm Livestock Management System - Streamlit UI.

Run:  streamlit run app.py
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from config import setup_logging
from constants import (
    BREED_HINTS, COST_CATEGORIES, CURRENCY, METRICS, SERVICE_METHODS, SEXES, SPECIES, STAGES, STATUSES,
    TASK_CATEGORIES,
)
from database import DB_PATH, init_db, reset_db, session_scope
from seeds import seed_demo_data
from services import biological_engine as eng
from services import farm_service as svc

setup_logging()
logger = logging.getLogger("farm.app")
st.set_page_config(page_title="Farm Livestock Management System", page_icon="", layout="wide")


@st.cache_resource
def bootstrap() -> bool:
    init_db()
    return True


bootstrap()


# --------------------------------------------------------------------------------------
# UI helpers
# --------------------------------------------------------------------------------------
def flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


def commit_and_rerun(session: Session, message: str) -> None:
    """Persist, leave a message for the next run, and refresh every table on the page."""
    session.commit()
    flash(message)
    st.rerun()


def optional_date(label: str, key: str, default: Optional[date] = None, help_text: Optional[str] = None) -> Optional[date]:
    """Date input that can be left blank (works inside st.form)."""
    c1, c2 = st.columns([3, 2])
    value = c1.date_input(label, value=default or date.today(), key=f"{key}_val", help=help_text)
    blank = c2.checkbox("Unknown / blank", value=default is None, key=f"{key}_blank")
    return None if blank else value


def animal_options(animals, none_label: Optional[str] = None) -> dict:
    opts: dict = {none_label: None} if none_label else {}
    for a in animals:
        opts[f"{svc.animal_label(a)} ({a.species})"] = a.id
    return opts


def df_or_info(df: pd.DataFrame, empty_msg: str, **kwargs) -> None:
    if df.empty:
        st.info(empty_msg)
    else:
        st.dataframe(df, hide_index=True, **kwargs)


# --------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------
def page_dashboard(session: Session, today: date) -> None:
    st.title(" Farmsmart Dashboard & Daily Alert Centre")
    st.caption(f"Working date: **{today:%A, %d %B %Y}**  |  🔴 ≤3 days / overdue   🟠 ≤7 days   🟢 later")
    data = svc.build_dashboard(session, today)
    a, s = data["alerts"], data["summary"]

    c = st.columns(6)
    c[0].metric("Active animals", s["active"])
    c[1].metric("Served / pregnant", s["pregnant"])
    c[2].metric("Births in 7 d", len(a["births_7"]) + len(a["overdue_births"]))
    c[3].metric("Births in 8-14 d", len(a["births_14"]))
    c[4].metric("Overdue tasks", len(a["tasks_overdue"]))
    c[5].metric("Tasks due today", len(a["tasks_today"]))
    if s["by_species"]:
        st.caption("Herd: " + "  •  ".join(f"{k}: {v}" for k, v in s["by_species"].items()))

    def block(title: str, rows: list, empty: str = " Nothing due.") -> None:
        st.subheader(f"{title} ({len(rows)})")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        else:
            st.caption(empty)

    left, right = st.columns(2)
    with left:
        block(" Births due in the next 7 days", a["overdue_births"] + a["births_7"])
        block(" Births due in 8-14 days", a["births_14"])
        block(" Heat / receptivity watch (this week)", a["heat"])
        block(" Pregnancy diagnosis due", a["pd"])
    with right:
        block(" Steaming-up (late gestation) feed", a["steaming"])
        block(" Flushing (pre-breeding) feed", a["flushing"])
        block(" Dry-off due (dairy cattle)", a["drying"])
    st.divider()
    t1, t2, t3 = st.tabs([f" Overdue ({len(a['tasks_overdue'])})", f" Due today ({len(a['tasks_today'])})", f" Next 7 days ({len(a['tasks_week'])})"])
    with t1:
        df_or_info(pd.DataFrame(a["tasks_overdue"]), "No overdue tasks.")
    with t2:
        df_or_info(pd.DataFrame(a["tasks_today"]), "Nothing due today.")
    with t3:
        df_or_info(pd.DataFrame(a["tasks_week"]), "Nothing in the next 7 days.")


def page_animals(session: Session, today: date) -> None:
    st.title("Animal Profiles & Identification")
    tab_reg, tab_add, tab_edit = st.tabs(["Herd register", "Register animal", "Edit / change status"])

    with tab_reg:
        c = st.columns(4)
        sp = c[0].selectbox("Species", ["All", *SPECIES], key="f_species")
        stt = c[1].selectbox("Status", ["All", *STATUSES], index=1, key="f_status")
        sx = c[2].selectbox("Sex", ["All", *SEXES], key="f_sex")
        q = c[3].text_input("Search tag / name / breed", key="f_q")
        df = svc.animals_df(session, today, species=None if sp == "All" else sp, status=None if stt == "All" else stt,
                            sex=None if sx == "All" else sx, search=q or None)
        st.caption(f"{len(df)} animal(s)")
        df_or_info(df.drop(columns=["ID"]), "No animals match the filters.")

    with tab_add:
        species = st.selectbox("Species *", SPECIES, key="reg_species")
        st.caption(f"Breeds seen locally: {BREED_HINTS[species]}")
        same_sp = [a for a in svc.list_animals(session, species=species)]
        dams = animal_options([a for a in same_sp if a.sex == "Female"], "- unknown -")
        sires = animal_options([a for a in same_sp if a.sex == "Male"], "- unknown -")
        with st.form("register_animal"):
            c1, c2 = st.columns(2)
            tag = c1.text_input("Tag ID * (unique)")
            name = c2.text_input("Animal name")
            breed = c1.text_input("Breed")
            sex = c2.selectbox("Sex *", SEXES)
            stage = c1.selectbox("Production stage", ["", *STAGES[species]])
            status = c2.selectbox("Status", STATUSES)
            dam = c1.selectbox("Dam (mother)", list(dams))
            sire = c2.selectbox("Sire (father)", list(sires))
            birth = optional_date("Birth date", "reg_birth", help_text="Leave blank if unknown - age-based tasks are then skipped.")
            notes = st.text_area("Notes")
            if st.form_submit_button("Register animal", type="primary"):
                try:
                    a = svc.create_animal(session, tag_id=tag, animal_name=name, species=species, breed=breed, sex=sex,
                                          production_stage=stage or None, dam_id=dams[dam], sire_id=sires[sire],
                                          birth_date=birth, status=status, notes=notes, today=today)
                    commit_and_rerun(session, f"Registered {a.tag_id}. Husbandry schedule generated.")
                except svc.ValidationError as e:
                    st.error(str(e))

    with tab_edit:
        animals = svc.list_animals(session)
        if not animals:
            st.info("Register an animal first.")
            return
        opts = animal_options(animals)
        pick = st.selectbox("Animal", list(opts), key="edit_pick")
        a = svc.get_animal(session, opts[pick])
        with st.form(f"edit_animal_{a.id}"):
            c1, c2 = st.columns(2)
            status = c1.selectbox("Status", STATUSES, index=STATUSES.index(a.status))
            stages = ["", *STAGES[a.species]]
            stage = c2.selectbox("Production stage", stages, index=stages.index(a.production_stage) if a.production_stage in stages else 0)
            plan = last_heat = None
            if a.sex == "Female":
                plan = optional_date("Planned breeding date (triggers flushing alert)", f"edit_plan_{a.id}", a.planned_breeding_date)
                last_heat = optional_date("Last observed heat", f"edit_heat_{a.id}", a.last_heat_date)
            notes = st.text_area("Notes", value=a.notes or "")
            if st.form_submit_button("Save changes", type="primary"):
                try:
                    changes = dict(status=status, production_stage=stage or None, notes=notes)
                    if a.sex == "Female":
                        changes.update(planned_breeding_date=plan, last_heat_date=last_heat)
                    svc.update_animal(session, a.id, today=today, **changes)
                    commit_and_rerun(session, f"Updated {a.tag_id}.")
                except svc.ValidationError as e:
                    st.error(str(e))


def page_breeding(session: Session, today: date) -> None:
    st.title("🧬 Reproduction & Breeding")
    t_mon, t_srv, t_out, t_birth, t_heat, t_hist = st.tabs(
        ["Pregnancy monitor", "Log service", "PD / outcome", "Record birth", "Heat tracking", "History"])

    with t_mon:
        df = svc.pregnancy_df(session, today)
        df_or_info(df.drop(columns=["Breeding ID"]), "No active services or pregnancies.", column_config={
            "Gestation %": st.column_config.ProgressColumn("Gestation", min_value=0, max_value=100, format="%.0f%%"),
            "Days remaining": st.column_config.NumberColumn("Days remaining", format="%d d"),
            "Days to heat check": st.column_config.NumberColumn("Days to heat check", format="%d d"),
        })
        st.caption("Heat-check window = expected return to oestrus (cycle ± tolerance). A return to heat means the service failed.")

    females = [a for a in svc.list_animals(session, status="Active", sex="Female")]
    with t_srv:
        if not females:
            st.info("No active females registered.")
        else:
            fopts = animal_options(females)
            dam_pick = st.selectbox("Female *", list(fopts), key="srv_dam")
            dam = svc.get_animal(session, fopts[dam_pick])
            males = [a for a in svc.list_animals(session, species=dam.species, status="Active", sex="Male")]
            mopts = animal_options(males, "External sire / AI straw")
            p = eng.get_profile(dam.species)
            with st.form("log_service"):
                c1, c2 = st.columns(2)
                sdate = c1.date_input("Date served *", value=today, max_value=today)
                method = c2.selectbox("Method", SERVICE_METHODS)
                sire_pick = c1.selectbox("In-herd sire", list(mopts))
                sire_label = c2.text_input("Bull / boar name (if external)")
                straw = c1.text_input("AI straw ID (required for AI)")
                notes = c2.text_input("Notes")
                override = st.checkbox("I understand the inbreeding risk - proceed anyway")
                if st.form_submit_button("Log service", type="primary"):
                    try:
                        ev = svc.log_service(session, dam.id, sdate, method, mopts[sire_pick], sire_label, straw, notes,
                                             override_inbreeding=override, today=today)
                        tl = eng.build_timeline(dam.species, ev.service_date, today)
                        commit_and_rerun(session, f"Service logged for {dam.tag_id}. Expected {p.birth_term} on {tl.due_date:%d %b %Y} "
                                                  f"({tl.days_remaining} days). Heat check {tl.heat_check_start:%d %b}-{tl.heat_check_end:%d %b}; "
                                                  f"PD {tl.pd_date:%d %b}; steaming-up from {tl.steaming_start:%d %b}.")
                    except svc.InbreedingRiskError as e:
                        st.error("Inbreeding risk detected:")
                        for w in e.warnings:
                            st.warning(w)
                        st.info("Tick the confirmation box and submit again to override, or choose a different sire.")
                    except svc.ValidationError as e:
                        st.error(str(e))

    active = svc.pregnancy_df(session, today)
    with t_out:
        if active.empty:
            st.info("No active services.")
        else:
            labels = {f"{r['Tag']} - served {r['Service date']:%d %b %Y} ({r['Status']})": int(r["Breeding ID"]) for r in active.to_dict("records")}
            pick = st.selectbox("Service", list(labels), key="out_pick")
            with st.form("outcome"):
                outcome = st.radio("Outcome", ["Pregnant", "Open", "Aborted"], horizontal=True,
                                   help="Pregnant = PD positive.  Open = not pregnant / returned to heat.  Aborted = lost.")
                heat = optional_date("Heat observed on (if returned to heat)", "out_heat")
                notes = st.text_input("Notes")
                if st.form_submit_button("Save outcome", type="primary"):
                    try:
                        svc.set_breeding_outcome(session, labels[pick], outcome, heat_date=heat, notes=notes, today=today)
                        commit_and_rerun(session, f"Outcome saved: {outcome}.")
                    except svc.ValidationError as e:
                        st.error(str(e))

    with t_birth:
        if active.empty:
            st.info("No active pregnancies.")
        else:
            labels = {f"{r['Tag']} - due {r['Expected date']:%d %b %Y}": int(r["Breeding ID"]) for r in active.to_dict("records")}
            pick = st.selectbox("Pregnancy", list(labels), key="birth_pick")
            n = st.number_input("Number of offspring born", min_value=0, max_value=20, value=1, step=1, key="birth_n")
            with st.form("delivery"):
                bdate = st.date_input("Birth date", value=today, max_value=today)
                bw = st.number_input("Average birth weight (kg, optional)", min_value=0.0, value=0.0, step=0.1)
                st.markdown("**Register offspring** (leave tag blank to skip registering that animal)")
                kids = []
                for i in range(int(n)):
                    c1, c2, c3 = st.columns(3)
                    t = c1.text_input(f"Tag #{i + 1}", key=f"kid_tag_{i}")
                    nm = c2.text_input(f"Name #{i + 1}", key=f"kid_name_{i}")
                    sx = c3.selectbox(f"Sex #{i + 1}", ["Female", "Male"], key=f"kid_sex_{i}")
                    if t.strip():
                        kids.append({"tag_id": t, "name": nm, "sex": sx})
                if st.form_submit_button("Record birth", type="primary"):
                    try:
                        created = svc.record_delivery(session, labels[pick], bdate, int(n), kids, bw or None, today=today)
                        commit_and_rerun(session, f"Birth recorded; {len(created)} offspring registered with auto-generated husbandry schedules.")
                    except svc.ValidationError as e:
                        st.error(str(e))

    with t_heat:
        df = svc.heat_tracking_df(session, today)
        df_or_info(df.drop(columns=["ID"]), "No adult open females.", column_config={
            "Days to heat": st.column_config.ProgressColumn("Days to next heat", min_value=0, max_value=25, format="%d d"),
        })
        st.caption("Rabbits are induced ovulators: the 'heat' column is a receptivity-check cycle, not a true oestrous cycle.")
        open_f = [a for a in females if a.id in set(df["ID"])]
        if open_f:
            hopts = animal_options(open_f)
            with st.form("heat_form"):
                who = st.selectbox("Female", list(hopts))
                hd = st.date_input("Heat observed on", value=today, max_value=today)
                if st.form_submit_button("Record heat", type="primary"):
                    svc.update_animal(session, hopts[who], today=today, last_heat_date=hd)
                    commit_and_rerun(session, "Heat recorded - next heat recalculated.")

    with t_hist:
        df_or_info(svc.breeding_history_df(session), "No breeding history yet.")


def page_tasks(session: Session, today: date) -> None:
    st.title(" Husbandry & Preventive Health Schedule")
    c = st.columns(3)
    status = c[0].selectbox("Status", ["Pending + Overdue", "Overdue", "Pending", "Done", "All"], key="t_status")
    species = c[1].selectbox("Species", ["All", *SPECIES], key="t_species")
    cat = c[2].selectbox("Category", ["All", *TASK_CATEGORIES], key="t_cat")
    df = svc.tasks_df(session, today, species=None if species == "All" else species, category=None if cat == "All" else cat)
    if status == "Pending + Overdue":
        df = df[df["Status"].isin(["Pending", "Overdue"])]
    elif status != "All":
        df = df[df["Status"] == status]
    st.caption(f"{len(df)} task(s). Status is derived: Pending → Overdue once the due date has passed.")
    df_or_info(df.drop(columns=["Task ID"]), "No tasks match.")

    pending = svc.tasks_df(session, today)
    pending = pending[pending["Status"].isin(["Pending", "Overdue"])]
    if pending.empty:
        return
    labels = {f"{r['Tag']} | {r['Task']} | due {r['Due date']:%d %b}": int(r["Task ID"]) for r in pending.to_dict("records")}
    with st.form("complete_task"):
        st.subheader("Complete or skip a task")
        pick = st.selectbox("Task", list(labels))
        c1, c2, c3 = st.columns(3)
        done = c1.date_input("Completed on", value=today, max_value=today)
        cost = c2.number_input(f"Cost ({CURRENCY}, optional)", min_value=0.0, value=0.0, step=50.0)
        action = c3.radio("Action", ["Mark done", "Skip / cancel"], horizontal=True)
        notes = st.text_input("Notes")
        if st.form_submit_button("Save", type="primary"):
            try:
                if action == "Mark done":
                    svc.complete_task(session, labels[pick], done, cost or None, notes, today=today)
                    msg = "Task completed. Next occurrence (if recurring) has been scheduled."
                else:
                    svc.cancel_task(session, labels[pick], notes or None)
                    msg = "Task skipped."
                commit_and_rerun(session, msg)
            except svc.ValidationError as e:
                st.error(str(e))


def page_production(session: Session, today: date) -> None:
    st.title(" Production Logs")
    t_log, t_milk, t_adg, t_raw = st.tabs(["Add record", "Milk yield", "Growth / ADG", "All records"])
    animals = svc.list_animals(session, status="Active")
    with t_log:
        if not animals:
            st.info("Register an animal first.")
        else:
            opts = animal_options(animals)
            with st.form("prod_form"):
                c1, c2 = st.columns(2)
                who = c1.selectbox("Animal", list(opts))
                metric = c2.selectbox("Metric", list(METRICS), format_func=lambda k: f"{METRICS[k][0]} ({METRICS[k][1]})")
                d = c1.date_input("Date", value=today, max_value=today)
                v = c2.number_input("Value", min_value=0.0, step=0.1)
                notes = st.text_input("Notes")
                if st.form_submit_button("Save record", type="primary"):
                    try:
                        svc.add_production_log(session, opts[who], d, metric, v, notes, today=today)
                        commit_and_rerun(session, "Record saved.")
                    except svc.ValidationError as e:
                        st.error(str(e))
    with t_milk:
        milk = svc.production_df(session, "milk_yield_l")
        if milk.empty:
            st.info("No milk records yet.")
        else:
            st.dataframe(svc.milk_summary_df(session, today), hide_index=True)
            st.line_chart(milk.pivot_table(index="Date", columns="Tag", values="Value", aggfunc="sum"), y_label="Litres / day")
    with t_adg:
        df_or_info(svc.adg_df(session), "Need at least two body-weight records per animal.")
    with t_raw:
        df_or_info(svc.production_df(session).drop(columns=["metric_key", "Log ID"]), "No production records.")


def page_finance(session: Session, today: date) -> None:
    st.title(f" Feed & Cost Tracker ({CURRENCY})")
    t_add, t_head, t_all = st.tabs(["Log cost", "Cost per head", "All costs"])
    with t_add:
        animals = svc.list_animals(session, status="Active")
        scope = st.radio("Applies to", ["Individual animal", "Species group", "Whole farm"], horizontal=True)
        with st.form("cost_form"):
            animal_id = species = None
            if scope == "Individual animal" and animals:
                opts = animal_options(animals)
                animal_id = opts[st.selectbox("Animal", list(opts))]
            elif scope == "Species group":
                species = st.selectbox("Species", SPECIES)
            else:
                species = "All"
            c1, c2, c3 = st.columns(3)
            cat = c1.selectbox("Category", COST_CATEGORIES)
            amount = c2.number_input(f"Amount ({CURRENCY})", min_value=0.0, step=50.0)
            kg = c3.number_input("Feed quantity (kg, optional)", min_value=0.0, step=1.0)
            d = c1.date_input("Date", value=today, max_value=today)
            desc = c2.text_input("Description")
            if st.form_submit_button("Save cost", type="primary"):
                try:
                    svc.add_cost(session, record_date=d, category=cat, amount=amount, description=desc, animal_id=animal_id,
                                 species=species, quantity_kg=kg or None, today=today)
                    commit_and_rerun(session, "Cost saved.")
                except svc.ValidationError as e:
                    st.error(str(e))
    with t_head:
        df = svc.cost_per_head_df(session)
        if df.empty:
            st.info("No active animals.")
        else:
            c = st.columns(3)
            c[0].metric("Total recorded cost", f"{CURRENCY} {df['Total cost'].sum():,.0f}")
            c[1].metric("Average per head", f"{CURRENCY} {df['Total cost'].mean():,.0f}")
            c[2].metric("Active animals", len(df))
            st.dataframe(df.sort_values("Total cost", ascending=False), hide_index=True)
            st.bar_chart(df.groupby("Species")[["Feed", "Medical", "Labour", "Other"]].sum())
        st.caption("Species-group and whole-farm costs are shared equally among currently active animals in scope.")
    with t_all:
        df_or_info(svc.costs_df(session).drop(columns=["ID"]), "No costs recorded.")


def page_export(session: Session, today: date) -> None:
    st.title("Data Export")
    frames = svc.export_frames(session, today)
    st.download_button("Download everything (Excel workbook)", svc.to_excel_bytes(frames),
                       file_name=f"farm_export_{today:%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    st.divider()
    for name, df in frames.items():
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{name}** - {len(df)} rows")
        c2.download_button("CSV", svc.to_csv_bytes(df), file_name=f"{name.lower().replace(' ', '_')}_{today:%Y%m%d}.csv",
                           mime="text/csv", key=f"csv_{name}")


def page_knowledge(session: Session, today: date) -> None:
    st.title("📚 Biological Knowledge Base")
    st.caption("Default baselines driving every calculation. Confirm vaccination and drug programmes with your veterinarian.")
    rows = [{
        "Species": p.species, "Cycle (d)": p.estrus_cycle_days, "Gestation (d)": p.gestation_days, "Birth event": p.birth_term,
        "Flushing (d before service)": p.flushing_days, "Steaming-up (d before birth)": p.steaming_up_days,
        "Dry-off (d before calving)": p.drying_off_days, "PD (d after service)": p.pd_days, "PD method": p.pd_method, "Notes": p.notes,
    } for p in eng.PROFILES.values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.subheader("Husbandry & health templates")
    sp = st.selectbox("Filter species", ["All", *SPECIES])
    tdf = pd.DataFrame(eng.describe_templates())
    if sp != "All":
        tdf = tdf[tdf["Species"].str.contains(sp)]
    st.dataframe(tdf, hide_index=True)


PAGES = {
    " Dashboard": page_dashboard, "Animals": page_animals, "Breeding": page_breeding,
    " Husbandry tasks": page_tasks, " Production": page_production, " Finance": page_finance,
    "Export": page_export, " Knowledge base": page_knowledge,
}


def main() -> None:
    st.sidebar.title("Farm Manager")
    page = st.sidebar.radio("Navigate", list(PAGES), label_visibility="collapsed")
    today = st.sidebar.date_input("Working date (change to simulate time passing)", value=date.today(), key="today")
    st.sidebar.divider()
    st.sidebar.caption(f"Database: `{DB_PATH.name}`")
    if st.sidebar.button("Load demo data"):
        with session_scope() as s:
            res = seed_demo_data(s, today=date.today())
        flash("Demo data already present." if res.get("skipped") else f"Loaded demo data: {res}", "info" if res.get("skipped") else "success")
        st.rerun()
    if st.sidebar.button(" Reset DB & reload demo"):
        reset_db()
        with session_scope() as s:
            seed_demo_data(s, today=date.today(), reset=True)
        flash("Database reset and demo data reloaded.")
        st.rerun()

    show_flash()
    with session_scope() as session:
        svc.sync_tasks(session, today)       # keeps schedules current as the calendar moves
        PAGES[page](session, today)


main()
