"""Structured, request-correlated telemetry for the travel agent."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from threading import Lock
from time import perf_counter
from uuid import uuid4


def setup_logger(name: str = "travel_agent", level: int = logging.INFO) -> logging.Logger:
    """Create a logger that prints clear step-by-step messages."""
    logger = logging.getLogger(name)

    # Reuse an existing handler if the logger is already configured.
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    return logger


# Shared logger instance
logger = setup_logger()


@dataclass
class RequestMetrics:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    outcome: str = "error"
    attempts: Counter = field(default_factory=Counter)
    failures: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_responses: int = 0
    lock: Lock = field(default_factory=Lock, repr=False)


request_metrics: ContextVar[RequestMetrics | None] = ContextVar("request_metrics", default=None)


def log_event(event: str, **fields) -> None:
    """Emit only operational metadata supplied explicitly by callers."""
    metrics = request_metrics.get()
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event}
    if metrics:
        record["request_id"] = metrics.request_id
    logger.info(json.dumps({**record, **fields}))


def _token_rate(name: str) -> float | None:
    try:
        rate = float(os.getenv(name, ""))
    except ValueError:
        return None
    return rate if isfinite(rate) and rate >= 0 else None


@contextmanager
def track_request():
    metrics = RequestMetrics()
    token = request_metrics.set(metrics)
    start = perf_counter()
    try:
        yield metrics
    finally:
        try:
            input_rate = _token_rate("BEDROCK_INPUT_USD_PER_MILLION")
            output_rate = _token_rate("BEDROCK_OUTPUT_USD_PER_MILLION")
            estimate = None
            if metrics.usage_responses and input_rate is not None and output_rate is not None:
                estimate = (metrics.input_tokens * input_rate + metrics.output_tokens * output_rate) / 1_000_000
            log_event(
                "request_completed",
                outcome=metrics.outcome,
                duration_ms=round((perf_counter() - start) * 1000, 2),
                llm_calls=metrics.attempts["bedrock"],
                llm_usage_responses=metrics.usage_responses,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
                estimated_cost_usd=estimate,
                attempts=dict(metrics.attempts),
                failures=dict(metrics.failures),
                failure_rates={name: metrics.failures[name] / count for name, count in metrics.attempts.items()},
                error_counts=dict(metrics.errors),
            )
        finally:
            request_metrics.reset(token)


@contextmanager
def track_operation(event: str, service: str, **fields):
    start = perf_counter()
    metrics = request_metrics.get()
    if metrics:
        with metrics.lock:
            metrics.attempts[service] += 1
    details = {"outcome": "success"}
    try:
        yield details
    except Exception as error:
        category = type(error).__name__
        details.update(outcome="error", error_type=category)
        if metrics:
            with metrics.lock:
                metrics.failures[service] += 1
                metrics.errors[f"{service}.{details.get('error_code', category)}"] += 1
        raise
    finally:
        log_event(event, service=service, duration_ms=round((perf_counter() - start) * 1000, 2), **fields, **details)


def record_bedrock_usage(response: dict, details: dict) -> None:
    usage = response.get("usage") or {}
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return
    details.update(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens)
    metrics = request_metrics.get()
    if metrics:
        metrics.input_tokens += input_tokens
        metrics.output_tokens += output_tokens
        metrics.usage_responses += 1
