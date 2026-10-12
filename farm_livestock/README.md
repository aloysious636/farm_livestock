# Farm Livestock Management System

Multi-species (cattle, swine, sheep, goats, rabbits) livestock manager: Streamlit + SQLAlchemy + SQLite.

## Setup (VS Code)
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
python seeds.py --reset      # optional demo data (or use the sidebar button)
streamlit run app.py
```
Select the `.venv` interpreter in VS Code (Ctrl+Shift+P -> "Python: Select Interpreter"). Tests: `pytest -q`.

## Layout
- `app.py` UI | `models.py` ORM | `database.py` engine/session | `constants.py` vocabularies
- `services/biological_engine.py` species baselines, timelines, task templates, pedigree (pure functions)
- `services/farm_service.py` validation, breeding workflow, task sync, dashboard, finance, export
- `seeds.py` demo data | `tests/` unit + UI smoke tests

Defaults are textbook baselines, not veterinary advice; confirm vaccination programmes with your vet.
