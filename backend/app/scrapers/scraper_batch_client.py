"""HTTPS-only HMAC client for sending collected pages to the Preço Bão API."""

from __future__ import annotations

import email.utils
import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class BatchSyncError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0, int((retry_at - datetime.now(UTC)).total_seconds()))


def _endpoint() -> str:
    value = os.getenv("SCRAPER_INGEST_URL", "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise BatchSyncError("scraper_ingest_url_must_be_https")
    return value


def _signed_headers(
    body: bytes, secret: str, content_type: str = "application/json"
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": content_type,
        "X-Scraper-Timestamp": timestamp,
        "X-Scraper-Signature": f"sha256={signature}",
    }


async def post_batch(
    client: httpx.AsyncClient, batch: dict[str, Any], *, max_retries: int = 3
) -> dict[str, Any]:
    """Submit the same immutable batch ID across bounded transport retries."""
    endpoint = _endpoint()
    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise BatchSyncError("scraper_ingest_secret_not_configured")
    body = json.dumps(
        batch, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(body) > int(os.getenv("SCRAPER_INGEST_MAX_BODY_BYTES", "1000000")):
        raise BatchSyncError("scraper_batch_exceeds_configured_size")

    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            response = await client.post(
                endpoint,
                content=body,
                headers=_signed_headers(body, secret),
            )
        except httpx.TimeoutException as error:
            if attempt >= max_retries:
                raise BatchSyncError("scraper_ingest_timeout") from error
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        except httpx.HTTPError as error:
            if attempt >= max_retries:
                raise BatchSyncError("scraper_ingest_transport_error") from error
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue

        if response.status_code in {200, 201}:
            try:
                payload = response.json()
            except ValueError as error:
                raise BatchSyncError("scraper_ingest_invalid_response") from error
            if payload.get("status") != "COMPLETE":
                raise BatchSyncError("scraper_ingest_batch_not_complete")
            return payload
        if response.status_code == 403:
            raise BatchSyncError("scraper_ingest_forbidden", status_code=403)
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_ingest_rate_limited:{retry_after}", status_code=429
                )
            await _sleep(float(retry_after) if retry_after is not None else delay)
            delay = min(delay * 2, 8.0)
            continue
        if response.status_code >= 500:
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_ingest_server_error:{response.status_code}",
                    status_code=response.status_code,
                )
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        raise BatchSyncError(
            f"scraper_ingest_rejected:{response.status_code}",
            status_code=response.status_code,
        )
    raise BatchSyncError("scraper_ingest_retry_exhausted")


def _image_endpoint() -> str:
    configured = os.getenv("SCRAPER_IMAGE_INGEST_URL", "").strip()
    if configured:
        parsed = urlsplit(configured)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise BatchSyncError("scraper_image_ingest_url_must_be_https")
        return configured
    parsed = urlsplit(_endpoint())
    path = parsed.path.rstrip("/")
    if path.endswith("/scraper-batches"):
        path = path[: -len("/scraper-batches")]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/medicine-images", "", ""))


async def post_image_upload(
    client: httpx.AsyncClient,
    metadata: dict[str, Any],
    image_bytes: bytes,
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Send an immutable binary image envelope using the same bounded HMAC channel."""
    endpoint = _image_endpoint()
    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise BatchSyncError("scraper_ingest_secret_not_configured")
    metadata_bytes = json.dumps(
        metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(metadata_bytes) > 16 * 1024:
        raise BatchSyncError("scraper_image_metadata_too_large")
    if not image_bytes or len(image_bytes) > int(
        os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))
    ):
        raise BatchSyncError("scraper_image_size_invalid")
    body = metadata_bytes + b"\n" + image_bytes
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            response = await client.post(
                endpoint,
                content=body,
                headers=_signed_headers(
                    body, secret, "application/vnd.preco-bao.image-upload"
                ),
            )
        except httpx.TimeoutException as error:
            if attempt >= max_retries:
                raise BatchSyncError("scraper_image_upload_timeout") from error
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        except httpx.HTTPError as error:
            if attempt >= max_retries:
                raise BatchSyncError("scraper_image_upload_transport_error") from error
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue

        if response.status_code in {200, 201}:
            try:
                payload = response.json()
            except ValueError as error:
                raise BatchSyncError("scraper_image_upload_invalid_response") from error
            if payload.get("status") != "STORED":
                raise BatchSyncError("scraper_image_upload_not_confirmed")
            return payload
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_image_upload_rate_limited:{retry_after}", status_code=429
                )
            await _sleep(float(retry_after) if retry_after is not None else delay)
            delay = min(delay * 2, 8.0)
            continue
        if response.status_code >= 500:
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_image_upload_server_error:{response.status_code}",
                    status_code=response.status_code,
                )
            await _sleep(delay)
            delay = min(delay * 2, 8.0)
            continue
        raise BatchSyncError(
            f"scraper_image_upload_rejected:{response.status_code}",
            status_code=response.status_code,
        )
    raise BatchSyncError("scraper_image_upload_retry_exhausted")


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
