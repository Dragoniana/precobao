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
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after_seconds = retry_after_seconds
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
            if payload.get("status") not in {"COMPLETE", "PARTIAL"}:
                raise BatchSyncError("scraper_ingest_batch_not_complete")
            accepted = int(
                payload.get("accepted_count", payload.get("product_count", 0))
            )
            rejected = int(payload.get("rejected_count", 0))
            if (
                accepted < 0
                or rejected < 0
                or accepted + rejected
                != int(payload.get("product_count", accepted + rejected))
            ):
                raise BatchSyncError("scraper_ingest_partial_counts_invalid")
            return payload
        if response.status_code == 403:
            raise BatchSyncError("scraper_ingest_forbidden", status_code=403)
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_ingest_rate_limited:{retry_after}",
                    status_code=429,
                    retry_after_seconds=retry_after,
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
        error_code = None
        try:
            detail = response.json().get("detail")
            error_code = detail if isinstance(detail, str) else None
        except (ValueError, AttributeError):
            pass
        category = {
            400: "validation",
            401: "hmac_invalid",
            409: "identity_conflict"
            if error_code == "scraper_product_identity_conflict"
            else "batch_conflict",
            403: "access_denied",
            422: "validation",
        }.get(response.status_code, "http_rejected")
        raise BatchSyncError(
            f"scraper_ingest_{category}:{response.status_code}",
            status_code=response.status_code,
            error_code=error_code,
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


def _image_backfill_endpoint(route: str) -> str:
    if route not in {"backfill-inventory", "backfill-queue"}:
        raise BatchSyncError("scraper_image_backfill_route_invalid")
    parsed = urlsplit(_endpoint())
    path = parsed.path.rstrip("/")
    if path.endswith("/scraper-batches"):
        path = path[: -len("/scraper-batches")]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"{path}/medicine-images/{route}", "", "")
    )


async def _post_image_backfill_request(
    client: httpx.AsyncClient, route: str, body: bytes
) -> dict[str, Any]:
    endpoint = _image_backfill_endpoint(route)
    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise BatchSyncError("scraper_ingest_secret_not_configured")
    try:
        response = await client.post(
            endpoint, content=body, headers=_signed_headers(body, secret)
        )
    except httpx.TimeoutException as error:
        raise BatchSyncError("scraper_image_backfill_timeout") from error
    except httpx.HTTPError as error:
        raise BatchSyncError("scraper_image_backfill_transport_error") from error
    if response.status_code == 429:
        raise BatchSyncError(
            "scraper_image_backfill_rate_limited",
            status_code=429,
            retry_after_seconds=_retry_after_seconds(
                response.headers.get("Retry-After")
            ),
        )
    if response.status_code != 200:
        detail = None
        try:
            value = response.json().get("detail")
            detail = value if isinstance(value, str) else None
        except (ValueError, AttributeError):
            pass
        raise BatchSyncError(
            f"scraper_image_backfill_rejected:{response.status_code}",
            status_code=response.status_code,
            error_code=detail,
        )
    try:
        value = response.json()
    except ValueError as error:
        raise BatchSyncError("scraper_image_backfill_invalid_response") from error
    if not isinstance(value, dict):
        raise BatchSyncError("scraper_image_backfill_invalid_response")
    return value


async def fetch_image_backfill_inventory(
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    return await _post_image_backfill_request(client, "backfill-inventory", b"{}")


async def fetch_image_backfill_page(
    client: httpx.AsyncClient,
    *,
    after_catalog_id: int,
    limit: int = 50,
    source: str | None = None,
) -> dict[str, Any]:
    if after_catalog_id < 0 or limit < 1 or limit > 100:
        raise BatchSyncError("scraper_image_backfill_page_invalid")
    if source not in {None, "araujo", "pacheco"}:
        raise BatchSyncError("scraper_image_backfill_source_invalid")
    payload: dict[str, Any] = {"after_catalog_id": after_catalog_id, "limit": limit}
    if source:
        payload["source"] = source
    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return await _post_image_backfill_request(client, "backfill-queue", body)


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
            status = payload.get("status")
            if status == "STORED":
                if payload.get("sha256") != metadata.get("content_sha256"):
                    raise BatchSyncError("scraper_image_upload_hash_unconfirmed")
                if (
                    payload.get("catalog_id") is None
                    and payload.get("offer_id") is None
                ):
                    raise BatchSyncError("scraper_image_upload_link_unconfirmed")
                return payload
            if status == "SKIPPED_EXISTING_IMAGE":
                if payload.get("confirmed_existing_image") is not True or (
                    payload.get("catalog_id") is None
                    and payload.get("offer_id") is None
                ):
                    raise BatchSyncError("scraper_image_existing_link_unconfirmed")
                return payload
            raise BatchSyncError("scraper_image_upload_not_confirmed")
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if attempt >= max_retries:
                raise BatchSyncError(
                    f"scraper_image_upload_rate_limited:{retry_after}",
                    status_code=429,
                    retry_after_seconds=retry_after,
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
