"""Load explicitly authorized pharmacy feeds without writing partial imports."""

from __future__ import annotations

import asyncio
import csv
import email.utils
import io
import json
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.schemas.oferta import ProdutoExtraidoSchema


class PartnerFeedError(RuntimeError):
    """A partner feed is missing, inaccessible, or invalid."""


class PartnerFeedForbidden(PartnerFeedError):
    """The partner explicitly refused the configured feed request."""


class PartnerFeedRateLimited(PartnerFeedError):
    """The partner feed rate-limited the request."""

    def __init__(self, retry_after_seconds: int | None = None):
        super().__init__("Partner feed returned HTTP 429; no retry was attempted.")
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = int((retry_at - datetime.now(UTC)).total_seconds())
    return max(0, seconds)


def _value(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _valid_gtin(value: str) -> bool:
    if len(value) not in {8, 12, 13, 14} or not value.isdigit():
        return False
    body, check_digit = value[:-1], int(value[-1])
    weighted_sum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    return (10 - weighted_sum % 10) % 10 == check_digit


def normalize_partner_record(
    record: dict[str, Any], *, sku_prefix: str
) -> dict[str, Any]:
    """Map common partner feed columns to the application's validated contract."""
    currency = _value(record, "currency", "moeda")
    if currency is None:
        raise PartnerFeedError("Each product must explicitly declare its currency.")
    if str(currency).strip().upper() != "BRL":
        raise PartnerFeedError(
            "Partner feed prices must be explicitly denominated in BRL."
        )

    source_id = _value(record, "id", "product_id", "productId", "sku_id", "sku")
    source_sku = _value(record, "sku", "sku_interno", "skuId", "itemId", "id")
    if source_id is None or source_sku is None:
        raise PartnerFeedError("Each product must include a stable product ID and SKU.")
    available = _value(record, "available", "disponivel", "isAvailable", "IsAvailable")
    quantity = _value(
        record, "quantity", "quantidade_estoque", "stock", "AvailableQuantity"
    )
    if available is None or quantity is None:
        raise PartnerFeedError(
            "Each product must include explicit availability and stock."
        )

    if isinstance(available, str):
        normalized = available.strip().casefold()
        if normalized not in {
            "true",
            "false",
            "1",
            "0",
            "yes",
            "no",
            "sim",
            "nao",
            "não",
        }:
            raise PartnerFeedError(
                "Partner feed contains an invalid availability value."
            )
        available = normalized in {"true", "1", "yes", "sim"}
    elif not isinstance(available, bool):
        raise PartnerFeedError("Partner feed availability must be a boolean.")

    try:
        quantity_decimal = Decimal(str(quantity))
        if (
            not quantity_decimal.is_finite()
            or quantity_decimal != quantity_decimal.to_integral_value()
        ):
            raise ValueError
        quantity = int(quantity_decimal)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PartnerFeedError(
            "Partner feed stock must be a non-negative integer."
        ) from error

    raw_sku = str(source_sku).strip()
    sku = f"{sku_prefix}{raw_sku[len(sku_prefix):]}" if raw_sku.upper().startswith(
        sku_prefix.upper()
    ) else f"{sku_prefix}{raw_sku}"
    product_url = _value(record, "url", "link", "product_url", "productUrl")
    image_url = _value(record, "image_url", "imagem_url", "imageUrl")
    try:
        parsed_product_url = urlsplit(str(product_url or ""))
    except ValueError as error:
        raise PartnerFeedError(
            "Every product must include its official HTTPS URL."
        ) from error
    if (
        parsed_product_url.scheme != "https"
        or not parsed_product_url.hostname
        or parsed_product_url.username
        or parsed_product_url.password
    ):
        raise PartnerFeedError("Every product must include its official HTTPS URL.")
    if image_url:
        try:
            parsed_image_url = urlsplit(str(image_url))
        except ValueError as error:
            raise PartnerFeedError(
                "Product image URLs must use public HTTPS."
            ) from error
        if (
            parsed_image_url.scheme != "https"
            or not parsed_image_url.hostname
            or parsed_image_url.username
            or parsed_image_url.password
        ):
            raise PartnerFeedError("Product image URLs must use public HTTPS.")
    observed_value = _value(
        record, "price_observed_at", "observed_at", "fonte_observado_em"
    )
    if observed_value is None:
        raise PartnerFeedError(
            "Each product must include the source price observation time."
        )
    try:
        observed_at = (
            observed_value
            if isinstance(observed_value, datetime)
            else datetime.fromisoformat(str(observed_value).replace("Z", "+00:00"))
        )
    except ValueError as error:
        raise PartnerFeedError(
            "Partner feed observation time must be ISO 8601."
        ) from error
    if observed_at.tzinfo is None:
        raise PartnerFeedError("Partner feed observation time must include a timezone.")
    max_age_hours = float(os.getenv("PARTNER_FEED_MAX_AGE_HOURS", "24"))
    if max_age_hours <= 0:
        raise PartnerFeedError("PARTNER_FEED_MAX_AGE_HOURS must be positive.")
    age_seconds = (datetime.now(UTC) - observed_at.astimezone(UTC)).total_seconds()
    if age_seconds < -300 or age_seconds > max_age_hours * 3600:
        raise PartnerFeedError(
            "Partner feed price observation is outside the freshness window."
        )
    ean = _value(record, "ean", "gtin", "barcode")
    if ean is not None:
        ean = str(ean).strip().replace("-", "").replace(" ", "")
        if not _valid_gtin(ean):
            raise PartnerFeedError("Partner feed contains an invalid EAN/GTIN.")
    product = {
        "id": str(source_id or raw_sku),
        "sku_interno": sku,
        "ean": ean,
        "name_search": _value(
            record, "name", "name_search", "product_name", "productName", "nome"
        ),
        "preco": _value(record, "price", "preco", "sellingPrice", "Price"),
        "disponivel": available,
        "quantidade_estoque": quantity,
        "link": product_url,
        "imagem_url": image_url,
        "fonte_observado_em": observed_at,
    }
    try:
        return ProdutoExtraidoSchema.model_validate(product).model_dump()
    except ValidationError as error:
        raise PartnerFeedError(
            "Partner feed contains an invalid product record."
        ) from error


def parse_partner_feed(
    payload: str, feed_format: str, *, sku_prefix: str
) -> list[dict[str, Any]]:
    """Parse and validate the entire CSV/JSON document before returning any rows."""
    feed_format = feed_format.casefold().strip()
    try:
        if feed_format == "csv":
            records = list(csv.DictReader(io.StringIO(payload, newline="")))
        elif feed_format == "json":
            parsed = json.loads(payload)
            records = parsed if isinstance(parsed, list) else None
            if records is None and isinstance(parsed, dict):
                for key in ("products", "items", "records", "data"):
                    candidate = parsed.get(key)
                    if isinstance(candidate, list):
                        records = candidate
                        break
            if records is None:
                raise PartnerFeedError("JSON feed must contain a product array.")
        else:
            raise PartnerFeedError("Feed format must be either csv or json.")
    except (csv.Error, json.JSONDecodeError) as error:
        raise PartnerFeedError("Partner feed could not be parsed.") from error

    if not records:
        raise PartnerFeedError("Partner feed contains no products.")
    if not all(isinstance(record, dict) for record in records):
        raise PartnerFeedError(
            "Every partner feed product must be an object or CSV row."
        )

    # Validate the complete payload before a scraper queues the first database write.
    return [
        normalize_partner_record(record, sku_prefix=sku_prefix) for record in records
    ]


async def load_authorized_partner_feed(
    *, prefix: str, sku_prefix: str
) -> list[dict[str, Any]] | None:
    """Read one configured file or HTTPS partner API; return None if unconfigured."""
    file_value = os.getenv(f"{prefix}_FILE", "").strip()
    url = os.getenv(f"{prefix}_URL", "").strip()
    if not file_value and not url:
        return None
    if file_value and url:
        raise PartnerFeedError("Configure either a partner feed file or URL, not both.")

    feed_format = os.getenv(f"{prefix}_FORMAT", "").strip().casefold()
    if not feed_format:
        feed_format = (
            Path(file_value or urlsplit(url).path).suffix.lstrip(".").casefold()
        )
    if feed_format not in {"csv", "json"}:
        raise PartnerFeedError("Set the partner feed format to csv or json.")

    if file_value:
        try:
            payload = await asyncio.to_thread(
                Path(file_value).read_text, encoding="utf-8-sig"
            )
        except OSError as error:
            raise PartnerFeedError(
                "Configured partner feed file is unavailable."
            ) from error
        return parse_partner_feed(payload, feed_format, sku_prefix=sku_prefix)

    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise PartnerFeedError("Partner feed APIs must use HTTPS.")
    if parsed_url.username or parsed_url.password:
        raise PartnerFeedError("Do not put partner API credentials in the feed URL.")

    token = os.getenv(f"{prefix}_TOKEN", "")
    username = os.getenv(f"{prefix}_USERNAME", "")
    password = os.getenv(f"{prefix}_PASSWORD", "")
    if token and (username or password):
        raise PartnerFeedError(
            "Configure either a bearer token or basic credentials, not both."
        )
    if bool(username) != bool(password):
        raise PartnerFeedError("Both partner API basic-auth values are required.")

    headers = {"Accept": "application/json" if feed_format == "json" else "text/csv"}
    auth = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif username:
        auth = httpx.BasicAuth(username, password)

    try:
        async with httpx.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=30.0,
            verify=True,
            follow_redirects=False,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as error:
        raise PartnerFeedError(
            "Partner feed request failed before receiving a response."
        ) from error

    if response.status_code == 403:
        raise PartnerFeedForbidden(
            "Partner feed returned HTTP 403; no retry was attempted."
        )
    if response.status_code == 429:
        raise PartnerFeedRateLimited(
            _retry_after_seconds(response.headers.get("Retry-After"))
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise PartnerFeedError(f"Partner feed returned HTTP {response.status_code}.")
    return parse_partner_feed(response.text, feed_format, sku_prefix=sku_prefix)
