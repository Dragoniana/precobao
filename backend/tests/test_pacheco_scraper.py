from urllib.parse import parse_qs, urlsplit

import pytest

from app.scrapers import pacheco as pacheco_module
from app.scrapers.pacheco import PachecoScraper
from app.scrapers.pacheco_scheduler import _next_run


@pytest.fixture(autouse=True)
def enable_mocked_pacheco_source(monkeypatch):
    monkeypatch.setenv("PACHECO_PUBLIC_API_AUTHORIZED", "true")


def test_pacheco_extracts_confirmed_unavailable_state_and_quantity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    scraper = PachecoScraper(request_delay=0)
    product = {
        "productId": "100",
        "productName": "Medicamento 100",
        "linkText": "medicamento-100",
        "items": [
            {
                "itemId": "100",
                "ean": "7891721201806",
                "name": "Medicamento 100",
                "images": [
                    {
                        "imageUrl": (
                            "https://drogariaspacheco.vteximg.com.br/arquivos/"
                            "7891721201806.jpg"
                        )
                    }
                ],
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 12.5,
                            "IsAvailable": False,
                            "AvailableQuantity": 0,
                        }
                    }
                ],
            }
        ],
    }

    extracted = scraper._extrair_produto(product)

    assert extracted is not None
    assert extracted["disponivel"] is False
    assert extracted["quantidade_estoque"] == 0
    assert extracted["preco"] == 12.5
    assert extracted["sku_interno"] == "PCH100"
    assert extracted["ean"] == "7891721201806"
    assert extracted["link"] == "https://www.drogariaspacheco.com.br/medicamento-100/p"
    assert extracted["imagem_url"].endswith("7891721201806.jpg")


@pytest.mark.asyncio
async def test_pacheco_paginates_past_pages_without_parseable_offers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    page_rows = {
        0: [{"productId": f"first-{i}", "items": []} for i in range(50)],
        50: [{"productId": f"last-{i}", "items": []} for i in range(3)],
    }

    class Response:
        status_code = 206

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        requested_urls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, timeout):
            self.requested_urls.append(url)
            if "/facets/" in url:
                return Response(
                    {
                        "Departments": [{"Quantity": 53}],
                        "Brands": [
                            {
                                "Name": "EMS",
                                "Quantity": 1,
                                "Link": "/medicamentos/EMS",
                            }
                        ],
                    }
                )
            offset = int(parse_qs(urlsplit(url).query)["_from"][0])
            if "/EMS?" in url:
                return Response([page_rows[50][0]])
            return Response(page_rows[offset])

    session = Session()
    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    root_offsets = [
        int(parse_qs(urlsplit(url).query)["_from"][0])
        for url in session.requested_urls
        if "/EMS?" not in url and "/facets/" not in url
    ]
    assert root_offsets == [0, 50]
    assert scraper.stats["pages_succeeded"] == 3
    assert scraper.stats["products_seen"] == 54
    assert scraper.stats["unique_source_products"] == 53
    assert scraper.stats["products_enqueued"] == 0
    assert scraper.stats["complete"] is True
    assert scraper.stats["stop_reason"] == "facet_total_reconciled"


@pytest.mark.asyncio
async def test_pacheco_stops_on_a_repeated_page_instead_of_looping(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    repeated_rows = [{"productId": f"repeat-{i}", "items": []} for i in range(50)]

    class Response:
        status_code = 206

        def json(self):
            return repeated_rows

    class Session:
        requested_offsets = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, timeout):
            if "/facets/" in url:
                return type(
                    "FacetResponse",
                    (),
                    {
                        "status_code": 200,
                        "json": lambda _: {
                            "Departments": [{"Quantity": 50}],
                            "Brands": [],
                        },
                    },
                )()
            offset = int(parse_qs(urlsplit(url).query)["_from"][0])
            self.requested_offsets.append(offset)
            return Response()

    session = Session()
    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert session.requested_offsets == [0, 50]
    assert scraper.stats["complete"] is False
    assert scraper.stats["pages_failed"] >= 1
    assert any("Repeated result page" in error for error in scraper.stats["errors"])


@pytest.mark.asyncio
async def test_pacheco_marks_http_400_as_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 400

        def json(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", lambda **_: Session())
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert scraper.stats["complete"] is False
    assert scraper.stats["pages_failed"] >= 1
    assert scraper.stats["status"] == "incomplete"
    assert any(
        "HTTP 400 at medicamentos offset 0" in error
        for error in scraper.stats["errors"]
    )


@pytest.mark.asyncio
async def test_shared_backoff_stops_on_429_without_retrying(monkeypatch):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        return Response(429)

    async def no_wait(_):
        return None

    monkeypatch.setattr(pacheco_module.asyncio, "sleep", no_wait)
    scraper = PachecoScraper(request_delay=0)
    response = await scraper.executar_com_backoff(request, initial_delay=0)

    assert response.status_code == 429
    assert calls == 1


@pytest.mark.asyncio
async def test_pacheco_stops_after_403_without_requesting_facets(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 403
        headers = {}

    class Session:
        requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, **_kwargs):
            self.requests.append(url)
            return Response()

    session = Session()
    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert len(session.requests) == 1
    assert scraper.stats["last_http_status"] == 403
    assert scraper.stats["retryable"] is False
    assert scraper.stats["stop_reason"] == "http_403"


@pytest.mark.asyncio
async def test_pacheco_stops_after_429_and_records_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 429
        headers = {"Retry-After": "3600"}

    class Session:
        requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, **_kwargs):
            self.requests.append(url)
            return Response()

    session = Session()
    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert len(session.requests) == 1
    assert scraper.stats["last_http_status"] == 429
    assert scraper.stats["retry_after_seconds"] == 3600
    assert scraper.stats["retryable"] is False


@pytest.mark.asyncio
async def test_pacheco_requires_authorized_source_before_network_access(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PACHECO_PUBLIC_API_AUTHORIZED", "false")

    def unexpected_network(**_kwargs):
        raise AssertionError("The source must not be contacted without authorization.")

    monkeypatch.setattr(pacheco_module.httpx, "AsyncClient", unexpected_network)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert scraper.stats["stop_reason"] == (
        "source_authorization_required: configure an authorized partner feed"
    )
    assert scraper.stats["products_enqueued"] == 0


@pytest.mark.asyncio
async def test_short_page_continues_when_facet_count_says_more_results(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 206

        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return self.rows

    class Session:
        offsets = []

        async def get(self, url, timeout):
            offset = int(parse_qs(urlsplit(url).query)["_from"][0])
            self.offsets.append(offset)
            if offset == 0:
                rows = [{"productId": f"short-{i}", "items": []} for i in range(10)]
                return Response(rows)
            if offset == 50:
                rows = [{"productId": f"next-{i}", "items": []} for i in range(50)]
                return Response(rows)
            return Response([])

    session = Session()
    scraper = PachecoScraper(request_delay=0)
    result = await scraper._scan_partition(
        session,
        "https://www.drogariaspacheco.com.br/api/search",
        partition="brand:test",
        expected_count=100,
        query_map="c,b",
    )

    assert result is True
    assert session.offsets == [0, 50, 100]
    assert scraper.stats["unique_source_products"] == 60
    assert scraper.stats["pages_failed"] == 0
    assert scraper.stats["partition_discrepancies"]


def test_scheduler_uses_durable_daily_interval(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setenv("PACHECO_INTERVAL_HOURS", "24")
    now = datetime(2026, 10, 24, 12, tzinfo=UTC)
    due = _next_run(
        now,
        {"finished_at": "2026-10-24T08:00:00+00:00", "status": "complete"},
    )

    assert due.isoformat() == "2026-10-25T08:00:00+00:00"


def test_scheduler_waits_at_least_the_source_retry_after(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setenv("PACHECO_FAILURE_RETRY_MINUTES", "60")
    now = datetime(2026, 10, 24, 12, tzinfo=UTC)
    due = _next_run(
        now,
        {
            "finished_at": "2026-10-24T08:00:00+00:00",
            "status": "incomplete",
            "retry_after_seconds": 18_000,
        },
    )

    assert due.isoformat() == "2026-10-24T13:00:00+00:00"
