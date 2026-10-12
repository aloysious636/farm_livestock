"""Central logging configuration (idempotent, safe under Streamlit reruns)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console + rotating file logging exactly once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "farm.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    _CONFIGURED = True
    logging.getLogger("farm.config").info("logging_configured log_dir=%s", LOG_DIR)
