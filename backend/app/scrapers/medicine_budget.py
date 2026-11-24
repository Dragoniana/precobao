"""Persistent shared rolling-window budget for distinct pharmacy medicines."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scrapers.source_rate_limiter import _exclusive_file_lock


def _settings() -> tuple[int, int, int]:
    limit = int(os.getenv("SCRAPER_MEDICINE_BUDGET_MAX", "50"))
    window = int(os.getenv("SCRAPER_MEDICINE_BUDGET_WINDOW_SECONDS", "900"))
    share = int(
        os.getenv("SCRAPER_MEDICINE_BUDGET_SOURCE_SHARE", str((limit + 1) // 2))
    )
    if limit < 1 or window < 1 or share < 1 or share > limit:
        raise ValueError("Invalid shared medicine budget configuration")
    return limit, window, share


def _state_path() -> Path:
    configured = os.getenv("SCRAPER_MEDICINE_BUDGET_STATE_DIR", "").strip()
    root = (
        Path(configured)
        if configured
        else (
            Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
            / "medicine-budget"
        )
    )
    return root.resolve() / "processed-medicines.json"


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        limit, window, share = _settings()
        return {
            "version": 1,
            "limit": limit,
            "window_seconds": window,
            "source_share": share,
            "events": [],
            "source_runs": [],
        }
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("MEDICINE_BUDGET_STATE_INVALID_FAIL_CLOSED") from error
    limit, window, share = _settings()
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("limit", limit) != limit
        or value.get("window_seconds", window) != window
        or value.get("source_share", share) != share
        or not isinstance(value.get("events"), list)
        or not isinstance(value.get("source_runs"), list)
    ):
        raise RuntimeError("MEDICINE_BUDGET_STATE_INVALID_FAIL_CLOSED")
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".medicine-budget-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
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


def _active(state: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    window = int(state["window_seconds"])
    events = [
        event
        for event in state["events"]
        if (now - _parse_time(event["at"])).total_seconds() < window
    ]
    runs = [
        run
        for run in state["source_runs"]
        if (now - _parse_time(run["at"])).total_seconds() < window
    ]
    return events, runs


def _identity(source: str, sku: str) -> str:
    normalized_source = source.casefold().strip()
    normalized_sku = sku.strip().casefold()
    if normalized_source not in {"araujo", "pacheco"} or not normalized_sku:
        raise ValueError("Medicine budget requires source and stable SKU")
    return hashlib.sha256(f"{normalized_source}\0{normalized_sku}".encode()).hexdigest()


def mark_source_run(source: str, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    path = _state_path()
    with _exclusive_file_lock(path.with_suffix(".lock")):
        state = _read(path)
        _events, runs = _active(state, now)
        runs.append({"source": source.casefold(), "at": now.isoformat()})
        state["source_runs"] = runs
        _write(path, state)


def reserve_product(source: str, sku: str, *, now: datetime | None = None) -> str:
    """Return RESERVED, DUPLICATE, EXHAUSTED, or IDENTITY_MISSING atomically."""
    if not sku.strip():
        return "IDENTITY_MISSING"
    identity = _identity(source, sku)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    path = _state_path()
    with _exclusive_file_lock(path.with_suffix(".lock")):
        state = _read(path)
        events, runs = _active(state, now)
        if any(event["identity"] == identity for event in events):
            state["events"] = events
            state["source_runs"] = runs
            return "DUPLICATE"
        limit = int(state["limit"])
        share = int(state["source_share"])
        if len(events) >= limit:
            state["events"] = events
            state["source_runs"] = runs
            return "EXHAUSTED"
        source = source.casefold()
        other = "pacheco" if source == "araujo" else "araujo"
        own_count = sum(1 for event in events if event["source"] == source)
        other_count = sum(1 for event in events if event["source"] == other)
        quarter = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        other_slot_start = quarter + timedelta(minutes=7 if source == "araujo" else 0)
        other_had_opportunity = any(
            run["source"] == other and _parse_time(run["at"]) >= other_slot_start
            for run in runs
        )
        allowed_for_source = share
        if other_had_opportunity:
            allowed_for_source += max(0, share - other_count)
        if own_count >= allowed_for_source:
            state["events"] = events
            state["source_runs"] = runs
            return "EXHAUSTED"
        events.append(
            {
                "identity": identity,
                "source": source,
                "sku": sku.strip(),
                "at": now.isoformat(),
                "status": "RESERVED",
                "reason": None,
            }
        )
        state["events"] = events
        state["source_runs"] = runs
        _write(path, state)
    return "RESERVED"


def update_product_status(
    source: str,
    sku: str,
    status: str,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    identity = _identity(source, sku)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    path = _state_path()
    with _exclusive_file_lock(path.with_suffix(".lock")):
        state = _read(path)
        events, runs = _active(state, now)
        for event in reversed(events):
            if event["identity"] == identity:
                event["status"] = status
                event["reason"] = reason
                event["updated_at"] = now.isoformat()
                break
        state["events"] = events
        state["source_runs"] = runs
        _write(path, state)


def medicine_budget_status(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    path = _state_path()
    with _exclusive_file_lock(path.with_suffix(".lock")):
        state = _read(path)
    events, _runs = _active(state, now)
    by_source = {}
    for source in ("araujo", "pacheco"):
        selected = [event for event in events if event["source"] == source]
        by_source[source] = {
            "processed": len(selected),
            "statuses": {
                status: sum(1 for event in selected if event["status"] == status)
                for status in (
                    "RESERVED",
                    "COMPLETE_WITH_IMAGE",
                    "COMPLETE_WITH_GENERIC_IMAGE",
                    "COMPLETE_WITHOUT_IMAGE",
                    "DEFERRED_IMAGE",
                    "FAILED",
                    "IDENTITY_CONFLICT",
                )
            },
        }
    return {
        "enabled": True,
        "limit": int(state["limit"]),
        "window_seconds": int(state["window_seconds"]),
        "source_share": int(state["source_share"]),
        "total": len(events),
        "remaining": max(0, int(state["limit"]) - len(events)),
        "by_source": by_source,
    }
