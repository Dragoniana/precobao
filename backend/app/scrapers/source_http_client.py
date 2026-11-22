"""Configurable HTTP clients scoped to public pharmacy scraper requests."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.scrapers.source_rate_limiter import (
    acquire_external_request,
    note_source_response,
)

logger = logging.getLogger(__name__)


class RateLimitedSourceClient:
    """Count attempts to pharmacy hosts while leaving Azure/API calls untouched."""

    def __init__(self, client: Any, source: str):
        self._client = client
        self._source = source

    async def request(self, method: str, url: str, *args, **kwargs):
        await acquire_external_request(self._source, str(url))
        response = await self._client.request(method, url, *args, **kwargs)
        note_source_response(self._source, str(url), response)
        return response

    async def get(self, url: str, *args, **kwargs):
        await acquire_external_request(self._source, str(url))
        response = await self._client.get(url, *args, **kwargs)
        note_source_response(self._source, str(url), response)
        return response

    @asynccontextmanager
    async def stream(self, method: str, url: str, *args, **kwargs):
        await acquire_external_request(self._source, str(url))
        async with self._client.stream(method, url, *args, **kwargs) as response:
            note_source_response(self._source, str(url), response)
            yield response

    def __getattr__(self, name: str):
        return getattr(self._client, name)


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class SourceHTTPConfig:
    pharmacy: str
    client: str
    impersonate: str
    verify: bool
    timeout: float


def source_http_config(pharmacy: str) -> SourceHTTPConfig:
    prefix = pharmacy.upper()
    client = os.getenv(f"{prefix}_HTTP_CLIENT", "httpx").strip().casefold()
    if client not in {"httpx", "curl_cffi"}:
        raise ValueError(f"{prefix}_HTTP_CLIENT must be httpx or curl_cffi")
    timeout = float(os.getenv(f"{prefix}_HTTP_TIMEOUT_SECONDS", "30"))
    if timeout <= 0:
        raise ValueError(f"{prefix}_HTTP_TIMEOUT_SECONDS must be positive")
    return SourceHTTPConfig(
        pharmacy=pharmacy.casefold(),
        client=client,
        impersonate=os.getenv(f"{prefix}_CURL_IMPERSONATE", "chrome110").strip(),
        verify=_env_bool(f"{prefix}_TLS_VERIFY", True),
        timeout=timeout,
    )


@asynccontextmanager
async def open_source_http_client(
    pharmacy: str, *, headers: dict[str, str]
) -> AsyncIterator[Any]:
    """Open the explicitly configured client with redirects disabled."""
    config = source_http_config(pharmacy)
    logger.info(
        "pharmacy_http_client_selected",
        extra={
            "event": "pharmacy_http_client_selected",
            "pharmacy": config.pharmacy,
            "client": config.client,
            "impersonate": config.impersonate if config.client == "curl_cffi" else None,
            "tls_verify": config.verify,
            "timeout_seconds": config.timeout,
            "follow_redirects": False,
        },
    )
    if config.client == "httpx":
        async with httpx.AsyncClient(
            headers=headers,
            verify=config.verify,
            follow_redirects=False,
            timeout=config.timeout,
            trust_env=False,
        ) as client:
            yield RateLimitedSourceClient(client, config.pharmacy)
        return

    from curl_cffi import requests

    async with requests.AsyncSession(
        headers=headers,
        impersonate=config.impersonate,
        verify=config.verify,
        timeout=config.timeout,
        allow_redirects=False,
        trust_env=False,
    ) as client:
        yield RateLimitedSourceClient(client, config.pharmacy)
