from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from app.scrapers import medicine_budget as budget


def configure(monkeypatch, tmp_path, **values):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_MEDICINE_BUDGET_MAX", str(values.get("limit", 50)))
    monkeypatch.setenv(
        "SCRAPER_MEDICINE_BUDGET_WINDOW_SECONDS", str(values.get("window", 900))
    )
    monkeypatch.setenv(
        "SCRAPER_MEDICINE_BUDGET_SOURCE_SHARE", str(values.get("share", 25))
    )


def test_unique_source_sku_consumes_one_unit_and_same_window_deduplicates(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    budget.mark_source_run("araujo", now=now)

    assert budget.reserve_product("araujo", "ARJ-1", now=now) == "RESERVED"
    assert budget.reserve_product("araujo", "ARJ-1", now=now) == "DUPLICATE"
    assert budget.medicine_budget_status(now=now)["total"] == 1


def test_shared_cap_and_source_share_allow_safe_reallocation_after_other_run(
    monkeypatch, tmp_path
):
    configure(monkeypatch, tmp_path)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    budget.mark_source_run("araujo", now=now)
    for index in range(10):
        assert budget.reserve_product("araujo", f"ARJ-{index}", now=now) == "RESERVED"
    budget.mark_source_run("pacheco", now=now + timedelta(minutes=7))
    for index in range(25):
        assert (
            budget.reserve_product(
                "pacheco", f"PCH-{index}", now=now + timedelta(minutes=7)
            )
            == "RESERVED"
        )
    # Pacheco can borrow Araújo's 15 unused units only after Araújo had a run.
    for index in range(25, 40):
        assert (
            budget.reserve_product(
                "pacheco", f"PCH-{index}", now=now + timedelta(minutes=7)
            )
            == "RESERVED"
        )
    assert budget.reserve_product("pacheco", "PCH-OVER", now=now) == "EXHAUSTED"
    status = budget.medicine_budget_status(now=now + timedelta(minutes=7))
    assert status["total"] == 50
    assert status["remaining"] == 0


def test_rolling_window_releases_only_expired_medicine_units(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, limit=4, share=2)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    budget.mark_source_run("araujo", now=now)
    budget.mark_source_run("pacheco", now=now)
    for index in range(2):
        assert budget.reserve_product("araujo", f"A-{index}", now=now) == "RESERVED"
        assert budget.reserve_product("pacheco", f"P-{index}", now=now) == "RESERVED"
    later = now + timedelta(seconds=901)
    assert budget.reserve_product("araujo", "A-NEW", now=later) == "RESERVED"
    assert budget.medicine_budget_status(now=later)["total"] == 1


def test_concurrent_sources_cannot_reserve_more_than_global_cap(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)

    def reserve(index):
        source = "araujo" if index % 2 == 0 else "pacheco"
        return budget.reserve_product(source, f"{source}-{index}", now=now)

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(reserve, range(100)))

    assert outcomes.count("RESERVED") == 50
    assert outcomes.count("EXHAUSTED") == 50
    assert budget.medicine_budget_status(now=now)["total"] == 50


def test_status_transition_does_not_release_reserved_unit(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, limit=2, share=1)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    budget.mark_source_run("araujo", now=now)
    budget.mark_source_run("pacheco", now=now)
    assert budget.reserve_product("araujo", "A-1", now=now) == "RESERVED"
    budget.update_product_status("araujo", "A-1", "DEFERRED_IMAGE", now=now)
    status = budget.medicine_budget_status(now=now)
    assert status["total"] == 1
    assert status["by_source"]["araujo"]["statuses"]["DEFERRED_IMAGE"] == 1
