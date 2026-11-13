"""Simple step-by-step logger for the travel agent."""

from __future__ import annotations

import logging
import sys


def setup_logger(name: str = "travel_agent", level: int = logging.INFO) -> logging.Logger:
    """Create a logger that prints clear step-by-step messages."""
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times (Streamlit re-runs)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


# Shared logger instance
logger = setup_logger()


def log_step(step: str, detail: str = "") -> None:
    """Convenience helper for step logging."""
    message = f"[{step}] {detail}".strip() if detail else f"[{step}]"
    logger.info(message)
    print(message, flush=True)