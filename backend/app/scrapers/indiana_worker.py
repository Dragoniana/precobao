"""Independent low-resource scheduler for the existing Indiana scraper."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scrapers.indiana import extrair_todos_produtos
from app.scrapers.offer_refresh import _run_lock

logger = logging.getLogger("preco_bao.worker.indiana")
STATE_DIR = Path(os.getenv("SCRAPER_STATE_DIR", "/app/data/scraper_state"))


@contextmanager
def _singleton_lock(lock_file):
    """Lock the worker process on Linux and remain importable in Windows dev."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - exercised on Windows developers' machines
        import msvcrt

        lock_file.seek(0)
        if lock_file.read(1) == "":
            lock_file.write("0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError(
                "Another Indiana worker holds the singleton lock"
            ) from error
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Another Indiana worker holds the singleton lock"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _enabled(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).casefold() in {"1", "true", "yes", "on"}


def _write_json(path: Path, state: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def serve() -> None:
    # Modules for other pharmacies remain available, but their terms require
    # prior authorization, so production defaults are explicitly disabled.
    for key in (
        "INDIANA_SCRAPER_ENABLED",
        "ARAUJO_SCRAPER_ENABLED",
        "PACHECO_SCRAPER_ENABLED",
    ):
        logger.info("%s=%s", key, _enabled(key, key == "INDIANA_SCRAPER_ENABLED"))
    interval = float(os.getenv("SCRAPER_INTERVAL_HOURS", "24")) * 3600
    if interval < 3600:
        raise ValueError("SCRAPER_INTERVAL_HOURS must be at least 1 hour")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = (STATE_DIR / "indiana-worker.lock").open("a+")
    with _singleton_lock(lock_file):
        while True:
            started = datetime.now(UTC)
            if _enabled("INDIANA_SCRAPER_ENABLED", True):
                state_path = STATE_DIR / "indiana-last-run.json"
                try:
                    previous = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    previous = {}
                if previous.get("last_success_at") or previous.get("success"):
                    try:
                        scheduled = datetime.fromisoformat(previous["next_run_at"])
                        if scheduled.tzinfo is None:
                            scheduled = scheduled.replace(tzinfo=UTC)
                        wait_seconds = (scheduled - started).total_seconds()
                    except (KeyError, TypeError, ValueError):
                        wait_seconds = 0
                    if wait_seconds > 0:
                        logger.info(
                            "Indiana next scheduled run in %.0fs at %s",
                            wait_seconds,
                            scheduled.isoformat(),
                        )
                        time.sleep(min(wait_seconds, 3600))
                        continue
                _write_json(
                    state_path,
                    {
                        **previous,
                        "source": "indiana",
                        "status": "running",
                        "started_at": started.isoformat(),
                        "next_run_at": (
                            started + timedelta(seconds=interval)
                        ).isoformat(),
                    },
                )
                try:
                    while True:
                        try:
                            with _run_lock():
                                discovery = __import__("asyncio").run(
                                    extrair_todos_produtos()
                                )
                            break
                        except RuntimeError:
                            logger.info(
                                "Indiana refresh batch active; discovery waits 30s"
                            )
                            time.sleep(30)
                    finished = datetime.now(UTC)
                    complete = discovery["errors"] == 0 and discovery["pages"] > 0
                    success_at = (
                        finished.isoformat()
                        if complete
                        else previous.get("last_success_at")
                    )
                    state = {
                        **previous,
                        "source": "indiana",
                        "status": "success" if complete else "degraded",
                        "started_at": started.isoformat(),
                        "finished_at": finished.isoformat(),
                        "next_run_at": (
                            started + timedelta(seconds=interval)
                        ).isoformat(),
                        "success": complete,
                        "duration_seconds": round(
                            (finished - started).total_seconds(), 2
                        ),
                        "consecutive_failures": 0
                        if complete
                        else previous.get("consecutive_failures", 0) + 1,
                        "last_success_at": success_at,
                        "last_error": None if complete else "discovery_incomplete",
                        "discovery": discovery,
                    }
                    _write_json(state_path, state)
                    logger.info(
                        "Indiana run stats=%s", json.dumps(state, ensure_ascii=False)
                    )
                except Exception:
                    finished = datetime.now(UTC)
                    failed = {
                        **previous,
                        "source": "indiana",
                        "status": "failed",
                        "success": False,
                        "started_at": started.isoformat(),
                        "finished_at": finished.isoformat(),
                        "next_run_at": (
                            started + timedelta(seconds=interval)
                        ).isoformat(),
                        "duration_seconds": round(
                            (finished - started).total_seconds(), 2
                        ),
                        "consecutive_failures": previous.get("consecutive_failures", 0)
                        + 1,
                        "last_success_at": previous.get("last_success_at"),
                        "last_error": "see worker log",
                    }
                    _write_json(state_path, failed)
                    logger.exception("Indiana scheduled run failed")
            else:
                logger.info("Indiana scraper disabled; sleeping")
            time.sleep(max(0, interval - (datetime.now(UTC) - started).total_seconds()))


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    serve()
