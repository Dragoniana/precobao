from datetime import UTC, datetime, timedelta

import pytest

from app.scrapers import araujo_scheduler, pacheco_scheduler, source_403_recovery
from app.scrapers.araujo import AraujoScraper
from app.scrapers.reset_403_circuit import reset
from app.scrapers.source_modes import SourceHTTPError


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


def test_403_recovery_success_clears_episode(monkeypatch):
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    blocked = source_403_recovery.record_403_failure(
        {"status": "running"}, source="araujo", now=now
    )
    complete = source_403_recovery.clear_403_state(
        {**blocked, "status": "complete", "stop_reason": "imported"}
    )

    assert complete["status"] == "complete"
    assert complete["stop_reason"] == "imported"
    assert "http_403_attempts" not in complete


def test_operator_reset_preserves_checkpoint_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))
    state = source_403_recovery.record_403_failure(
        {"last_completed_page": 7, "products_queued": 35},
        source="araujo",
        now=datetime.now(UTC),
    )
    araujo_scheduler._write_checkpoint(state)

    reset("araujo")
    updated = araujo_scheduler._checkpoint()

    assert updated["last_completed_page"] == 7
    assert updated["products_queued"] == 35
    assert updated["stop_reason"] == "operator_reset_http_403"
    assert "http_403_attempts" not in updated


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["araujo", "pacheco"])
async def test_scheduler_retries_403_once_then_clears_after_success(
    source, tmp_path, monkeypatch
):
    scheduler = araujo_scheduler if source == "araujo" else pacheco_scheduler
    prefix = source.upper()
    state_key = "ARAUJO_STATE_DIR" if source == "araujo" else "PACHECO_STATE_DIR"
    monkeypatch.setenv(state_key, str(tmp_path))
    monkeypatch.setenv(f"{prefix}_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv(f"{prefix}_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv(f"{prefix}_403_RECOVERY_ENABLED", "true")
    monkeypatch.setenv(f"{prefix}_PUBLIC_CATALOG_AUTHORIZED", "true")
    if source == "pacheco":
        monkeypatch.setenv("PACHECO_PUBLIC_API_AUTHORIZED", "true")
    monkeypatch.setenv(f"{prefix}_ACCESS_CONFIRMED", "true")
    waits = []
    runs = 0

    class StopSchedulerLoop(Exception):
        pass

    async def fake_sleep(seconds):
        waits.append(seconds)
        if seconds >= 3600:
            raise StopSchedulerLoop

    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    async def due_immediately(_source, _due, **_kwargs):
        return True

    monkeypatch.setattr(scheduler, "wait_for_retry_time", due_immediately)
    def begin_now(state, *, now):
        due_state = dict(state)
        due_state["http_403_next_retry_at"] = now.isoformat()
        return source_403_recovery.begin_due_attempt(due_state, now=now)

    monkeypatch.setattr(scheduler, "begin_due_attempt", begin_now)

    if source == "araujo":

        class FakeScraper:
            products_queued = 0
            persistence_stats = {}

            def __init__(self, **_kwargs):
                pass

            async def run(self):
                nonlocal runs
                runs += 1
                if runs == 1:
                    raise SourceHTTPError(403)

        monkeypatch.setattr(araujo_scheduler, "AraujoScraper", FakeScraper)
    else:

        class FakeScraper:
            async def run(self):
                nonlocal runs
                runs += 1
                pacheco_scheduler._write_checkpoint(
                    {
                        "status": "blocked" if runs == 1 else "complete",
                        "stop_reason": "http_403" if runs == 1 else "completed",
                        "last_http_status": 403 if runs == 1 else 200,
                        "products_enqueued": 0,
                        "finished_at": datetime.now(UTC).isoformat(),
                    }
                )

        monkeypatch.setattr(pacheco_scheduler, "PachecoScraper", FakeScraper)

    with pytest.raises(StopSchedulerLoop):
        await scheduler.serve()

    state = scheduler._checkpoint()
    assert runs == 2
    assert state.get("stop_reason") != "http_403"
    assert "http_403_attempts" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["araujo", "pacheco"])
async def test_scheduler_opens_persistent_403_circuit_after_three_runs(
    source, tmp_path, monkeypatch
):
    scheduler = araujo_scheduler if source == "araujo" else pacheco_scheduler
    prefix = source.upper()
    state_key = "ARAUJO_STATE_DIR" if source == "araujo" else "PACHECO_STATE_DIR"
    monkeypatch.setenv(state_key, str(tmp_path))
    monkeypatch.setenv(f"{prefix}_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv(f"{prefix}_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv(f"{prefix}_403_RECOVERY_ENABLED", "true")
    monkeypatch.setenv(f"{prefix}_PUBLIC_CATALOG_AUTHORIZED", "true")
    if source == "pacheco":
        monkeypatch.setenv("PACHECO_PUBLIC_API_AUTHORIZED", "true")
    monkeypatch.setenv(f"{prefix}_ACCESS_CONFIRMED", "true")
    waits = []
    runs = 0

    class StopSchedulerLoop(Exception):
        pass

    async def fake_sleep(seconds):
        waits.append(seconds)
        if seconds >= 3600:
            raise StopSchedulerLoop

    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    class StopBeforeCooldown(Exception):
        pass

    async def stop_at_cooldown(_source, due, *, now, **_kwargs):
        if (due - now()).total_seconds() > 1800:
            raise StopBeforeCooldown
        return True

    monkeypatch.setattr(scheduler, "wait_for_retry_time", stop_at_cooldown)

    def begin_now(state, *, now):
        due_state = dict(state)
        due_state["http_403_next_retry_at"] = now.isoformat()
        return source_403_recovery.begin_due_attempt(due_state, now=now)

    monkeypatch.setattr(scheduler, "begin_due_attempt", begin_now)

    if source == "araujo":

        class FakeScraper:
            products_queued = 0
            persistence_stats = {}

            def __init__(self, **_kwargs):
                pass

            async def run(self):
                nonlocal runs
                runs += 1
                raise SourceHTTPError(403)

        monkeypatch.setattr(araujo_scheduler, "AraujoScraper", FakeScraper)
    else:

        class FakeScraper:
            async def run(self):
                nonlocal runs
                runs += 1
                pacheco_scheduler._write_checkpoint(
                    {
                        "status": "blocked",
                        "stop_reason": "http_403",
                        "last_http_status": 403,
                        "products_enqueued": 0,
                        "finished_at": datetime.now(UTC).isoformat(),
                    }
                )

        monkeypatch.setattr(pacheco_scheduler, "PachecoScraper", FakeScraper)

    with pytest.raises(StopBeforeCooldown):
        await scheduler.serve()

    state = scheduler._checkpoint()
    assert runs == 3
    assert state["http_403_attempts"] == 3
    assert state["http_403_failed_cycles"] == 1
    assert state["http_403_cooldown"] is True
    assert state["http_403_cooldown_until"] is not None
    assert state["http_403_circuit_open"] is False


def test_araujo_import_lock_prevents_concurrent_runs(tmp_path):
    first = AraujoScraper()
    second = AraujoScraper()
    first.state_dir = tmp_path
    second.state_dir = tmp_path

    with first._run_lock():
        with pytest.raises(RuntimeError, match="Outra coleta Araújo"):
            with second._run_lock():
                pass
