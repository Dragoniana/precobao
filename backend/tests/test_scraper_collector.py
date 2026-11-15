from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.scrapers import scraper_batch_client as sync_client
from app.scrapers import scraper_collector, scraper_collector_scheduler


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
