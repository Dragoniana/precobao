"""Single-process, configurable daily scheduler for the Pacheco scraper."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scrapers.pacheco import PachecoScraper
from app.scrapers.source_403_recovery import (
    begin_due_attempt,
    checkpoint_snapshot,
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
    SourceMode,
    direct_access_ready,
    source_mode,
)


def _log(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "service": "pacheco-scheduler",
                "event": event,
                "time": datetime.now(UTC).isoformat(),
                **fields,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _state_path() -> Path:
    state_dir = Path(
        os.getenv(
            "PACHECO_STATE_DIR",
            str(Path(__file__).resolve().parents[2] / "data" / "pacheco"),
        )
    )
    return state_dir / "checkpoint.json"


def _checkpoint() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_checkpoint(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pacheco-scheduler-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _next_run(now: datetime, state: dict) -> datetime:
    """Use the durable prior result so container restarts don't reset cadence."""
    completed = state.get("finished_at")
    if completed:
        try:
            finished_at = datetime.fromisoformat(completed)
        except ValueError:
            finished_at = now
        interval = timedelta(hours=float(os.getenv("PACHECO_INTERVAL_HOURS", "24")))
        retry = timedelta(
            minutes=float(os.getenv("PACHECO_FAILURE_RETRY_MINUTES", "60"))
        )
        status = state.get("status")
        if status in {"complete", "partial"}:
            return finished_at + interval
        source_retry = timedelta(
            seconds=max(0, int(state.get("retry_after_seconds") or 0))
        )
        return finished_at + max(retry, source_retry)

    # First run only occurs when explicitly enabled after a controlled run.
    if state:
        retry = timedelta(
            minutes=float(os.getenv("PACHECO_FAILURE_RETRY_MINUTES", "60"))
        )
        started = state.get("started_at")
        try:
            return datetime.fromisoformat(started) + retry if started else now
        except ValueError:
            return now
    return now


async def serve() -> None:
    enabled = os.getenv("PACHECO_SCHEDULER_ENABLED", "false").casefold()
    if enabled not in {"1", "true", "yes", "on"}:
        _log("disabled", reason="PACHECO_SCHEDULER_ENABLED=false")
        while True:
            await asyncio.sleep(3600)

    interval = float(os.getenv("PACHECO_INTERVAL_HOURS", "24"))
    retry_minutes = float(os.getenv("PACHECO_FAILURE_RETRY_MINUTES", "60"))
    if interval <= 0 or retry_minutes <= 0:
        raise ValueError("Pacheco scheduler intervals must be positive")

    while True:
        state = _checkpoint()
        recovering_403 = False
        mode = source_mode("pacheco")
        if mode is SourceMode.DISABLED:
            _log("paused", reason="source_disabled", source_mode=mode.value)
            await asyncio.sleep(3600)
            continue
        if state.get("stop_reason") == "http_403":
            if not recovery_enabled("pacheco") or recovery_manually_paused("pacheco"):
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
                    state, source="pacheco", now=datetime.now(UTC)
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
                    "pacheco",
                    due,
                    now=lambda: datetime.now(UTC),
                    sleep=asyncio.sleep,
                ):
                    _log("paused", reason="manual_403_recovery_pause")
                    continue
            state = _checkpoint()
            due_state = begin_due_attempt(state, now=datetime.now(UTC))
            if due_state is None:
                continue
            if due_state != state:
                state = due_state
                _write_checkpoint(state)
            recovering_403 = True
        if mode is SourceMode.FEED and not (
            os.getenv("PACHECO_PARTNER_FEED_FILE", "").strip()
            or os.getenv("PACHECO_PARTNER_FEED_URL", "").strip()
        ):
            _log("paused", reason="authorized_feed_not_configured")
            await asyncio.sleep(3600)
            continue
        if mode is SourceMode.DIRECT and not direct_access_ready("pacheco"):
            _log("paused", reason="direct_access_not_confirmed")
            await asyncio.sleep(3600)
            continue
        if state.get("stop_reason") == "PACHECO_DIRECT_ACCESS_NOT_CONFIRMED":
            _log("paused", reason="direct_access_not_confirmed")
            await asyncio.sleep(3600)
            continue

        now = datetime.now(UTC)
        due = now if recovering_403 else _next_run(now, state)
        delay = max(0.0, (due - now).total_seconds())
        if delay:
            _log("waiting", next_run=due.isoformat())
            await asyncio.sleep(delay)

        started = datetime.now(UTC)
        previous_state = state
        _log("run_started", started_at=started.isoformat())
        try:
            await PachecoScraper().run()
        except Exception as error:
            _log(
                "run_failed",
                error_type=type(error).__name__,
                error=str(error),
            )
        finished = datetime.now(UTC)
        elapsed = (finished - started).total_seconds()
        state = _checkpoint()
        if state.get("stop_reason") == "http_403" and recovery_enabled("pacheco"):
            recovery_metadata = {
                key: value
                for key, value in previous_state.items()
                if key.startswith("http_403_")
            }
            checkpoint = checkpoint_snapshot(state) or checkpoint_snapshot(
                previous_state
            )
            state = record_403_failure(
                {**state, **recovery_metadata},
                source="pacheco",
                now=finished,
                retry_after_seconds=state.get("retry_after_seconds"),
                last_valid_checkpoint=checkpoint or None,
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
        elif state.get("stop_reason") != "http_403":
            state = clear_403_state(state)
        _write_checkpoint(state)
        _log(
            "run_finished",
            status=state.get("status", "unknown"),
            products=state.get("products_enqueued", 0),
            duration_seconds=round(elapsed, 1),
        )


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        _log("stopped")
