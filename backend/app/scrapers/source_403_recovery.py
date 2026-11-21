"""Persistent, bounded-by-policy recovery cycles for source HTTP 403s."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

_DEFAULT_ATTEMPTS_PER_CYCLE = 3
_DEFAULT_RETRY_SECONDS = 300
_DEFAULT_CYCLE_COOLDOWN_SECONDS = 3600


def _enabled_value(name: str) -> bool:
    return os.getenv(name, "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def enabled(source: str) -> bool:
    return _enabled_value(f"{source.upper()}_403_RECOVERY_ENABLED")


def manually_paused(source: str) -> bool:
    return _enabled_value(f"{source.upper()}_403_MANUAL_PAUSE")


def max_attempts(source: str) -> int:
    value = int(
        os.getenv(
            f"{source.upper()}_403_MAX_ATTEMPTS",
            str(_DEFAULT_ATTEMPTS_PER_CYCLE),
        )
    )
    if value != _DEFAULT_ATTEMPTS_PER_CYCLE:
        raise ValueError(
            f"{source.upper()}_403_MAX_ATTEMPTS must equal 3 for recovery cycles"
        )
    return value


def retry_interval_seconds(source: str) -> int:
    value = int(os.getenv(f"{source.upper()}_403_RETRY_INTERVAL_SECONDS", "300"))
    if value < _DEFAULT_RETRY_SECONDS:
        raise ValueError(
            f"{source.upper()}_403_RETRY_INTERVAL_SECONDS must be at least 300"
        )
    return value


def cycle_cooldown_seconds(source: str) -> int:
    value = int(os.getenv(f"{source.upper()}_403_CYCLE_COOLDOWN_SECONDS", "3600"))
    if value < _DEFAULT_CYCLE_COOLDOWN_SECONDS:
        raise ValueError(
            f"{source.upper()}_403_CYCLE_COOLDOWN_SECONDS must be at least 3600"
        )
    return value


def max_cycles(source: str) -> int:
    """Zero means continue cycles until policy/manual pause stops the scheduler."""
    value = int(os.getenv(f"{source.upper()}_403_MAX_CYCLES", "0"))
    if value < 0:
        raise ValueError(f"{source.upper()}_403_MAX_CYCLES cannot be negative")
    return value


def retry_due(state: dict, now: datetime) -> datetime | None:
    """Return persisted retry time, or None when the circuit is open."""
    if state.get("http_403_circuit_open"):
        return None
    value = state.get("http_403_next_retry_at")
    if not value:
        return None
    try:
        due = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return due


def begin_due_attempt(state: dict, *, now: datetime) -> dict | None:
    """Prepare a scheduled retry; cooldown completion begins a fresh cycle."""
    due = retry_due(state, now)
    if due is None or now < due:
        return None
    result = dict(state)
    if result.get("http_403_cooldown"):
        result["http_403_attempts"] = 0
        result["http_403_cooldown"] = False
        result["http_403_cooldown_until"] = None
        result["http_403_next_retry_at"] = None
        result["http_403_circuit_open"] = False
        result["http_403_cycle_started_at"] = now.isoformat()
    return result


async def wait_for_retry_time(
    source: str,
    due: datetime,
    *,
    now: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
) -> bool:
    """Wait cancellably in short intervals and observe an operator pause."""
    while True:
        if not enabled(source) or manually_paused(source):
            return False
        remaining = (due - now()).total_seconds()
        if remaining <= 0:
            return True
        await sleep(min(remaining, 60.0))


def _last_checkpoint(state: dict) -> dict:
    existing = state.get("http_403_last_valid_checkpoint")
    if isinstance(existing, dict):
        return existing
    keys = (
        "last_completed_page",
        "last_completed_offset",
        "products_queued",
        "products_enqueued",
        "updated_at",
    )
    return {key: state[key] for key in keys if state.get(key) is not None}


def checkpoint_snapshot(state: dict) -> dict:
    return _last_checkpoint(state)


def record_403_failure(
    state: dict,
    *,
    source: str,
    now: datetime,
    retry_after_seconds: int | None = None,
    last_valid_checkpoint: dict | None = None,
) -> dict:
    """Record a 403 and schedule the next try or the next recovery cycle."""
    attempts = int(state.get("http_403_attempts", 0)) + 1
    limit = max_attempts(source)
    cycles = int(state.get("http_403_failed_cycles", 0))
    retry_after = max(0, int(retry_after_seconds or 0))
    interval = max(retry_interval_seconds(source), retry_after)
    cooldown = False
    circuit_open = False
    next_retry = now + timedelta(seconds=interval)
    cooldown_until = None

    if attempts >= limit:
        cycles += 1
        allowed_cycles = max_cycles(source)
        circuit_open = bool(allowed_cycles and cycles >= allowed_cycles)
        cooldown = not circuit_open
        cooldown_seconds = max(cycle_cooldown_seconds(source), retry_after)
        cooldown_until = (
            None if circuit_open else now + timedelta(seconds=cooldown_seconds)
        )
        next_retry = cooldown_until

    checkpoint = last_valid_checkpoint or _last_checkpoint(state)
    return {
        **state,
        "status": "blocked",
        "stop_reason": "http_403",
        "last_http_status": 403,
        "failure_category": "source_access_blocked",
        "http_403_origin": {
            "araujo": "https://www.araujo.com.br",
            "pacheco": "https://www.drogariaspacheco.com.br",
        }[source.casefold()],
        "http_403_detected_at": now.isoformat(),
        "http_403_last_at": now.isoformat(),
        "http_403_attempts": attempts,
        "http_403_max_attempts": limit,
        "http_403_failed_cycles": cycles,
        "http_403_max_cycles": max_cycles(source),
        "http_403_cooldown": cooldown,
        "http_403_cooldown_until": (
            cooldown_until.isoformat() if cooldown_until else None
        ),
        "http_403_next_retry_at": next_retry.isoformat() if next_retry else None,
        "http_403_circuit_open": circuit_open,
        "http_403_alert": cycles >= 2,
        "http_403_last_valid_checkpoint": checkpoint,
        "retry_after_seconds": retry_after or None,
    }


def clear_403_state(state: dict) -> dict:
    result = dict(state)
    for key in (
        "http_403_attempts",
        "http_403_max_attempts",
        "http_403_failed_cycles",
        "http_403_max_cycles",
        "http_403_cooldown",
        "http_403_cooldown_until",
        "http_403_next_retry_at",
        "http_403_circuit_open",
        "http_403_alert",
        "http_403_origin",
        "http_403_detected_at",
        "http_403_last_at",
        "http_403_last_valid_checkpoint",
        "http_403_cycle_started_at",
    ):
        result.pop(key, None)
    return result


def operator_reset_state(state: dict) -> dict:
    """Explicitly reopen a 403 circuit after an operator has reviewed access."""
    if state.get("stop_reason") != "http_403":
        raise ValueError("checkpoint is not stopped by HTTP 403")
    result = clear_403_state(state)
    result.update(
        {
            "status": "retryable",
            "stop_reason": "operator_reset_http_403",
            "started_at": None,
            "finished_at": None,
        }
    )
    result.pop("error", None)
    return result
