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
        if not _feed_configured():
            _log("paused", reason="authorized_feed_not_configured")
            await asyncio.sleep(3600)
            continue

        state = _checkpoint()
        if state.get("stop_reason") == "http_403":
            _log("paused", reason="partner_feed_http_403")
            await asyncio.sleep(3600)
            continue
        now = datetime.now(UTC)
        due = _next_run(now, state)
        delay = max(0.0, (due - now).total_seconds())
        if delay:
            _log("waiting", next_run=due.isoformat())
            await asyncio.sleep(delay)

        started = datetime.now(UTC)
        state = {**state, "status": "running", "started_at": started.isoformat()}
        _write_checkpoint(state)
        _log("run_started", started_at=started.isoformat())
        scraper = AraujoScraper()
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
            }
        except Exception as error:
            blocked = isinstance(error, PartnerFeedForbidden)
            rate_limited = isinstance(error, PartnerFeedRateLimited)
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
                "error": str(error),
                "products_queued": scraper.products_queued,
                "persistence": scraper.persistence_stats.copy(),
            }
            _log(
                "run_failed",
                error_type=type(error).__name__,
                error=str(error),
            )
        state["finished_at"] = datetime.now(UTC).isoformat()
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
