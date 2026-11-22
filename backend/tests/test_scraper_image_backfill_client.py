from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.scrapers import scraper_batch_client


@pytest.mark.asyncio
async def test_backfill_requests_use_hmac_and_resolve_internal_api_paths(monkeypatch):
    secret = "backfill-test-secret-with-more-than-32-bytes"
    monkeypatch.setenv(
        "SCRAPER_INGEST_URL",
        "https://precobao.app/backend/api/v1/internal/scraper-batches",
    )
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", secret)
    captured = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        timestamp = request.headers["X-Scraper-Timestamp"]
        signature = request.headers["X-Scraper-Signature"].removeprefix("sha256=")
        expected = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(signature, expected)
        captured.append((request.url.path, body))
        return httpx.Response(200, json={"items": [], "has_more": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await scraper_batch_client.fetch_image_backfill_inventory(client)
        await scraper_batch_client.fetch_image_backfill_page(
            client, after_catalog_id=1200, limit=25, source="pacheco"
        )

    assert captured[0] == (
        "/backend/api/v1/internal/medicine-images/backfill-inventory",
        b"{}",
    )
    assert captured[1][0] == "/backend/api/v1/internal/medicine-images/backfill-queue"
    assert json.loads(captured[1][1]) == {
        "after_catalog_id": 1200,
        "limit": 25,
        "source": "pacheco",
        "phase": "known_references",
    }


@pytest.mark.asyncio
async def test_backfill_request_stops_on_unauthorized_response(monkeypatch):
    monkeypatch.setenv(
        "SCRAPER_INGEST_URL",
        "https://precobao.app/backend/api/v1/internal/scraper-batches",
    )
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "backfill-test-secret-with-more-than-32-bytes"
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid_scraper_signature"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(scraper_batch_client.BatchSyncError) as error:
            await scraper_batch_client.fetch_image_backfill_inventory(client)

    assert error.value.status_code == 401
    assert error.value.error_code == "invalid_scraper_signature"
