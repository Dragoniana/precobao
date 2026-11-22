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
from app.scrapers.source_modes import SourceHTTPError


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


def test_araujo_parser_marks_unavailable_cards_and_rejects_missing_identity():
    scraper = AraujoScraper()
    html = """
    <div class="productTile" data-url="/dipirona.html" data-pid="123">
      <div class="gtmContainer__productTile"
        data-gtmga4data='{"item_name":"Dipirona 500 mg","price":12.5}'></div>
      <span>Produto indisponível</span>
    </div>
    <div class="productTile" data-url="/bad.html">
      <div class="gtmContainer__productTile"
        data-gtmga4data='{"item_name":"Sem SKU","price":1}'></div>
    </div>
    """

    [product] = scraper.adaptar_parser_araujo_html(html)

    assert product["sku_interno"] == "ARJ123"
    assert product["disponivel"] is False
    assert product["quantidade_estoque"] == 0


@pytest.mark.asyncio
async def test_araujo_direct_mode_works_without_feed_and_persists_page_checkpoint(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.delenv("ARAUJO_PARTNER_FEED_FILE", raising=False)
    monkeypatch.delenv("ARAUJO_PARTNER_FEED_URL", raising=False)
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))
    captured = {}

    class Response:
        status_code = 200
        headers = {}
        text = '<div class="productTile" data-pid="1"></div>'

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    monkeypatch.setattr("app.scrapers.source_http_client.httpx.AsyncClient", Client)
    scraper = AraujoScraper(source_mode="DIRECT", request_delay=0)
    monkeypatch.setattr(
        scraper,
        "adaptar_parser_araujo_html",
        lambda _html: [
            {
                "id": "1",
                "sku_interno": "ARJ1",
                "name_search": "Produto",
                "preco": 10,
                "link": "https://www.araujo.com.br/produto/1.html",
            }
        ],
    )

    await scraper.extrair_catalogo()

    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["url"] == scraper.CATALOG_URL
    assert captured["request"]["params"] == {"page": 1}
    assert scraper.fila.get_nowait()["sku_interno"] == "ARJ1"
    checkpoint = json.loads(scraper.source_checkpoint_path.read_text())
    assert checkpoint["status"] == "COMPLETE"
    assert checkpoint["resume_strategy"] == "durable_page_and_item_cursor"
    assert checkpoint["next_page"] == 1


@pytest.mark.asyncio
async def test_araujo_direct_403_stops_without_advancing_checkpoint(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 403
        headers = {}
        text = "denied"

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr("app.scrapers.source_http_client.httpx.AsyncClient", Client)
    scraper = AraujoScraper(source_mode="DIRECT", request_delay=0)

    with pytest.raises(SourceHTTPError) as error:
        await scraper.extrair_catalogo()

    checkpoint = json.loads(scraper.source_checkpoint_path.read_text())
    assert error.value.status_code == 403
    assert checkpoint["status"] == "ACCESS_BLOCKED"
    assert checkpoint["last_completed_page"] == 0


@pytest.mark.asyncio
async def test_araujo_checkpoint_preserves_budget_stop_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 200
        headers = {}
        text = '<div class="productTile" data-pid="1"></div>'

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr("app.scrapers.source_http_client.httpx.AsyncClient", Client)
    scraper = AraujoScraper(
        source_mode="DIRECT",
        request_delay=0,
        stop_requested_callback=lambda: True,
        stop_reason_callback=lambda: "BUDGET_EXHAUSTED",
    )
    monkeypatch.setattr(
        scraper,
        "adaptar_parser_araujo_html",
        lambda _html: [
            {
                "id": "1",
                "sku_interno": "ARJ1",
                "name_search": "Produto",
                "preco": 10,
                "link": "https://www.araujo.com.br/produto/1.html",
            }
        ],
    )

    await scraper.extrair_catalogo()

    checkpoint = json.loads(scraper.source_checkpoint_path.read_text())
    assert checkpoint["status"] == "BUDGET_EXHAUSTED"
    assert checkpoint["stop_reason"] == "budget_exhausted"
    assert checkpoint["last_completed_page"] == 1
    assert checkpoint["next_page"] == 2


@pytest.mark.asyncio
async def test_araujo_resumes_inside_page_after_50_product_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))
    requests = []

    class Response:
        status_code = 200
        headers = {}
        text = '<div class="productTile" data-pid="1"></div>'

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            requests.append(kwargs["params"]["page"])
            return Response()

    monkeypatch.setattr("app.scrapers.source_http_client.httpx.AsyncClient", Client)
    products = [{"sku_interno": f"ARJ{i}"} for i in range(3)]
    delivered_batches = []
    scraper = AraujoScraper(source_mode="DIRECT", request_delay=0)
    monkeypatch.setattr(scraper, "adaptar_parser_araujo_html", lambda _html: products)

    async def deliver_first_two(batch):
        delivered_batches.append([product["sku_interno"] for product in batch[:2]])
        return 2

    scraper.products_callback = deliver_first_two
    await scraper._extract_direct_catalog()
    first = json.loads(scraper.source_checkpoint_path.read_text())
    assert first["status"] == "PARTIAL"
    assert first["next_page"] == 1
    assert first["next_item_index"] == 2

    scraper = AraujoScraper(source_mode="DIRECT", request_delay=0)
    monkeypatch.setattr(scraper, "adaptar_parser_araujo_html", lambda _html: products)

    async def deliver_remainder(batch):
        delivered_batches.append([product["sku_interno"] for product in batch])
        return len(batch)

    scraper.products_callback = deliver_remainder
    await scraper._extract_direct_catalog()
    second = json.loads(scraper.source_checkpoint_path.read_text())
    assert requests == [1, 1]
    assert delivered_batches == [["ARJ0", "ARJ1"], ["ARJ2"]]
    assert second["status"] == "COMPLETE"
    assert second["next_page"] == 1
    assert second["next_item_index"] == 0


@pytest.mark.asyncio
async def test_araujo_does_not_overwrite_corrupt_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAUJO_STATE_DIR", str(tmp_path))
    checkpoint = tmp_path / "source-checkpoint.json"
    checkpoint.write_text("{invalid", encoding="utf-8")
    scraper = AraujoScraper(source_mode="DIRECT", request_delay=0)

    with pytest.raises(RuntimeError, match="ARAUJO_SOURCE_CHECKPOINT_INVALID"):
        await scraper._extract_direct_catalog()

    assert checkpoint.read_text(encoding="utf-8") == "{invalid"


def test_source_modes_are_explicit_and_default_disabled(monkeypatch):
    from app.scrapers.source_modes import SourceMode, source_mode

    monkeypatch.delenv("ARAUJO_SOURCE_MODE", raising=False)
    assert source_mode("araujo") is SourceMode.DISABLED
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "direct")
    assert source_mode("araujo") is SourceMode.DIRECT
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "unknown")
    with pytest.raises(ValueError, match="DIRECT, FEED or DISABLED"):
        source_mode("araujo")


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
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "FEED")
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
    scraper = AraujoScraper(source_mode="FEED")
    with pytest.raises(RuntimeError, match="ARAUJO_PARTNER_FEED_NOT_CONFIGURED"):
        await scraper.extrair_catalogo()


@pytest.mark.asyncio
async def test_araujo_disabled_mode_never_falls_back_to_direct(monkeypatch):
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DISABLED")
    scraper = AraujoScraper()
    monkeypatch.setattr(
        "app.scrapers.source_http_client.httpx.AsyncClient",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("DISABLED mode must not contact the source")
        ),
    )

    await scraper.extrair_catalogo()

    assert scraper.fila.empty()
    assert scraper.products_queued == 0


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
    monkeypatch.setenv("PACHECO_SOURCE_MODE", "FEED")
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
