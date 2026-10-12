"""Engine / session management. The SQLite file is created automatically on first run."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from models import Base

logger = logging.getLogger("farm.db")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("FARM_DB_PATH", BASE_DIR / "farm_management.db"))

engine = create_engine(f"sqlite:///{DB_PATH}", future=True, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables if they do not exist."""
    Base.metadata.create_all(engine)
    logger.info("database_ready path=%s", DB_PATH)


def reset_db() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    logger.warning("database_reset path=%s", DB_PATH)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("session_rollback")
        raise
    finally:
        session.close()
