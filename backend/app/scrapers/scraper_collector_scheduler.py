"""Opt-in 08:00/20:00 scheduler for a separately authorized collector host."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.scrapers.scraper_collector import (
    _exclusive_lock,
    _log,
    _state_dir,
    _write_state,
    collect_source,
)


def _parse_slots() -> tuple[tuple[int, int], ...]:
    values = os.getenv("SCRAPER_COLLECTOR_SCHEDULE_TIMES", "08:00,20:00")
    slots = []
    for value in values.split(","):
        try:
            hour, minute = (int(part) for part in value.strip().split(":", maxsplit=1))
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid SCRAPER_COLLECTOR_SCHEDULE_TIMES") from error
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Invalid SCRAPER_COLLECTOR_SCHEDULE_TIMES")
        slots.append((hour, minute))
    if not slots:
        raise ValueError("At least one collector schedule time is required")
    return tuple(sorted(set(slots)))


def _next_slot(now: datetime, slots: tuple[tuple[int, int], ...]) -> datetime:
    zone = ZoneInfo(os.getenv("SCRAPER_COLLECTOR_TIMEZONE", "America/Sao_Paulo"))
    local_now = now.astimezone(zone)
    candidates = []
    for day_offset in range(2):
        date = local_now.date() + timedelta(days=day_offset)
        for hour, minute in slots:
            candidate = datetime(
                date.year,
                date.month,
                date.day,
                hour,
                minute,
                tzinfo=zone,
            )
            if candidate > local_now:
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError("No future collector schedule slot found")
    return min(candidates).astimezone(UTC)


def _scheduler_state_path() -> Path:
    return _state_dir() / "scheduler.json"


def _load_scheduler_state() -> dict:
    try:
        return json.loads(_scheduler_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


async def _run_slot(slot: datetime, sources: tuple[str, ...]) -> None:
    local_slot = slot.astimezone(
        ZoneInfo(os.getenv("SCRAPER_COLLECTOR_TIMEZONE", "America/Sao_Paulo"))
    )
    slot_key = local_slot.strftime("%Y-%m-%dT%H:%M%z")
    state = _load_scheduler_state()
    if state.get("last_slot") == slot_key:
        return

    state.update(last_slot=slot_key, started_at=datetime.now(UTC).isoformat())
    _write_state("scheduler", state)
    _log("slot_started", slot=slot_key, sources=list(sources))
    for source in sources:
        try:
            result = await collect_source(source)
            state.setdefault("sources", {})[source] = {
                "status": result.get("status"),
                "finished_at": result.get("finished_at"),
                "stop_reason": result.get("stop_reason"),
            }
        except RuntimeError as error:
            state.setdefault("sources", {})[source] = {
                "status": "SKIPPED",
                "stop_reason": str(error),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        _write_state("scheduler", state)
    state["finished_at"] = datetime.now(UTC).isoformat()
    _write_state("scheduler", state)
    _log("slot_finished", slot=slot_key)


async def serve() -> None:
    if os.getenv("SCRAPER_COLLECTOR_ENABLED", "false").casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        _log("disabled", reason="SCRAPER_COLLECTOR_ENABLED=false")
        while True:
            await asyncio.sleep(3600)

    raw_sources = os.getenv("SCRAPER_COLLECTOR_SOURCES", "araujo,pacheco")
    sources = tuple(source.strip().casefold() for source in raw_sources.split(","))
    if not sources or any(source not in {"araujo", "pacheco"} for source in sources):
        raise ValueError("SCRAPER_COLLECTOR_SOURCES must be araujo,pacheco")
    slots = _parse_slots()
    global_lock = _state_dir() / "locks" / "scheduler.lock"

    while True:
        now = datetime.now(UTC)
        next_slot = _next_slot(now, slots)
        local_now = now.astimezone(
            ZoneInfo(os.getenv("SCRAPER_COLLECTOR_TIMEZONE", "America/Sao_Paulo"))
        )
        current_slot = local_now.strftime("%Y-%m-%dT%H:%M%z")
        if (local_now.hour, local_now.minute) in slots and _load_scheduler_state().get(
            "last_slot"
        ) != current_slot:
            next_slot = now
        delay = max(0.0, (next_slot - now).total_seconds())
        if delay:
            _log("waiting", next_slot=next_slot.isoformat())
            await asyncio.sleep(delay)
            continue
        try:
            with _exclusive_lock(global_lock):
                await _run_slot(next_slot, sources)
        except RuntimeError as error:
            _log("slot_skipped", reason=str(error))
            await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        _log("stopped")
