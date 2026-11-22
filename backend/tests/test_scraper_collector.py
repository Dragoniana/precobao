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


@pytest.mark.parametrize(
    ("scheduled", "limit", "expected", "message"),
    [
        ("true", "", None, "SCHEDULED_PRODUCT_LIMIT_REQUIRED"),
        ("true", "0", None, "MAX_PRODUCTS_PER_RUN_INVALID"),
        ("true", "51", None, "MAX_PRODUCTS_PER_RUN_INVALID"),
        ("true", "50", 50, None),
        ("", "50", 50, None),
    ],
)
def test_scheduled_product_limit_fails_closed(
    monkeypatch, scheduled, limit, expected, message
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_SCHEDULED", scheduled)
    monkeypatch.setenv("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN", limit)
    if message:
        with pytest.raises(RuntimeError, match=message):
            scraper_collector._configured_product_limit()
    else:
        assert scraper_collector._configured_product_limit() == expected


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (401, "scraper_ingest_hmac_invalid:401", ("SYNC_AUTH_FAILED", "hmac_invalid")),
        (403, "scraper_ingest_forbidden", ("SYNC_BLOCKED", "access_denied")),
        (409, "scraper_ingest_batch_conflict:409", ("SYNC_CONFLICT", "batch_conflict")),
        (429, "scraper_ingest_rate_limited", ("RATE_LIMITED", "rate_limited")),
        (503, "scraper_ingest_server_error:503", ("SYNC_UNAVAILABLE", "server_error")),
        (None, "scraper_ingest_timeout", ("SYNC_FAILED", "timeout")),
        (None, "scraper_ingest_transport_error", ("SYNC_FAILED", "connection_error")),
    ],
)
def test_sync_http_failures_have_distinct_categories(status, message, expected):
    error = sync_client.BatchSyncError(message, status_code=status)
    assert scraper_collector._sync_failure_state(error) == expected


def test_rejected_item_queue_is_persistent_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    batch = {
        "batch_id": "batch-one",
        "products": [
            {
                "sku_interno": "PCH155349",
                "ean": "7891721027468",
                "preco": 9.4,
                "link": "https://www.drogariaspacheco.com.br/produto/155349/p",
            }
        ],
    }
    rejected = [
        {
            "product_id": "155349",
            "sku_interno": "PCH155349",
            "ean": "7891721027468",
            "link": batch["products"][0]["link"],
            "reason": "ean_catalog_mismatch",
        }
    ]
    scraper_collector._persist_rejected_products("pacheco", batch, rejected)
    scraper_collector._persist_rejected_products("pacheco", batch, rejected)
    repeated_batch = {**batch, "batch_id": "batch-two"}
    scraper_collector._persist_rejected_products("pacheco", repeated_batch, rejected)
    path = tmp_path / "rejected" / "pacheco.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["batch_id"] == "batch-one"
    assert stored[0]["product"]["preco"] == 9.4
    assert stored[0]["reason"] == "ean_catalog_mismatch"


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
async def test_batch_client_classifies_409_without_retry(monkeypatch):
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )

    class Response:
        status_code = 409
        headers = {}

        @staticmethod
        def json():
            return {"detail": "batch_id_payload_conflict"}

    class Client:
        calls = 0

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = Client()
    with pytest.raises(sync_client.BatchSyncError) as captured:
        await sync_client.post_batch(client, {"batch_id": "conflict"})
    assert client.calls == 1
    assert captured.value.status_code == 409
    assert captured.value.error_code == "batch_id_payload_conflict"
    assert scraper_collector._sync_failure_state(captured.value) == (
        "SYNC_CONFLICT",
        "batch_conflict",
    )


@pytest.mark.asyncio
async def test_batch_client_accepts_partial_response_with_consistent_counts(
    monkeypatch,
):
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "status": "PARTIAL",
                "product_count": 2,
                "accepted_count": 1,
                "rejected_count": 1,
                "rejected_items": [
                    {"sku_interno": "PCH1", "reason": "ean_catalog_mismatch"}
                ],
            }

    class Client:
        async def post(self, *_args, **_kwargs):
            return Response()

    result = await sync_client.post_batch(Client(), {"batch_id": "partial"})
    assert result["status"] == "PARTIAL"
    assert result["accepted_count"] == 1


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


@pytest.mark.asyncio
async def test_image_failures_do_not_interrupt_confirmed_price_batches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("SCRAPER_COLLECTION_ENV_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("SCRAPER_INGEST_URL", "https://api.example.test/ingest")
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "collector-secret-which-is-longer-than-32"
    )

    pending_dir = tmp_path / "outbox" / "images" / "araujo"
    pending_dir.mkdir(parents=True)
    pending_manifest = pending_dir / "pending.json"
    pending_blob = pending_dir / "pending.bin"
    pending_manifest.write_text(
        json.dumps({"sku": "ARJ-PENDING", "upload_id": "pending-id"}),
        encoding="utf-8",
    )
    pending_blob.write_bytes(b"image bytes remain queued")

    posted = []

    async def post_batch(_client, payload):
        posted.append(payload)
        return {"status": "COMPLETE", "offers_updated": len(payload["products"])}

    async def replay_fails(_client, _source):
        raise sync_client.BatchSyncError("scraper_image_upload_server_error:503")

    async def image_upload(_client, _source, product):
        if product["sku_interno"] == "ARJ-FAIL":
            raise sync_client.BatchSyncError("scraper_image_upload_server_error:503")
        return {
            "status": "STORED",
            "is_generic_fallback": True,
            "normalized_size_bytes": 42,
        }

    class FakeAraujoScraper:
        products_queued = 2
        allow_page_limit = False
        stats = {"complete": True}

        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def _run_lock():
            return nullcontext()

        async def _extract_direct_catalog(self):
            await self.products_callback(
                [
                    {
                        "sku_interno": "ARJ-FAIL",
                        "imagem_url": "https://images.example.test/fail.webp",
                    },
                    {
                        "sku_interno": "ARJ-OK",
                        "imagem_url": "https://images.example.test/ok.webp",
                    },
                ]
            )

    monkeypatch.setattr(scraper_collector, "post_batch", post_batch)
    monkeypatch.setattr(scraper_collector, "sync_pending_image_outbox", replay_fails)
    monkeypatch.setattr(
        scraper_collector, "download_and_upload_product_image", image_upload
    )
    monkeypatch.setattr("app.scrapers.araujo.AraujoScraper", FakeAraujoScraper)

    result = await scraper_collector.collect_source("araujo")
    state = scraper_collector._read_state("araujo")

    assert result["status"] == "COMPLETE"
    assert result["products_sent"] == 2
    assert len(posted) == 1
    assert state["image_sync_status"] == "PARTIAL"
    assert state["image_upload_failures"] == 1
    assert state["pending_image_replay_failures"] == 1
    assert {failure["sku"] for failure in state["image_failures"]} == {
        "ARJ-FAIL",
        "ARJ-PENDING",
    }
    assert pending_manifest.exists()
    assert pending_blob.read_bytes() == b"image bytes remain queued"
    assert not scraper_collector._outbox_path("araujo").exists()
