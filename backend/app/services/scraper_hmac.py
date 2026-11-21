"""Shared, fail-closed loading of the scraper-ingestion HMAC key."""

from __future__ import annotations

import os
import stat


class HMACSecretConfigurationError(Exception):
    """Raised when the configured HMAC secret is absent or unusable."""


def get_scraper_hmac_secret() -> bytes:
    """Load the key from a protected file, or use the legacy environment value.

    A configured file takes precedence. Read errors never fall back to the
    environment variable, which could otherwise silently select another key.
    """
    secret_file = os.getenv("SCRAPER_INGEST_HMAC_SECRET_FILE", "")
    if secret_file:
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(secret_file, flags)
            with os.fdopen(descriptor, "rb") as stream:
                file_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 4096:
                    raise OSError
                secret = stream.read().rstrip(b"\r\n")
        except OSError:
            raise HMACSecretConfigurationError from None
    else:
        secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "").encode("utf-8")

    if len(secret) < 32:
        raise HMACSecretConfigurationError
    return secret
