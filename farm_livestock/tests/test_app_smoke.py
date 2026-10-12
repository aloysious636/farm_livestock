"""Headless UI smoke test: every page renders against seeded data without raising."""
import os
import tempfile

import pytest

pytest.importorskip("streamlit.testing.v1")


@pytest.fixture()
def app_test(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("FARM_DB_PATH", os.path.join(tmp, "smoke.db"))
    from streamlit.testing.v1 import AppTest
    root = os.path.dirname(os.path.dirname(__file__))
    at = AppTest.from_file(os.path.join(root, "app.py"), default_timeout=60)
    at.run()
    return at


def test_all_pages_render(app_test):
    at = app_test
    assert not at.exception
    # load demo data via sidebar button
    next(b for b in at.sidebar.button if b.label == "Load demo data").click().run()
    assert not at.exception
    pages = list(at.sidebar.radio[0].options)
    assert len(pages) == 8
    for p in pages:
        at.sidebar.radio[0].set_value(p).run()
        assert not at.exception, f"{p}: {at.exception}"
