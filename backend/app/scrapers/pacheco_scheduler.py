"""Single-process, configurable daily scheduler for the Pacheco scraper."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scrapers.pacheco import PachecoScraper


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
        if state.get("stop_reason") in {
            "http_403",
            "source_authorization_required: configure an authorized partner feed",
        }:
            _log("paused", reason="authorized_source_access_required")
            await asyncio.sleep(3600)
            continue

        now = datetime.now(UTC)
        due = _next_run(now, state)
        delay = max(0.0, (due - now).total_seconds())
        if delay:
            _log("waiting", next_run=due.isoformat())
            await asyncio.sleep(delay)

        started = datetime.now(UTC)
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
