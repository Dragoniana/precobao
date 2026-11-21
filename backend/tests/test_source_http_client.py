from __future__ import annotations

import pytest

from app.scrapers import source_http_client as clients


def test_source_http_defaults_to_httpx_with_tls_verification(monkeypatch):
    monkeypatch.delenv("ARAUJO_HTTP_CLIENT", raising=False)
    monkeypatch.delenv("ARAUJO_TLS_VERIFY", raising=False)

    config = clients.source_http_config("araujo")

    assert config.client == "httpx"
    assert config.verify is True
    assert config.timeout == 30


def test_source_http_settings_are_independent(monkeypatch):
    monkeypatch.setenv("ARAUJO_HTTP_CLIENT", "curl_cffi")
    monkeypatch.setenv("ARAUJO_CURL_IMPERSONATE", "chrome110")
    monkeypatch.setenv("ARAUJO_TLS_VERIFY", "false")
    monkeypatch.setenv("PACHECO_HTTP_CLIENT", "httpx")
    monkeypatch.setenv("PACHECO_TLS_VERIFY", "true")

    araujo = clients.source_http_config("araujo")
    pacheco = clients.source_http_config("pacheco")

    assert (araujo.client, araujo.impersonate, araujo.verify) == (
        "curl_cffi",
        "chrome110",
        False,
    )
    assert (pacheco.client, pacheco.verify) == ("httpx", True)


@pytest.mark.asyncio
async def test_curl_cffi_client_receives_historical_settings(monkeypatch):
    captured = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def make_session(**kwargs):
        captured.update(kwargs)
        return Session()

    from curl_cffi import requests

    monkeypatch.setenv("ARAUJO_HTTP_CLIENT", "curl_cffi")
    monkeypatch.setenv("ARAUJO_CURL_IMPERSONATE", "chrome110")
    monkeypatch.setenv("ARAUJO_TLS_VERIFY", "false")
    monkeypatch.setattr(requests, "AsyncSession", make_session)

    async with clients.open_source_http_client(
        "araujo", headers={"Accept": "text/html"}
    ):
        pass

    assert captured["impersonate"] == "chrome110"
    assert captured["verify"] is False
    assert captured["allow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["headers"] == {"Accept": "text/html"}


@pytest.mark.asyncio
async def test_httpx_client_uses_verified_tls_and_disables_redirects(monkeypatch):
    captured = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    def make_client(**kwargs):
        captured.update(kwargs)
        return Session()

    monkeypatch.setenv("PACHECO_HTTP_CLIENT", "httpx")
    monkeypatch.setenv("PACHECO_TLS_VERIFY", "true")
    monkeypatch.setattr(clients.httpx, "AsyncClient", make_client)

    async with clients.open_source_http_client("pacheco", headers={}):
        pass

    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["timeout"] == 30
    assert captured["trust_env"] is False


def test_source_http_rejects_invalid_client_or_timeout(monkeypatch):
    monkeypatch.setenv("PACHECO_HTTP_CLIENT", "other")
    with pytest.raises(ValueError, match="must be httpx or curl_cffi"):
        clients.source_http_config("pacheco")

    monkeypatch.setenv("PACHECO_HTTP_CLIENT", "httpx")
    monkeypatch.setenv("PACHECO_HTTP_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="must be positive"):
        clients.source_http_config("pacheco")
