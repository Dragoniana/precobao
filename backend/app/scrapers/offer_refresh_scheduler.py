"""Periodic, bounded refresh of existing Indiana and Araújo offers."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia
from app.scrapers.offer_refresh import STATE_DIR, refresh, refresh_images

SOURCE_CNPJ = {
    "indiana": "00000000000100",
    "araujo": "17256512000116",
    "pacheco": "33438250000167",
}
BRASILIA = ZoneInfo("America/Sao_Paulo")


def _write_monitoring(key: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{key}-monitoring.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _catalog_metrics(source: str) -> dict:
    with SessionLocal() as db:
        pharmacy_id = db.scalar(
            select(Farmacia.id).where(Farmacia.cnpj == SOURCE_CNPJ[source])
        )
        if pharmacy_id is None:
            return {
                "total_offers": 0,
                "latest_observation_at": None,
                "oldest_observation_at": None,
            }
        total = (
            db.scalar(
                select(func.count())
                .select_from(OfertaFarmacia)
                .where(OfertaFarmacia.farmacia_id == pharmacy_id)
            )
            or 0
        )
        latest = db.scalar(
            select(func.max(OfertaFarmacia.observado_em)).where(
                OfertaFarmacia.farmacia_id == pharmacy_id
            )
        )
        oldest = db.scalar(
            select(func.min(OfertaFarmacia.observado_em)).where(
                OfertaFarmacia.farmacia_id == pharmacy_id
            )
        )
        return {
            "total_offers": total,
            "latest_observation_at": latest.isoformat() if latest else None,
            "oldest_observation_at": oldest.isoformat() if oldest else None,
        }


def _run_monitored(key: str, source: str, interval: float, operation) -> None:
    path = STATE_DIR / f"{key}-monitoring.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    started = datetime.now(UTC)
    attempt = {
        **previous,
        "source": source,
        "status": "running",
        "started_at": started.isoformat(),
        "next_run_at": (started + timedelta(minutes=interval)).isoformat(),
    }
    _write_monitoring(key, attempt)
    try:
        result = operation()
        finished = datetime.now(UTC)
        errors = result.get("errors", [])
        identity_reasons = {
            "ean_mismatch",
            "sku_mismatch",
            "product_url_mismatch",
            "sku_missing_or_ambiguous",
        }
        non_identity_errors = [
            error
            for error in errors
            if not isinstance(error, dict)
            or error.get("reason") not in identity_reasons
        ]
        success = (
            not result.get("stop_reason")
            and not result.get("unavailable")
            and not non_identity_errors
        )
        degraded = bool(
            result.get("mismatch")
            or errors
            or result.get("unavailable")
            or result.get("stop_reason")
        )
        failures = 0 if success else previous.get("consecutive_failures", 0) + 1
        metrics = _catalog_metrics(source)
        state = {
            **attempt,
            **metrics,
            "status": "degraded" if degraded else "success",
            "success": success,
            "finished_at": finished.isoformat(),
            "next_run_at": (finished + timedelta(minutes=interval)).isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "consecutive_failures": failures,
            "last_success_at": finished.isoformat()
            if success
            else previous.get("last_success_at"),
            "products_checked": result.get("checked", 0),
            "price_events": result.get("events", 0),
            "unchanged": result.get("unchanged", 0),
            "unavailable": result.get("unavailable", 0),
            "ean_or_identity_conflicts": result.get("mismatch", 0),
            "errors": errors,
            "cursor": result.get("cursor"),
            "stop_reason": result.get("stop_reason"),
        }
        _write_monitoring(key, state)
        print(
            json.dumps({**result, "monitoring": state}, ensure_ascii=False), flush=True
        )
    except Exception as error:
        finished = datetime.now(UTC)
        state = {
            **attempt,
            "status": "failed",
            "success": False,
            "finished_at": finished.isoformat(),
            "next_run_at": (finished + timedelta(minutes=interval)).isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "consecutive_failures": previous.get("consecutive_failures", 0) + 1,
            "last_success_at": previous.get("last_success_at"),
            "last_error": f"{type(error).__name__}: {error}",
        }
        _write_monitoring(key, state)
        print(
            f"{key} refresh failed: {state['last_error']}; "
            f"consecutive_failures={state['consecutive_failures']}",
            flush=True,
        )


def _source_authorized(source: str) -> bool:
    source = source.removeprefix("images-")
    variable = {
        "araujo": "ARAUJO_PUBLIC_CATALOG_AUTHORIZED",
        "pacheco": "PACHECO_PUBLIC_API_AUTHORIZED",
    }.get(source)
    if variable is None:
        return True
    return os.getenv(variable, "false").casefold() in {"1", "true", "yes", "on"}


def _due(source: str, now: datetime, interval_minutes: float) -> bool:
    if not _source_authorized(source):
        return False
    source_name = source.removeprefix("images-")
    if source_name != "indiana" and os.getenv(
        f"{source_name.upper()}_ACCESS_CONFIRMED", "false"
    ).casefold() not in {"1", "true", "yes", "on"}:
        return False
    try:
        state = json.loads((STATE_DIR / f"{source}.json").read_text(encoding="utf-8"))
        status = state.get("status")
        if status == "ACCESS_BLOCKED":
            return False
        retry_at = state.get("retry_at")
        if state.get("status") == "RATE_LIMITED" and retry_at:
            return now >= datetime.fromisoformat(retry_at)
        last = datetime.fromisoformat(state["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    interval = timedelta(minutes=interval_minutes)
    retry_after = timedelta(seconds=max(0, int(state.get("retry_after_seconds") or 0)))
    return now >= last + max(interval, retry_after)


def _daily_slot_due(
    key: str, now: datetime, times: list[str]
) -> tuple[bool, str | None]:
    """Return the latest due Brasilia slot, with persistent per-job idempotence."""
    local_now = now.astimezone(BRASILIA)
    due = []
    for value in times:
        hour, minute = (int(part) for part in value.split(":"))
        slot = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot <= local_now and local_now - slot <= timedelta(hours=6):
            due.append(slot)
    if not due:
        return False, None
    selected = max(due).isoformat()
    try:
        state = json.loads(
            (STATE_DIR / f"{key}-schedule.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        state = {}
    if state.get("last_slot") == selected:
        return False, selected
    return True, selected


def _mark_daily_slot(key: str, slot: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{key}-schedule.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "last_slot": slot,
                "status": "WAITING",
                "updated_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def serve() -> None:
    if os.getenv("OFFER_REFRESH_ENABLED", "false").casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        print("Offer refresh disabled", flush=True)
        while True:
            time.sleep(3600)
    interval = float(os.getenv("OFFER_REFRESH_INTERVAL_MINUTES", "30"))
    delay = float(os.getenv("OFFER_REFRESH_REQUEST_DELAY_SECONDS", "1"))
    sources = {
        source.strip().casefold()
        for source in os.getenv("OFFER_REFRESH_SOURCES", "indiana").split(",")
        if source.strip()
    }
    image_sources = {
        source.strip().casefold()
        for source in os.getenv("OFFER_IMAGE_REFRESH_SOURCES", "").split(",")
        if source.strip()
    }
    allowed_sources = {"indiana", "araujo", "pacheco"}
    allowed_image_sources = {"indiana", "araujo", "pacheco"}
    if not sources <= allowed_sources or not image_sources <= allowed_image_sources:
        raise ValueError("Unknown source in offer refresh configuration")
    limits = {
        source: int(os.getenv(f"OFFER_REFRESH_{source.upper()}_BATCH", "200"))
        for source in sources
    }
    image_limits = {
        source: int(os.getenv(f"OFFER_IMAGE_REFRESH_{source.upper()}_BATCH", "150"))
        for source in image_sources
    }
    if (
        interval <= 0
        or delay < 0
        or any(limit <= 0 for limit in [*limits.values(), *image_limits.values()])
    ):
        raise ValueError("Invalid offer refresh schedule")
    while True:
        daily_sources = {
            source.strip().casefold()
            for source in os.getenv(
                "OFFER_REFRESH_DAILY_SOURCES", "araujo,pacheco"
            ).split(",")
            if source.strip()
        }
        times = [
            item.strip()
            for item in os.getenv("OFFER_REFRESH_SCHEDULE_TIMES", "08:00,20:00").split(
                ","
            )
            if item.strip()
        ]
        if len(times) != 2:
            raise ValueError(
                "OFFER_REFRESH_SCHEDULE_TIMES must contain two daily times"
            )
        for source in daily_sources:
            if source not in {"araujo", "pacheco"}:
                raise ValueError("Unknown source in OFFER_REFRESH_DAILY_SOURCES")
            if not _source_authorized(source):
                continue
            if os.getenv(
                f"{source.upper()}_ACCESS_CONFIRMED", "false"
            ).casefold() not in {"1", "true", "yes", "on"}:
                continue
            now = datetime.now(UTC)
            due, slot = _daily_slot_due(source, now, times)
            if not due or not slot:
                continue
            image_limit = int(
                os.getenv(f"OFFER_IMAGE_REFRESH_{source.upper()}_BATCH", "100")
            )
            price_limit = int(os.getenv(f"OFFER_REFRESH_{source.upper()}_BATCH", "100"))
            _run_monitored(
                f"images-{source}",
                source,
                720,
                lambda s=source, n=image_limit: refresh_images(s, n, delay),
            )
            _run_monitored(
                source,
                source,
                720,
                lambda s=source, n=price_limit: refresh(s, n, delay),
            )
            _mark_daily_slot(source, slot)

        for source, limit in image_limits.items():
            state_key = f"images-{source}"
            if not _due(state_key, datetime.now(UTC), interval):
                continue
            _run_monitored(
                state_key,
                source,
                interval,
                lambda: refresh_images(source, limit, delay),
            )

        for source, limit in limits.items():
            if not _due(source, datetime.now(UTC), interval):
                continue
            _run_monitored(
                source, source, interval, lambda: refresh(source, limit, delay)
            )
        time.sleep(60)


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        print("Offer refresh stopped", flush=True)
