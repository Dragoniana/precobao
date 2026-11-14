from datetime import UTC, datetime, timedelta

import pytest

from app.scrapers import araujo_scheduler
from app.scrapers.araujo import AraujoScraper


def test_checkpoint_is_durable_and_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))
    state = {"status": "complete", "products_queued": 12}

    araujo_scheduler._write_checkpoint(state)

    assert araujo_scheduler._checkpoint() == state


def test_next_run_uses_configured_success_interval(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("ARAUJO_INTERVAL_HOURS", "12")

    due = araujo_scheduler._next_run(
        now, {"status": "complete", "finished_at": now.isoformat()}
    )

    assert due == now + timedelta(hours=12)


def test_next_run_uses_failure_retry_after(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("ARAUJO_FAILURE_RETRY_MINUTES", "15")
    state = {
        "status": "rate_limited",
        "finished_at": now.isoformat(),
        "retry_after_seconds": 3600,
    }

    assert araujo_scheduler._next_run(now, state) == now + timedelta(minutes=60)


def test_persistence_errors_are_not_reported_as_success():
    assert not araujo_scheduler._persistence_failed({"offers_updated": 1})
    assert araujo_scheduler._persistence_failed({"persistence_errors": 1})
    assert araujo_scheduler._persistence_failed({"image_errors": 1})


def test_araujo_import_lock_prevents_concurrent_runs(tmp_path):
    first = AraujoScraper()
    second = AraujoScraper()
    first.state_dir = tmp_path
    second.state_dir = tmp_path

    with first._run_lock():
        with pytest.raises(RuntimeError, match="Outra coleta Araújo"):
            with second._run_lock():
                pass
