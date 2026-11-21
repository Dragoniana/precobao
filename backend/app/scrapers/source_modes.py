"""Explicit source modes and HTTP failure handling for pharmacy collectors."""

from __future__ import annotations

import email.utils
import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SourceMode(StrEnum):
    DIRECT = "DIRECT"
    FEED = "FEED"
    DISABLED = "DISABLED"


class SourceHTTPError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"source_http_{status_code}")


def source_mode(pharmacy: str) -> SourceMode:
    variable = f"{pharmacy.upper()}_SOURCE_MODE"
    value = os.getenv(variable, SourceMode.DISABLED.value).strip().upper()
    try:
        return SourceMode(value)
    except ValueError as error:
        raise ValueError(f"{variable} must be DIRECT, FEED or DISABLED") from error


def direct_access_ready(pharmacy: str) -> bool:
    """Require both recorded authorization and a separate access confirmation."""
    variables = {
        "araujo": ("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "ARAUJO_ACCESS_CONFIRMED"),
        "pacheco": ("PACHECO_PUBLIC_API_AUTHORIZED", "PACHECO_ACCESS_CONFIRMED"),
    }
    try:
        checks = variables[pharmacy.casefold()]
    except KeyError as error:
        raise ValueError(f"Unsupported pharmacy source: {pharmacy}") from error
    return all(
        os.getenv(variable, "false").strip().casefold() in {"1", "true", "yes", "on"}
        for variable in checks
    )


def retry_after_seconds(value: str | None) -> int | None:
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


def validate_source_response(response: Any, *, accepted: set[int]) -> None:
    status = int(response.status_code)
    if status in accepted:
        return
    retry_after = (
        retry_after_seconds(response.headers.get("Retry-After"))
        if status in {403, 429}
        else None
    )
    raise SourceHTTPError(status, retry_after_seconds=retry_after)
