from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.scrapers import source_rate_limiter as limiter
from app.scrapers.scraper_image_client import _download_image
from app.scrapers.source_http_client import RateLimitedSourceClient
from app.services.medicine_images import InvalidMedicineImage


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS", "50")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS", "900")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR", str(tmp_path))


def test_global_sliding_window_is_shared_and_survives_reload(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)

    for index in range(50):
        source = "araujo" if index % 2 == 0 else "pacheco"
        assert limiter._try_reserve_request(source, "www.example.com", now=now) is None

    wait = limiter._try_reserve_request("pacheco", "www.example.com", now=now)
    assert wait == pytest.approx(900)
    # A fresh process reads the same on-disk budget rather than resetting it.
    assert limiter._try_reserve_request(
        "araujo", "www.example.com", now=now + timedelta(seconds=899)
    ) == pytest.approx(1)
    assert (
        limiter._try_reserve_request(
            "araujo", "www.example.com", now=now + timedelta(seconds=900)
        )
        is None
    )


def test_concurrent_sources_cannot_reserve_the_51st_request(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    now = datetime.now(UTC)

    def reserve(index):
        source = "araujo" if index % 2 == 0 else "pacheco"
        return limiter._try_reserve_request(source, "shop.example", now=now)

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(reserve, range(100)))

    assert results.count(None) == 50
    assert sum(value is not None for value in results) == 50


def test_state_replace_retries_transient_windows_sharing_violation(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    real_replace = limiter.os.replace
    attempts = 0

    def transient_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporary file sharing conflict")
        real_replace(source, destination)

    monkeypatch.setattr(limiter.os, "replace", transient_replace)
    assert limiter._try_reserve_request("araujo", "shop.example") is None
    assert attempts == 3
    assert limiter.rate_budget_status()["total"] == 1


def test_429_retry_after_blocks_only_the_source_host(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "60"})
    limiter.note_source_response("araujo", "https://shop.example/search", response)

    wait = limiter._try_reserve_request("pacheco", "shop.example")
    assert wait is not None and 0 < wait <= 60
    assert limiter._try_reserve_request("pacheco", "other.example") is None


def test_429_without_retry_after_uses_a_conservative_cooldown(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    response = SimpleNamespace(status_code=429, headers={})
    limiter.note_source_response("araujo", "https://shop.example/search", response)

    wait = limiter._try_reserve_request("pacheco", "shop.example")
    assert wait is not None and 0 < wait <= 60


def test_403_applies_persistent_source_cooldown(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    response = SimpleNamespace(status_code=403, headers={})
    limiter.note_source_response("araujo", "https://shop.example/search", response)

    assert limiter.source_cooldown_active("araujo")
    wait = limiter._try_reserve_request("araujo", "other.example")
    assert wait is not None and wait >= 3599
    assert limiter._try_reserve_request("pacheco", "other.example") is None


@pytest.mark.asyncio
async def test_each_source_client_attempt_is_counted_but_api_client_is_not(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)

    class FakeClient:
        async def get(self, url, **_kwargs):
            return SimpleNamespace(status_code=503, headers={})

        async def post(self, *_args, **_kwargs):
            return SimpleNamespace(status_code=200, headers={})

        async def head(self, url, **_kwargs):
            return SimpleNamespace(status_code=200, headers={})

    source_client = RateLimitedSourceClient(FakeClient(), "araujo")
    await source_client.get("https://shop.example/catalog")
    await source_client.get("https://shop.example/catalog")
    await source_client.head("https://shop.example/catalog")
    # API ingestion uses its separate plain HTTP client, outside the pharmacy budget.
    await FakeClient().post("https://precobao.app/backend/ingest")

    state = limiter.rate_budget_status()
    assert state["total"] == 3
    assert state["by_source"] == {"araujo": 3, "pacheco": 0}


@pytest.mark.asyncio
async def test_image_download_consumes_the_shared_external_budget(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)

    class Response:
        status_code = 404
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(InvalidMedicineImage, match="image_source_http_404"):
        await _download_image(
            FakeClient(),
            "https://www.araujo.com.br/images/medicine.jpg",
            "araujo",
        )

    assert limiter.rate_budget_status()["by_source"]["araujo"] == 1


@pytest.mark.asyncio
async def test_scheduled_budget_exhaustion_returns_immediately(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_WAIT_MODE", "stop")
    now = datetime.now(UTC)
    for index in range(50):
        source = "araujo" if index % 2 else "pacheco"
        assert limiter._try_reserve_request(source, "shop.example", now=now) is None

    async def no_sleep(_seconds):
        raise AssertionError("scheduled collection must not wait for a new window")

    monkeypatch.setattr(limiter.asyncio, "sleep", no_sleep)
    with pytest.raises(limiter.ExternalRequestDeferred) as error:
        await limiter.acquire_external_request("araujo", "https://shop.example/page")

    assert error.value.reason == "BUDGET_EXHAUSTED"
    assert limiter.rate_budget_status(now=now)["total"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected"), [(403, "ACCESS_BLOCKED"), (429, "RATE_LIMITED")]
)
async def test_scheduled_cooldowns_keep_source_error_distinct(
    monkeypatch, tmp_path, status_code, expected
):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_WAIT_MODE", "stop")
    response = SimpleNamespace(status_code=status_code, headers={})
    limiter.note_source_response("araujo", "https://shop.example/page", response)

    with pytest.raises(limiter.ExternalRequestDeferred) as error:
        await limiter.acquire_external_request("araujo", "https://shop.example/page")

    assert error.value.reason == expected
    assert limiter.rate_budget_status()["total"] == 0
