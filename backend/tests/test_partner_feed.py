import json
from datetime import UTC, datetime

import pytest

from app.scrapers.araujo import AraujoScraper
from app.scrapers.pacheco import PachecoScraper
from app.scrapers.partner_feed import (
    PartnerFeedError,
    PartnerFeedForbidden,
    load_authorized_partner_feed,
    parse_partner_feed,
)


def _product(**overrides):
    return {
        "id": "source-1",
        "sku": "SKU-1",
        "ean": "7891234567895",
        "name": "Medicamento de teste",
        "price": "12.34",
        "currency": "BRL",
        "available": "true",
        "quantity": "4",
        "observed_at": datetime.now(UTC).isoformat(),
        "url": "https://partner.example.test/products/1",
        "image_url": "https://partner.example.test/images/1.webp",
        **overrides,
    }


def test_legacy_araujo_html_parser_remains_available_for_offline_fixtures():
    scraper = AraujoScraper()
    html = """
    <div class="productTile" data-url="/dipirona-500mg.html" data-pid="123">
      <div class="gtmContainer__productTile"
        data-gtmga4data='{"item_name":"Dipirona 500 mg","price":12.5}'></div>
      <img class="productTile__imageWrapper__img"
        data-src="https://i3-imagens-prd.araujo.com.br/webp/123/7891234567895_1.webp">
    </div>
    """

    [product] = scraper.adaptar_parser_araujo_html(html)

    assert product["id"] == "123"
    assert product["sku_interno"] == "ARJ123"
    assert product["ean"] == "7891234567895"
    assert product["preco"] == 12.5
    assert product["link"] == "https://www.araujo.com.br/dipirona-500mg.html"
    assert product["imagem_url"].endswith("7891234567895_1.webp")


def test_csv_feed_maps_to_validated_scraper_contract():
    observed_at = datetime.now(UTC).isoformat()
    payload = (
        "id,sku,ean,name,price,currency,available,quantity,url,image_url,observed_at\n"
        f"source-1,SKU-1,7891234567895,Medicamento,12.34,BRL,true,4,"
        "https://partner.example.test/products/1,"
        f"https://partner.example.test/images/1.webp,{observed_at}\n"
    )

    [product] = parse_partner_feed(payload, "csv", sku_prefix="ARJ")

    assert product["sku_interno"] == "ARJSKU-1"
    assert product["ean"] == "7891234567895"
    assert product["preco"] == 12.34
    assert product["disponivel"] is True
    assert product["quantidade_estoque"] == 4


def test_json_feed_accepts_products_envelope():
    payload = json.dumps({"products": [_product()]})

    [product] = parse_partner_feed(payload, "json", sku_prefix="PCH")

    assert product["sku_interno"] == "PCHSKU-1"
    assert product["name_search"] == "Medicamento de teste"


def test_partner_feed_normalizes_valid_ean():
    [product] = parse_partner_feed(
        json.dumps([_product(ean="789-123-456-7895")]), "json", sku_prefix="PCH"
    )

    assert product["ean"] == "7891234567895"


def test_partner_feed_canonicalizes_pharmacy_sku_prefix():
    [product] = parse_partner_feed(
        json.dumps([_product(sku="pch-42")]), "json", sku_prefix="PCH"
    )

    assert product["sku_interno"] == "PCH-42"


@pytest.mark.parametrize(
    "overrides",
    [
        {"currency": "USD"},
        {"available": "unknown"},
        {"quantity": "-1"},
        {"quantity": "1.5"},
        {"price": "0"},
        {"url": "http://partner.example.test/products/1"},
        {"ean": "7891234567890"},
        {"currency": None},
    ],
)
def test_invalid_feed_row_rejects_the_whole_document(overrides):
    with pytest.raises(PartnerFeedError):
        parse_partner_feed(
            json.dumps([_product(**overrides)]), "json", sku_prefix="PCH"
        )


@pytest.mark.asyncio
async def test_file_feed_is_validated_before_araujo_queues_products(
    tmp_path, monkeypatch
):
    source_file = tmp_path / "araujo.json"
    source_file.write_text(json.dumps([_product()]), encoding="utf-8")
    monkeypatch.setenv("ARAUJO_PARTNER_FEED_FILE", str(source_file))
    monkeypatch.setenv("ARAUJO_PARTNER_FEED_FORMAT", "json")

    scraper = AraujoScraper()
    await scraper.extrair_catalogo()

    queued = scraper.fila.get_nowait()
    assert queued["sku_interno"] == "ARJSKU-1"
    assert scraper.fila.empty()


@pytest.mark.asyncio
async def test_araujo_without_partner_feed_fails_closed():
    scraper = AraujoScraper()
    with pytest.raises(RuntimeError, match="feed autorizado"):
        await scraper.extrair_catalogo()


@pytest.mark.asyncio
async def test_partner_api_uses_tls_and_bearer_auth_without_logging_token(monkeypatch):
    token = "test-secret-token"
    monkeypatch.setenv(
        "PACHECO_PARTNER_FEED_URL", "https://feed.example.test/data.json"
    )
    monkeypatch.setenv("PACHECO_PARTNER_FEED_FORMAT", "json")
    monkeypatch.setenv("PACHECO_PARTNER_FEED_TOKEN", token)
    captured = {}

    class Response:
        status_code = 200
        text = json.dumps([_product()])

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

    monkeypatch.setattr("app.scrapers.partner_feed.httpx.AsyncClient", Client)

    products = await load_authorized_partner_feed(
        prefix="PACHECO_PARTNER_FEED", sku_prefix="PCH"
    )

    assert len(products) == 1
    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["headers"]["Authorization"] == f"Bearer {token}"


@pytest.mark.asyncio
async def test_partner_api_403_is_not_retried_or_exposed_with_credentials(monkeypatch):
    token = "must-not-appear-in-errors"
    monkeypatch.setenv("ARAUJO_PARTNER_FEED_URL", "https://feed.example.test/data.json")
    monkeypatch.setenv("ARAUJO_PARTNER_FEED_FORMAT", "json")
    monkeypatch.setenv("ARAUJO_PARTNER_FEED_TOKEN", token)
    calls = 0

    class Response:
        status_code = 403
        text = "sensitive upstream body"

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr("app.scrapers.partner_feed.httpx.AsyncClient", Client)

    with pytest.raises(PartnerFeedForbidden) as error:
        await load_authorized_partner_feed(
            prefix="ARAUJO_PARTNER_FEED", sku_prefix="ARJ"
        )

    assert calls == 1
    assert token not in str(error.value)
    assert "sensitive upstream body" not in str(error.value)


@pytest.mark.asyncio
async def test_pacheco_feed_is_used_without_contacting_public_site(
    tmp_path, monkeypatch
):
    source_file = tmp_path / "pacheco.csv"
    source_file.write_text(
        "id,sku,name,price,currency,available,quantity,url,observed_at\n"
        f"p-1,sku-1,Medicamento,4.50,BRL,true,1,"
        f"https://partner.example.test/p/1,{datetime.now(UTC).isoformat()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PACHECO_PARTNER_FEED_FILE", str(source_file))
    monkeypatch.setenv("PACHECO_PARTNER_FEED_FORMAT", "csv")

    scraper = PachecoScraper(request_delay=0)
    await scraper.extrair_catalogo()

    assert scraper.stats["stop_reason"] == "authorized_partner_feed_validated"
    assert scraper.stats["products_enqueued"] == 1
    assert scraper.fila.qsize() == 1
