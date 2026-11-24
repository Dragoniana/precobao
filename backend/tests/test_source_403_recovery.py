import json
from datetime import UTC, datetime, timedelta

import pytest

from app.scrapers import source_403_recovery as recovery


def test_three_attempts_then_one_hour_cooldown_then_new_cycle(monkeypatch):
    monkeypatch.setenv("ARAUJO_403_RECOVERY_ENABLED", "true")
    start = datetime(2026, 11, 20, 12, tzinfo=UTC)
    state = {"last_completed_page": 8, "products_queued": 64}

    state = recovery.record_403_failure(state, source="araujo", now=start)
    assert state["http_403_attempts"] == 1
    assert recovery.retry_due(state, start) == start + timedelta(minutes=5)

    second_at = recovery.retry_due(state, start)
    state = recovery.begin_due_attempt(state, now=second_at)
    state = recovery.record_403_failure(state, source="araujo", now=second_at)
    assert state["http_403_attempts"] == 2
    assert recovery.retry_due(state, second_at) == second_at + timedelta(minutes=5)

    third_at = recovery.retry_due(state, second_at)
    state = recovery.begin_due_attempt(state, now=third_at)
    state = recovery.record_403_failure(state, source="araujo", now=third_at)
    assert state["http_403_attempts"] == 3
    assert state["http_403_failed_cycles"] == 1
    assert state["http_403_cooldown"] is True
    cooldown = recovery.retry_due(state, third_at)
    assert cooldown == third_at + timedelta(hours=1)
    assert (
        recovery.begin_due_attempt(state, now=cooldown - timedelta(seconds=1)) is None
    )

    # A container restart only serializes/deserializes the durable checkpoint.
    restarted = json.loads(json.dumps(state))
    assert recovery.retry_due(restarted, third_at) == cooldown
    state = recovery.begin_due_attempt(restarted, now=cooldown)
    assert state["http_403_attempts"] == 0
    assert state["http_403_failed_cycles"] == 1
    assert state["http_403_cooldown"] is False
    assert state["http_403_next_retry_at"] is None

    next_cycle = recovery.record_403_failure(state, source="araujo", now=cooldown)
    assert next_cycle["http_403_attempts"] == 1
    assert next_cycle["http_403_failed_cycles"] == 1
    assert recovery.retry_due(next_cycle, cooldown) == cooldown + timedelta(minutes=5)
    assert next_cycle["http_403_last_valid_checkpoint"] == {
        "last_completed_page": 8,
        "products_queued": 64,
    }


def test_retry_after_extends_minimum_waits(monkeypatch):
    start = datetime(2026, 11, 20, 12, tzinfo=UTC)
    first = recovery.record_403_failure(
        {}, source="pacheco", now=start, retry_after_seconds=900
    )
    assert recovery.retry_due(first, start) == start + timedelta(seconds=900)

    state = first
    for attempt in (2, 3):
        due = recovery.retry_due(state, start)
        state = recovery.begin_due_attempt(state, now=due)
        state = recovery.record_403_failure(
            state,
            source="pacheco",
            now=due,
            retry_after_seconds=7200 if attempt == 3 else None,
        )
    assert recovery.retry_due(state, start) == due + timedelta(hours=2)


def test_success_clears_cycle_counters_and_checkpoint_metadata():
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    state = recovery.record_403_failure(
        {"last_completed_offset": 100}, source="pacheco", now=now
    )
    complete = recovery.clear_403_state(
        {**state, "status": "complete", "stop_reason": "facet_total_reconciled"}
    )
    assert complete["status"] == "complete"
    assert complete["stop_reason"] == "facet_total_reconciled"
    assert not any(key.startswith("http_403_") for key in complete)


def test_sources_keep_independent_persisted_recovery_state(monkeypatch):
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    araujo = recovery.record_403_failure(
        {"last_completed_page": 2}, source="araujo", now=now
    )
    pacheco = recovery.record_403_failure(
        {"last_completed_offset": 50}, source="pacheco", now=now
    )
    pacheco = recovery.record_403_failure(pacheco, source="pacheco", now=now)

    assert araujo["http_403_attempts"] == 1
    assert pacheco["http_403_attempts"] == 2
    assert araujo["http_403_origin"] != pacheco["http_403_origin"]
    assert araujo["http_403_last_valid_checkpoint"] == {"last_completed_page": 2}
    assert pacheco["http_403_last_valid_checkpoint"] == {"last_completed_offset": 50}


def test_cycle_limit_and_manual_pause_are_operational_controls(monkeypatch):
    monkeypatch.setenv("PACHECO_403_MAX_CYCLES", "1")
    start = datetime(2026, 11, 20, 12, tzinfo=UTC)
    state = {}
    for attempt in range(3):
        state = recovery.record_403_failure(state, source="pacheco", now=start)
        due = recovery.retry_due(state, start)
        if due:
            state = recovery.begin_due_attempt(state, now=due)
            start = due

    assert state["http_403_failed_cycles"] == 1
    assert state["http_403_circuit_open"] is True
    assert recovery.retry_due(state, start) is None


@pytest.mark.asyncio
async def test_manual_pause_interrupts_cooldown_wait(monkeypatch):
    monkeypatch.setenv("ARAUJO_403_RECOVERY_ENABLED", "true")
    current = datetime(2026, 11, 20, 12, tzinfo=UTC)
    calls = 0

    async def simulated_sleep(seconds):
        nonlocal current, calls
        calls += 1
        current += timedelta(seconds=seconds)
        monkeypatch.setenv("ARAUJO_403_MANUAL_PAUSE", "true")

    completed = await recovery.wait_for_retry_time(
        "araujo",
        current + timedelta(hours=1),
        now=lambda: current,
        sleep=simulated_sleep,
    )

    assert completed is False
    assert calls == 1
