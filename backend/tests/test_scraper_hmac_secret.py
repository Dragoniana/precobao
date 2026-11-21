from __future__ import annotations

from pathlib import Path

import pytest

from app.services.scraper_hmac import (
    HMACSecretConfigurationError,
    get_scraper_hmac_secret,
)


def test_reads_protected_file_in_preference_to_legacy_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = b"f" * 32
    secret_file = tmp_path / "hmac-secret"
    secret_file.write_bytes(secret + b"\n")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", "legacy-value-that-must-not-win")

    assert get_scraper_hmac_secret() == secret


def test_supports_legacy_environment_when_file_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SCRAPER_INGEST_HMAC_SECRET_FILE", raising=False)
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "legacy-secret-value-32-bytes-long"
    )

    assert get_scraper_hmac_secret() == b"legacy-secret-value-32-bytes-long"


def test_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SCRAPER_INGEST_HMAC_SECRET_FILE", raising=False)
    monkeypatch.delenv("SCRAPER_INGEST_HMAC_SECRET", raising=False)

    with pytest.raises(HMACSecretConfigurationError):
        get_scraper_hmac_secret()


@pytest.mark.parametrize("secret", [b"", b"s" * 31])
def test_rejects_missing_or_short_secret_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: bytes
):
    secret_file = tmp_path / "short-secret"
    secret_file.write_bytes(secret)
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET_FILE", str(secret_file))
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "legacy-secret-value-32-bytes-long"
    )

    with pytest.raises(HMACSecretConfigurationError):
        get_scraper_hmac_secret()


def test_unreadable_configured_file_does_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret_file = tmp_path / "is-a-directory"
    secret_file.mkdir()
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET_FILE", str(secret_file))
    monkeypatch.setenv(
        "SCRAPER_INGEST_HMAC_SECRET", "legacy-secret-value-32-bytes-long"
    )

    with pytest.raises(HMACSecretConfigurationError) as error:
        get_scraper_hmac_secret()

    assert str(error.value) == ""
    assert error.value.__suppress_context__
