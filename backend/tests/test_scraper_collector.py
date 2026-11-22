from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import nullcontext

import pytest

from app.scrapers import scraper_batch_client as sync_client
from app.scrapers import scraper_collector, scraper_collector_scheduler
from app.scrapers.source_rate_limiter import ExternalRequestDeferred


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
        ("true", "", None, None),
        ("true", "0", None, "MAX_PRODUCTS_PER_RUN_INVALID"),
        ("true", "51", None, "MAX_PRODUCTS_PER_RUN_INVALID"),
        ("true", "50", 50, None),
        ("", "", None, None),
        ("", "50", 50, None),
    ],
)
def test_scheduled_product_limit_fails_closed(
    monkeypatch, scheduled, limit, expected, message
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_SCHEDULED", scheduled)
    monkeypatch.setenv("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN", limit)
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "true")
    if message:
        with pytest.raises(RuntimeError, match=message):
            scraper_collector._configured_product_limit()
    else:
        assert scraper_collector._configured_product_limit() == expected


def test_scheduled_collector_requires_request_budget_not_product_cap(monkeypatch):
    monkeypatch.setenv("SCRAPER_COLLECTOR_SCHEDULED", "true")
    monkeypatch.delenv("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN", raising=False)
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "false")
    with pytest.raises(RuntimeError, match="SCHEDULED_SOURCE_RATE_LIMIT_REQUIRED"):
        scraper_collector._configured_product_limit()


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


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        ("COMPLETE", 0),
        ("ACCESS_BLOCKED", 2),
        ("PARTIAL", 3),
        ("STOPPED", 4),
        ("RATE_LIMITED", 5),
        ("SYNC_FAILED", 6),
        ("FAILED", 7),
        ("ALREADY_RUNNING", 9),
        ("BUDGET_EXHAUSTED", 12),
        ("CONFIGURED_PRODUCT_LIMIT", 13),
    ],
)
def test_collector_exit_codes_are_explicit(status, exit_code):
    assert scraper_collector._exit_code_for_status(status) == exit_code


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


@pytest.mark.asyncio
async def test_budget_exhaustion_confirms_batch_and_queues_remaining_images(
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
    monkeypatch.setenv("SCRAPER_COLLECTOR_SCHEDULED", "true")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "true")
    scraper_collector._write_state(
        "araujo",
        {
            "status": "RUNNING",
            "started_at": "2026-11-22T00:00:00+00:00",
            "run_id": "prior-run",
        },
    )

    posted = []

    async def post_batch(_client, payload):
        posted.append(payload)
        return {
            "status": "COMPLETE",
            "accepted_count": len(payload["products"]),
            "rejected_count": 0,
        }

    async def replay_ok(_client, _source):
        return 0

    async def image_budget_exhausted(_client, _source, _product):
        raise ExternalRequestDeferred("BUDGET_EXHAUSTED", 321.0)

    class FakeAraujoScraper:
        products_queued = 0
        allow_page_limit = False

        def __init__(self, **kwargs):
            self._run_lock = nullcontext
            self.stop_requested_callback = kwargs["stop_requested_callback"]

        async def _extract_direct_catalog(self):
            products = [
                {
                    "sku_interno": "ARJ1",
                    "ean": "7890000000000",
                    "link": "https://www.araujo.com.br/p/1",
                    "imagem_url": "https://images.example.test/1.jpg",
                    "preco": 10,
                },
                {
                    "sku_interno": "ARJ2",
                    "ean": "7890000000001",
                    "link": "https://www.araujo.com.br/p/2",
                    "imagem_url": "https://images.example.test/2.jpg",
                    "preco": 11,
                },
            ]
            self.products_queued = await self.products_callback(products)
            assert self.stop_requested_callback()

    monkeypatch.setattr(scraper_collector, "post_batch", post_batch)
    monkeypatch.setattr(scraper_collector, "sync_pending_image_outbox", replay_ok)
    monkeypatch.setattr(
        scraper_collector,
        "download_and_upload_product_image",
        image_budget_exhausted,
    )
    monkeypatch.setattr("app.scrapers.araujo.AraujoScraper", FakeAraujoScraper)

    result = await scraper_collector.collect_source("araujo")

    assert result["status"] == "BUDGET_EXHAUSTED"
    assert result["stop_reason"] == "budget_exhausted"
    assert result["previous_run_interrupted"] is True
    assert result["previous_run_id"] == "prior-run"
    assert len(posted) == 1
    assert result["last_batch_id"] == posted[0]["batch_id"]
    assert not scraper_collector._outbox_path("araujo").exists()
    manifests = list(
        scraper_collector._image_fetch_outbox_dir("araujo").glob("*.json")
    )
    assert len(manifests) == 2
    manifest_skus = {
        json.loads(path.read_text(encoding="utf-8"))["sku_interno"]
        for path in manifests
    }
    assert manifest_skus == {"ARJ1", "ARJ2"}
    with scraper_collector._exclusive_lock(tmp_path / "locks" / "araujo.lock"):
        pass


def test_deferred_image_queue_is_idempotent_and_does_not_store_product_payload(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    product = {
        "sku_interno": "PCH9",
        "ean": "7890000000000",
        "link": "https://www.drogariaspacheco.com.br/p/9",
        "imagem_url": "https://images.example.test/9.jpg",
        "nome": "Medicine",
        "preco": 12.5,
    }

    scraper_collector._queue_image_fetch("pacheco", product)
    scraper_collector._queue_image_fetch("pacheco", product)

    manifests = list(
        scraper_collector._image_fetch_outbox_dir("pacheco").glob("*.json")
    )
    saved = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert len(manifests) == 1
    assert saved["sku_interno"] == "PCH9"
    assert "preco" not in saved and "nome" not in saved


@pytest.mark.asyncio
async def test_confirmed_batch_outbox_replay_keeps_same_id_without_double_counting(
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
    scraper_collector._write_state(
        "araujo", {"status": "RUNNING", "last_batch_id": "stable-batch-id"}
    )
    payload = {
        "batch_id": "stable-batch-id",
        "source": "araujo",
        "products": [{"sku_interno": "ARJ1", "preco": 10}],
    }
    scraper_collector._write_outbox("araujo", payload)
    posted_ids = []

    async def post_batch(_client, batch):
        posted_ids.append(batch["batch_id"])
        return {"status": "COMPLETE", "accepted_count": 1, "rejected_count": 0}

    async def replay_ok(_client, _source):
        return 0

    class EmptyAraujoScraper:
        products_queued = 0
        allow_page_limit = False

        def __init__(self, **_kwargs):
            self._run_lock = nullcontext

        async def _extract_direct_catalog(self):
            return None

    monkeypatch.setattr(scraper_collector, "post_batch", post_batch)
    monkeypatch.setattr(scraper_collector, "sync_pending_image_outbox", replay_ok)
    monkeypatch.setattr("app.scrapers.araujo.AraujoScraper", EmptyAraujoScraper)

    result = await scraper_collector.collect_source("araujo")

    assert posted_ids == ["stable-batch-id"]
    assert result["last_batch_id"] == "stable-batch-id"
    assert result["products_sent"] == 0
    assert result["batches_sent"] == 0
    assert not scraper_collector._outbox_path("araujo").exists()


@pytest.mark.asyncio
async def test_deferred_image_queue_resumes_without_losing_manifest(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    product = {
        "sku_interno": "PCH9",
        "ean": "7890000000000",
        "link": "https://www.drogariaspacheco.com.br/p/9",
        "imagem_url": "https://images.example.test/9.jpg",
    }
    scraper_collector._queue_image_fetch("pacheco", product)
    state = {
        "image_blobs_uploaded": 0,
        "image_bytes_uploaded": 0,
        "images_skipped_generic": 0,
        "image_download_failures": 0,
        "image_upload_failures": 0,
        "pending_image_replay_failures": 0,
        "image_failures": [],
    }

    async def no_budget(_client, _source, _product):
        raise ExternalRequestDeferred("BUDGET_EXHAUSTED", 10)

    monkeypatch.setattr(
        scraper_collector, "download_and_upload_product_image", no_budget
    )
    assert (
        await scraper_collector._replay_image_fetch_outbox(
            object(), "pacheco", state
        )
        == "BUDGET_EXHAUSTED"
    )
    manifests = list(
        scraper_collector._image_fetch_outbox_dir("pacheco").glob("*.json")
    )
    assert len(manifests) == 1

    async def uploaded(_client, _source, _product):
        return {"normalized_size_bytes": 256}

    monkeypatch.setattr(
        scraper_collector, "download_and_upload_product_image", uploaded
    )
    assert (
        await scraper_collector._replay_image_fetch_outbox(
            object(), "pacheco", state
        )
        is None
    )
    assert list(
        scraper_collector._image_fetch_outbox_dir("pacheco").glob("*.json")
    ) == []
    assert state["image_blobs_uploaded"] == 1
    assert state["image_bytes_uploaded"] == 256
