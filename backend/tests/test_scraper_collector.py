from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import nullcontext

import pytest

from app.scrapers import scraper_batch_client as sync_client
from app.scrapers import scraper_collector, scraper_collector_scheduler


def test_pilot_item_cap_limits_batches_across_source_pages():
    page_one = [{"sku": f"ARJ{i}"} for i in range(8)]
    page_two = [{"sku": f"ARJ{i}"} for i in range(8, 16)]

    first_selection = scraper_collector._select_page_products(page_one, 0, 10)
    second_selection = scraper_collector._select_page_products(
        page_two, len(first_selection), 10
    )

    assert len(first_selection) == 8
    assert [item["sku"] for item in second_selection] == ["ARJ8", "ARJ9"]
    assert scraper_collector._select_page_products(page_two, 10, 10) == []
    assert scraper_collector._select_page_products(page_two, 0, None) == page_two


@pytest.mark.asyncio
async def test_batch_client_uses_https_and_signed_body(monkeypatch):
    monkeypatch.setenv(
        "SCRAPER_INGEST_URL",
        "https://precobao.app/backend/api/v1/internal/scraper-batches",
    )
    secret = "collector-shared-secret-long-enough-for-hmac-signing"
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", secret)
    batch = {"batch_id": "test-id", "source": "araujo", "products": []}

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"status": "COMPLETE"}

    class Client:
        calls = 0

        async def post(self, url, *, content, headers):
            self.calls += 1
            assert url.startswith("https://")
            timestamp = headers["X-Scraper-Timestamp"]
            signature = hmac.new(
                secret.encode(), timestamp.encode() + b"." + content, hashlib.sha256
            ).hexdigest()
            assert headers["X-Scraper-Signature"] == f"sha256={signature}"
            assert json.loads(content) == batch
            return Response()

    client = Client()
    result = await sync_client.post_batch(client, batch)

    assert result["status"] == "COMPLETE"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_batch_client_does_not_retry_403(monkeypatch):
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )

    class Response:
        status_code = 403
        headers = {}

    class Client:
        calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = Client()
    with pytest.raises(sync_client.BatchSyncError, match="forbidden"):
        await sync_client.post_batch(client, {"batch_id": "blocked"})
    assert client.calls == 1


@pytest.mark.asyncio
async def test_batch_client_retries_limited_5xx_with_backoff(monkeypatch):
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )
    delays = []

    async def record_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(sync_client, "_sleep", record_sleep)

    class Response:
        status_code = 503
        headers = {}

    class Client:
        calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = Client()
    with pytest.raises(sync_client.BatchSyncError, match="server_error:503"):
        await sync_client.post_batch(client, {"batch_id": "unavailable"}, max_retries=2)
    assert client.calls == 3
    assert delays == [1.0, 2.0]


def test_batch_client_rejects_non_https_endpoint(monkeypatch):
    monkeypatch.setenv("SCRAPER_INGEST_URL", "http://api.example.test/ingest")
    with pytest.raises(sync_client.BatchSyncError, match="must_be_https"):
        sync_client._endpoint()


def test_collector_requires_environment_and_blocks_after_source_403(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("SCRAPER_COLLECTION_ENV_AUTHORIZED", "false")
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )

    with pytest.raises(RuntimeError, match="ENVIRONMENT_NOT_CONFIRMED"):
        scraper_collector._require_direct_collection("araujo")

    monkeypatch.setenv("SCRAPER_COLLECTION_ENV_AUTHORIZED", "true")
    scraper_collector._write_state(
        "araujo", {"source": "araujo", "status": "ACCESS_BLOCKED"}
    )
    with pytest.raises(RuntimeError, match="MANUAL_RESET_REQUIRED"):
        scraper_collector._require_direct_collection("araujo")


def test_collector_scheduler_uses_brasilia_slots(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setenv("SCRAPER_COLLECTOR_TIMEZONE", "America/Sao_Paulo")
    slots = scraper_collector_scheduler._parse_slots()

    morning = scraper_collector_scheduler._next_slot(
        datetime(2026, 11, 15, 9, 0, tzinfo=UTC), slots
    )
    evening = scraper_collector_scheduler._next_slot(
        datetime(2026, 11, 15, 12, 0, tzinfo=UTC), slots
    )

    assert morning == datetime(2026, 11, 15, 11, 0, tzinfo=UTC)
    assert evening == datetime(2026, 11, 15, 23, 0, tzinfo=UTC)


def test_corrupt_collector_checkpoint_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    path = tmp_path / "araujo.json"
    path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="CHECKPOINT_INVALID"):
        scraper_collector._read_state("araujo")


def test_collector_safe_stop_marker_is_observed(tmp_path, monkeypatch):
    marker = tmp_path / "stop.requested"
    monkeypatch.setenv("SCRAPER_COLLECTOR_STOP_FILE", str(marker))
    assert scraper_collector._stop_requested() is False
    marker.write_text("stop after current synced page", encoding="utf-8")
    assert scraper_collector._stop_requested() is True


@pytest.mark.asyncio
async def test_collector_replays_pending_outbox_before_source_collection(
    tmp_path, monkeypatch
):
    secret = "collector-outbox-recovery-secret-over-32-bytes"
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("SCRAPER_COLLECTION_ENV_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", secret)
    pending = {
        "batch_id": "f242bf8f-06a2-4d4b-8f12-80af0ff90650",
        "source": "araujo",
        "observed_at": "2026-11-15T12:00:00+00:00",
        "products": [{"id": "42"}],
    }
    scraper_collector._write_outbox("araujo", pending)
    posted = []
    source_calls = []

    async def fake_post(_client, payload):
        posted.append(payload)
        return {"status": "COMPLETE", "product_count": len(payload["products"])}

    class FakeAraujoScraper:
        products_queued = 0
        allow_page_limit = False

        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def _run_lock():
            return nullcontext()

        async def _extract_direct_catalog(self):
            source_calls.append("called")

    monkeypatch.setattr(scraper_collector, "post_batch", fake_post)
    monkeypatch.setattr("app.scrapers.araujo.AraujoScraper", FakeAraujoScraper)

    result = await scraper_collector.collect_source("araujo")

    assert result["status"] == "COMPLETE"
    assert result["products_sent"] == 1
    assert posted == [pending]
    assert source_calls == ["called"]
    assert not scraper_collector._outbox_path("araujo").exists()
