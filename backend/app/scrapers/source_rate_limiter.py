"""Persistent global sliding-window budget for external pharmacy requests."""

from __future__ import annotations

import asyncio
import contextvars
import email.utils
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_REQUEST_PURPOSE = contextvars.ContextVar("source_request_purpose", default="normal")
_IMAGE_BACKFILL_NORMAL_RESERVE = 20


class ExternalRequestDeferred(RuntimeError):
    """A scheduled collector must finish this cycle instead of waiting."""

    def __init__(self, reason: str, wait_seconds: float):
        self.reason = reason
        self.wait_seconds = wait_seconds
        super().__init__(reason)


def _enabled() -> bool:
    return os.getenv(
        "SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "false"
    ).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _limit() -> int:
    value = int(os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS", "50"))
    if value < 1:
        raise ValueError("SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS must be positive")
    return value


def _window_seconds() -> int:
    value = int(os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS", "900"))
    if value < 1:
        raise ValueError("SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS must be positive")
    return value


def _per_source_limit() -> int:
    total = _limit()
    value = int(
        os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_PER_SOURCE_MAX_REQUESTS", "")
        or max(1, total // 2)
    )
    if value < 1 or value > total:
        raise ValueError(
            "SCRAPER_EXTERNAL_RATE_LIMIT_PER_SOURCE_MAX_REQUESTS must be in "
            "1..MAX_REQUESTS"
        )
    return value


def _image_backfill_source_limit() -> int:
    total = _limit()
    value = int(
        os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_IMAGE_BACKFILL_PER_SOURCE", "10")
    )
    if value < 0 or value > total:
        raise ValueError(
            "SCRAPER_EXTERNAL_RATE_LIMIT_IMAGE_BACKFILL_PER_SOURCE must be in "
            "0..MAX_REQUESTS"
        )
    return value


def _effective_image_backfill_source_limit() -> int:
    return min(
        _image_backfill_source_limit(),
        max(0, _per_source_limit() - _IMAGE_BACKFILL_NORMAL_RESERVE),
    )


@contextmanager
def external_request_purpose(purpose: str):
    """Classify image-recovery calls separately from the normal request budget."""
    if purpose not in {"normal", "image_backfill"}:
        raise ValueError("Unsupported external request purpose")
    token = _REQUEST_PURPOSE.set(purpose)
    try:
        yield
    finally:
        _REQUEST_PURPOSE.reset(token)


def _state_path() -> Path:
    configured = os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR", "").strip()
    if configured:
        root = Path(configured)
    else:
        root = (
            Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
            / "rate-limit"
        )
    root = root.resolve()
    return root / "external-requests.json"


def _lock_path() -> Path:
    return _state_path().with_suffix(".lock")


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(lock_key, threading.Lock())
    with thread_lock:
        with path.open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    stream.seek(0, os.SEEK_END)
                    if stream.tell() == 0:
                        stream.write(b"0")
                        stream.flush()
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "version": 1,
            "events": [],
            "blocked_until": {},
            "blocked_sources_until": {},
            "limit": _limit(),
            "window_seconds": _window_seconds(),
            "per_source_limit": _per_source_limit(),
            "image_backfill_per_source_limit": _image_backfill_source_limit(),
        }
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED")
    if (
        not isinstance(value.get("events"), list)
        or not isinstance(value.get("blocked_until", {}), dict)
        or not isinstance(value.get("blocked_sources_until", {}), dict)
    ):
        raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED")
    return value


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".external-requests-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _try_reserve_request(
    source: str,
    host: str,
    *,
    now: datetime | None = None,
    purpose: str = "normal",
) -> float | None:
    """Persist a reservation or return seconds until the next safe reservation."""
    if not _enabled():
        return None
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    limit = _limit()
    window = _window_seconds()
    per_source_limit = _per_source_limit()
    backfill_source_limit = _image_backfill_source_limit()
    effective_backfill_source_limit = _effective_image_backfill_source_limit()
    if purpose not in {"normal", "image_backfill"}:
        raise ValueError("Unsupported external request purpose")
    path = _state_path()
    with _exclusive_file_lock(_lock_path()):
        state = _read_state(path)
        if (
            state.get("limit", limit) != limit
            or state.get("window_seconds", window) != window
            or state.get("per_source_limit", per_source_limit) != per_source_limit
            or state.get("image_backfill_per_source_limit", backfill_source_limit)
            != backfill_source_limit
        ):
            raise RuntimeError("SOURCE_RATE_LIMIT_CONFIGURATION_CHANGED_FAIL_CLOSED")
        events = state["events"]
        retained = []
        for event in events:
            if not isinstance(event, dict) or not event.get("at"):
                raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED")
            try:
                event_time = _parse_time(event["at"])
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
            # Future entries caused by a clock rollback remain charged until expiry.
            if (now - event_time).total_seconds() < window:
                retained.append(event)

        blocked_until = state.get("blocked_until", {})
        blocked_sources = state.get("blocked_sources_until", {})
        original_blocked_until = dict(blocked_until)
        original_blocked_sources = dict(blocked_sources)
        source_due = blocked_sources.get(source.casefold())
        if source_due:
            try:
                due = _parse_time(source_due)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
            if now < due:
                state["events"] = retained
                if retained != events:
                    _write_state(path, state)
                return max(0.05, (due - now).total_seconds())
            blocked_sources.pop(source.casefold(), None)
        host_due = blocked_until.get(host)
        if host_due:
            try:
                due = _parse_time(host_due)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
            if now < due:
                state["events"] = retained
                state["blocked_until"] = blocked_until
                if retained != events:
                    _write_state(path, state)
                return max(0.05, (due - now).total_seconds())
            blocked_until.pop(host, None)

        if len(retained) >= limit:
            try:
                oldest = min(_parse_time(event["at"]) for event in retained)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
            state["events"] = retained
            state["blocked_until"] = blocked_until
            if (
                retained != events
                or blocked_until != original_blocked_until
                or blocked_sources != original_blocked_sources
            ):
                _write_state(path, state)
            return max(0.05, window - (now - oldest).total_seconds())

        source_events = [
            event for event in retained if event.get("source") == source.casefold()
        ]
        if len(source_events) >= per_source_limit:
            try:
                oldest_source = min(_parse_time(event["at"]) for event in source_events)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
            state["events"] = retained
            state["blocked_until"] = blocked_until
            _write_state(path, state)
            return max(
                0.05,
                window - (now - oldest_source).total_seconds(),
            )

        if purpose == "image_backfill":
            if len(source_events) >= effective_backfill_source_limit:
                if effective_backfill_source_limit == 0:
                    return float(window)
                try:
                    oldest_source = min(
                        _parse_time(event["at"]) for event in source_events
                    )
                except (TypeError, ValueError) as error:
                    raise RuntimeError(
                        "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                    ) from error
                state["events"] = retained
                state["blocked_until"] = blocked_until
                _write_state(path, state)
                return max(0.05, window - (now - oldest_source).total_seconds())
            backfill_events = [
                event
                for event in source_events
                if event.get("purpose", "normal") == "image_backfill"
            ]
            if len(backfill_events) >= backfill_source_limit:
                try:
                    oldest_backfill = min(
                        _parse_time(event["at"]) for event in backfill_events
                    )
                except (TypeError, ValueError) as error:
                    raise RuntimeError(
                        "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                    ) from error
                state["events"] = retained
                state["blocked_until"] = blocked_until
                _write_state(path, state)
                return max(
                    0.05,
                    window - (now - oldest_backfill).total_seconds(),
                )

        retained.append(
            {
                "at": now.isoformat(),
                "source": source.casefold(),
                "host": host.casefold(),
                "purpose": purpose,
            }
        )
        state["events"] = retained
        state["blocked_until"] = blocked_until
        _write_state(path, state)
    return None


async def acquire_external_request(source: str, url: str) -> None:
    """Count attempts; scheduled runs defer to the next cycle when constrained."""
    host = (urlsplit(url).hostname or "").casefold()
    if not host:
        raise ValueError("External request URL must contain a hostname")
    while True:
        purpose = _REQUEST_PURPOSE.get()
        wait_seconds = await asyncio.to_thread(
            _try_reserve_request, source, host, purpose=purpose
        )
        if wait_seconds is None:
            return
        logger.warning(
            "external_source_rate_budget_wait",
            extra={
                "event": "external_source_rate_budget_wait",
                "source": source,
                "host": host,
                "wait_seconds": round(wait_seconds, 2),
            },
        )
        wait_mode = os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_WAIT_MODE", "wait")
        if wait_mode.strip().casefold() == "stop":
            reason = _scheduled_defer_reason(source, host)
            raise ExternalRequestDeferred(reason, wait_seconds)
        await asyncio.sleep(wait_seconds)


def _scheduled_defer_reason(source: str, host: str) -> str:
    """Distinguish local budget exhaustion from persisted source/host cooldowns."""
    now = datetime.now(UTC)
    with _exclusive_file_lock(_lock_path()):
        state = _read_state(_state_path())
    source_due = state.get("blocked_sources_until", {}).get(source.casefold())
    if source_due:
        try:
            if now < _parse_time(source_due):
                return "ACCESS_BLOCKED"
        except (TypeError, ValueError) as error:
            raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED") from error
    host_due = state.get("blocked_until", {}).get(host)
    if host_due:
        try:
            if now < _parse_time(host_due):
                return "RATE_LIMITED"
        except (TypeError, ValueError) as error:
            raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED") from error
    return "HTTP_BUDGET_EXHAUSTED"


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def note_source_response(source: str, url: str, response) -> None:
    """Persist Retry-After and a source cooldown after 403/429 responses."""
    status_code = getattr(response, "status_code", None)
    if not _enabled() or status_code not in {403, 429}:
        return
    headers = getattr(response, "headers", {})
    delay = _retry_after_seconds(headers.get("Retry-After"))
    host = (urlsplit(url).hostname or "").casefold()
    if status_code == 403:
        delay = max(3600.0, delay or 0.0)
    elif delay is None:
        delay = 60.0
    elif delay <= 0:
        return
    if not host and status_code == 429:
        return
    due = datetime.now(UTC).timestamp() + delay
    path = _state_path()
    with _exclusive_file_lock(_lock_path()):
        state = _read_state(path)
        if status_code == 403:
            blocked = state.setdefault("blocked_sources_until", {})
            key = source.casefold()
        else:
            blocked = state.setdefault("blocked_until", {})
            key = host
        previous = blocked.get(key)
        if previous:
            try:
                due = max(due, _parse_time(previous).timestamp())
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    "SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED"
                ) from error
        blocked[key] = datetime.fromtimestamp(due, UTC).isoformat()
        _write_state(path, state)


def rate_budget_status(*, now: datetime | None = None) -> dict:
    """Read current shared-budget usage without changing persisted state."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    path = _state_path()
    with _exclusive_file_lock(_lock_path()):
        state = _read_state(path)
    window = _window_seconds()
    active = []
    for event in state["events"]:
        at = _parse_time(event["at"])
        if (now - at).total_seconds() < window:
            active.append(event)
    return {
        "enabled": _enabled(),
        "limit": state.get("limit", _limit()),
        "per_source_limit": state.get("per_source_limit", _per_source_limit()),
        "image_backfill_per_source_limit": state.get(
            "image_backfill_per_source_limit", _image_backfill_source_limit()
        ),
        "image_backfill_effective_per_source_limit": (
            _effective_image_backfill_source_limit()
        ),
        "window_seconds": state.get("window_seconds", window),
        "total": len(active),
        "by_source": {
            source: sum(1 for event in active if event["source"] == source)
            for source in ("araujo", "pacheco")
        },
        "by_source_purpose": {
            source: {
                purpose: sum(
                    1
                    for event in active
                    if event["source"] == source
                    and event.get("purpose", "normal") == purpose
                )
                for purpose in ("normal", "image_backfill")
            }
            for source in ("araujo", "pacheco")
        },
        "blocked_until": state.get("blocked_until", {}),
        "blocked_sources_until": state.get("blocked_sources_until", {}),
    }


def source_cooldown_active(source: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    with _exclusive_file_lock(_lock_path()):
        state = _read_state(_state_path())
    due = state.get("blocked_sources_until", {}).get(source.casefold())
    if not due:
        return False
    try:
        return now < _parse_time(due)
    except (TypeError, ValueError) as error:
        raise RuntimeError("SOURCE_RATE_LIMIT_STATE_INVALID_FAIL_CLOSED") from error
