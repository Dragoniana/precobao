"""Periodic, bounded refresh of existing Indiana and Araújo offers."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

from app.scrapers.offer_refresh import STATE_DIR, refresh, refresh_images


def _due(source: str, now: datetime, interval_minutes: float) -> bool:
    try:
        state = json.loads((STATE_DIR / f"{source}.json").read_text(encoding="utf-8"))
        if state.get("stop_reason") == "http_403":
            return False
        last = datetime.fromisoformat(state["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return True
    return now >= last + timedelta(minutes=interval_minutes)


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
    limits = {
        "indiana": int(os.getenv("OFFER_REFRESH_INDIANA_BATCH", "200")),
        "araujo": int(os.getenv("OFFER_REFRESH_ARAUJO_BATCH", "20")),
    }
    image_limits = {
        "indiana": int(os.getenv("OFFER_IMAGE_REFRESH_INDIANA_BATCH", "150")),
        "araujo": int(os.getenv("OFFER_IMAGE_REFRESH_ARAUJO_BATCH", "20")),
        "pacheco": int(os.getenv("OFFER_IMAGE_REFRESH_PACHECO_BATCH", "100")),
    }
    if (
        interval <= 0
        or delay < 0
        or any(limit <= 0 for limit in [*limits.values(), *image_limits.values()])
    ):
        raise ValueError("Invalid offer refresh schedule")
    while True:
        for source, limit in image_limits.items():
            state_key = f"images-{source}"
            if not _due(state_key, datetime.now(UTC), interval):
                continue
            try:
                result = refresh_images(source, limit, delay)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as error:
                print(
                    f"{state_key} refresh failed: {type(error).__name__}: {error}",
                    flush=True,
                )

        for source, limit in limits.items():
            if not _due(source, datetime.now(UTC), interval):
                continue
            try:
                result = refresh(source, limit, delay)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as error:
                print(
                    f"{source} refresh failed: {type(error).__name__}: {error}",
                    flush=True,
                )
        time.sleep(60)


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        print("Offer refresh stopped", flush=True)
