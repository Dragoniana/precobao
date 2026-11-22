from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from scripts import image_backfill


@pytest.mark.asyncio
async def test_pacheco_partial_product_response_is_rejected(monkeypatch):
    class Client:
        async def get(self, _url):
            return httpx.Response(206, request=httpx.Request("GET", "https://test"))

    @asynccontextmanager
    async def open_client(_source, *, headers):
        yield Client()

    async def should_not_parse(*_args, **_kwargs):
        raise AssertionError("HTTP 206 must not be parsed as a complete product")

    monkeypatch.setattr(image_backfill, "open_source_http_client", open_client)
    monkeypatch.setattr(image_backfill, "parse_pacheco_image", should_not_parse)
    result = await image_backfill._fetch_source_image_url(
        "pacheco",
        {
            "sku": "PCH155349",
            "product_url": "https://www.drogariaspacheco.com.br/produto/155349",
            "ean": "7897705203095",
        },
    )
    assert result == (None, "SOURCE_HTTP_206")


@pytest.mark.asyncio
async def test_valid_blob_and_identity_conflict_do_not_trigger_source_requests(
    monkeypatch,
):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("Stored/conflicted records must not query a pharmacy")

    monkeypatch.setattr(image_backfill, "_try_source_offer", unexpected)
    valid = await image_backfill._process_entry(
        httpx.AsyncClient(),
        {"catalog_id": 42, "status": "BYTEA_REAL_VALID", "sources": []},
        source_filter="pacheco",
        include_generic=False,
        phase="known_references",
    )
    conflicted = await image_backfill._process_entry(
        httpx.AsyncClient(),
        {"catalog_id": 43, "status": "IDENTITY_CONFLICT", "sources": []},
        source_filter="pacheco",
        include_generic=False,
        phase="known_references",
    )
    assert valid["status"] == "BYTEA_REAL_VALID"
    assert conflicted["status"] == "IDENTITY_CONFLICT"


def test_execute_guard_rejects_disabled_source_tls(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS", "50")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS", "900")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_PER_SOURCE_MAX_REQUESTS", "25")
    monkeypatch.setenv("SCRAPER_EXTERNAL_RATE_LIMIT_IMAGE_BACKFILL_PER_SOURCE", "10")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", "a" * 40)
    monkeypatch.setenv("PACHECO_TLS_VERIFY", "false")
    with pytest.raises(RuntimeError, match="TLS_VERIFICATION_REQUIRED"):
        image_backfill._check_execution_budget()
