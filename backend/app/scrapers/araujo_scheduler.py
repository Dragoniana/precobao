"""Scheduled, resumable imports from an explicitly authorized Araújo feed."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scrapers.araujo import AraujoScraper
from app.scrapers.partner_feed import PartnerFeedForbidden, PartnerFeedRateLimited
from app.scrapers.source_403_recovery import (
    begin_due_attempt,
    clear_403_state,
    record_403_failure,
    retry_due,
    wait_for_retry_time,
)
from app.scrapers.source_403_recovery import (
    enabled as recovery_enabled,
)
from app.scrapers.source_403_recovery import (
    manually_paused as recovery_manually_paused,
)
from app.scrapers.source_modes import (
    SourceHTTPError,
    SourceMode,
    direct_access_ready,
    source_mode,
)


def _state_dir() -> Path:
    return Path(os.getenv("ARAUJO_STATE_DIR", "data/araujo"))


def _state_path() -> Path:
    return _state_dir() / "checkpoint.json"


def _checkpoint() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_checkpoint(state: dict) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".araujo-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _state_path())
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _next_run(now: datetime, state: dict) -> datetime:
    completed = state.get("finished_at")
    if not completed:
        return now
    try:
        finished_at = datetime.fromisoformat(completed)
    except (TypeError, ValueError):
        return now
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    if state.get("status") == "complete":
        interval = float(os.getenv("ARAUJO_INTERVAL_HOURS", "24"))
        return finished_at + timedelta(hours=interval)
    retry = float(os.getenv("ARAUJO_FAILURE_RETRY_MINUTES", "60"))
    retry_after = max(0, int(state.get("retry_after_seconds") or 0))
    return finished_at + max(timedelta(minutes=retry), timedelta(seconds=retry_after))


def _feed_configured() -> bool:
    return bool(
        os.getenv("ARAUJO_PARTNER_FEED_FILE", "").strip()
        or os.getenv("ARAUJO_PARTNER_FEED_URL", "").strip()
    )


def _persistence_failed(persistence: dict[str, int]) -> bool:
    return any(
        persistence.get(key, 0) for key in ("persistence_errors", "image_errors")
    )


def _source_checkpoint(scraper: AraujoScraper) -> dict:
    path = getattr(scraper, "source_checkpoint_path", None)
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not value.get("last_completed_page"):
        return {}
    keys = ("last_completed_page", "products_queued", "updated_at")
    return {key: value[key] for key in keys if value.get(key) is not None}


def _log(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "service": "araujo-scheduler",
                "event": event,
                "time": datetime.now(UTC).isoformat(),
                **fields,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


async def serve() -> None:
    enabled = os.getenv("ARAUJO_SCHEDULER_ENABLED", "false").casefold()
    if enabled not in {"1", "true", "yes", "on"}:
        _log("disabled", reason="ARAUJO_SCHEDULER_ENABLED=false")
        while True:
            await asyncio.sleep(3600)

    interval = float(os.getenv("ARAUJO_INTERVAL_HOURS", "24"))
    retry_minutes = float(os.getenv("ARAUJO_FAILURE_RETRY_MINUTES", "60"))
    if interval <= 0 or retry_minutes <= 0:
        raise ValueError("Araújo scheduler intervals must be positive")

    while True:
        mode = source_mode("araujo")
        if mode is SourceMode.DISABLED:
            _log("paused", reason="source_disabled", source_mode=mode.value)
            await asyncio.sleep(3600)
            continue
        if mode is SourceMode.FEED and not _feed_configured():
            _log("paused", reason="authorized_feed_not_configured")
            await asyncio.sleep(3600)
            continue
        if mode is SourceMode.DIRECT and not direct_access_ready("araujo"):
            _log("paused", reason="direct_access_not_confirmed")
            await asyncio.sleep(3600)
            continue

        state = _checkpoint()
        recovering_403 = False
        if state.get("stop_reason") == "http_403":
            if not recovery_enabled("araujo") or recovery_manually_paused("araujo"):
                _log(
                    "paused",
                    reason="source_http_403_recovery_disabled_or_manual_pause",
                    source_mode=mode.value,
                    cycles=state.get("http_403_failed_cycles", 0),
                )
                await asyncio.sleep(60)
                continue
            if "http_403_attempts" not in state:
                state = record_403_failure(
                    state, source="araujo", now=datetime.now(UTC)
                )
                _write_checkpoint(state)
            due = retry_due(state, datetime.now(UTC))
            if due is None:
                _log(
                    "circuit_open",
                    reason="source_http_403_cycle_limit_reached",
                    source_mode=mode.value,
                    cycles=state.get("http_403_failed_cycles", 0),
                )
                await asyncio.sleep(60)
                continue
            delay = max(0.0, (due - datetime.now(UTC)).total_seconds())
            if delay:
                _log(
                    "http_403_retry_wait",
                    next_attempt=due.isoformat(),
                    delay_seconds=delay,
                )
                if not await wait_for_retry_time(
                    "araujo",
                    due,
                    now=lambda: datetime.now(UTC),
                    sleep=asyncio.sleep,
                ):
                    _log("paused", reason="manual_403_recovery_pause")
                    continue
            due_state = begin_due_attempt(state, now=datetime.now(UTC))
            if due_state is None:
                continue
            if due_state != state:
                state = due_state
                _write_checkpoint(state)
            recovering_403 = True
        now = datetime.now(UTC)
        due = now if recovering_403 else _next_run(now, state)
        delay = max(0.0, (due - now).total_seconds())
        if delay:
            _log("waiting", next_run=due.isoformat())
            await asyncio.sleep(delay)

        started = datetime.now(UTC)
        state = {
            **state,
            "status": "running",
            "stop_reason": None,
            "started_at": started.isoformat(),
        }
        _write_checkpoint(state)
        _log("run_started", started_at=started.isoformat())
        scraper = AraujoScraper(source_mode=mode)
        try:
            await scraper.run()
            persistence = scraper.persistence_stats.copy()
            persistence_failed = _persistence_failed(persistence)
            state = {
                **state,
                "status": "error" if persistence_failed else "complete",
                "stop_reason": (
                    "persistence_errors"
                    if persistence_failed
                    else "authorized_feed_imported"
                ),
                "products_queued": scraper.products_queued,
                "persistence": persistence,
                "failure_category": (
                    "database_persistence_error" if persistence_failed else None
                ),
            }
        except Exception as error:
            blocked = isinstance(error, PartnerFeedForbidden)
            rate_limited = isinstance(error, PartnerFeedRateLimited)
            status_code = (
                error.status_code if isinstance(error, SourceHTTPError) else None
            )
            blocked = blocked or status_code == 403
            rate_limited = rate_limited or status_code == 429
            error_name = type(error).__name__.casefold()
            failure_category = (
                "source_access_blocked"
                if blocked
                else "rate_limited"
                if rate_limited
                else "authentication_error"
                if status_code in {401, 407}
                else "transient_server_error"
                if status_code in {500, 502, 503, 504}
                else "tls_certificate_error"
                if any(word in error_name for word in ("ssl", "tls", "certificate"))
                else "network_timeout"
                if "timeout" in error_name
                else "source_or_persistence_error"
            )
            state = {
                **state,
                "status": (
                    "blocked"
                    if blocked
                    else "rate_limited"
                    if rate_limited
                    else "error"
                ),
                "stop_reason": (
                    "http_403"
                    if blocked
                    else "http_429"
                    if rate_limited
                    else type(error).__name__
                ),
                "retry_after_seconds": getattr(error, "retry_after_seconds", None),
                "failure_category": failure_category,
                "error": str(error),
                "products_queued": scraper.products_queued,
                "persistence": scraper.persistence_stats.copy(),
            }
            if blocked and recovery_enabled("araujo"):
                source_checkpoint = _source_checkpoint(scraper)
                state = record_403_failure(
                    state,
                    source="araujo",
                    now=datetime.now(UTC),
                    retry_after_seconds=getattr(error, "retry_after_seconds", None),
                    last_valid_checkpoint=source_checkpoint or None,
                )
                if state.get("http_403_alert"):
                    _log(
                        "persistent_403_alert",
                        cycles=state["http_403_failed_cycles"],
                        source=state["http_403_origin"],
                    )
                _log(
                    "403_cycle_failed",
                    attempt=state["http_403_attempts"],
                    failed_cycles=state["http_403_failed_cycles"],
                    cooldown_until=state["http_403_cooldown_until"],
                    circuit_open=state["http_403_circuit_open"],
                )
            _log(
                "run_failed",
                error_type=type(error).__name__,
                reason=str(error),
            )
        state["finished_at"] = datetime.now(UTC).isoformat()
        if state.get("stop_reason") != "http_403":
            state = clear_403_state(state)
        _write_checkpoint(state)
        _log(
            "run_finished",
            status=state["status"],
            products=state["products_queued"],
            finished_at=state["finished_at"],
        )


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        _log("stopped")
