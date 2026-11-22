from urllib.parse import parse_qs, urlsplit

import pytest

from app.scrapers import pacheco as pacheco_module
from app.scrapers import source_http_client
from app.scrapers.pacheco import PachecoScraper
from app.scrapers.pacheco_scheduler import _next_run


@pytest.fixture(autouse=True)
def enable_mocked_pacheco_source(monkeypatch):
    monkeypatch.setenv("PACHECO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("PACHECO_PUBLIC_API_AUTHORIZED", "true")
    monkeypatch.setenv("PACHECO_ACCESS_CONFIRMED", "true")


def test_pacheco_restart_preserves_403_schedule_and_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    scraper = PachecoScraper(request_delay=0)
    scraper.stats.update(
        {
            "stop_reason": "http_403",
            "http_403_attempts": 2,
            "http_403_failed_cycles": 0,
            "http_403_next_retry_at": "2026-11-21T12:05:00+00:00",
            "http_403_last_valid_checkpoint": {"last_completed_offset": 100},
        }
    )
    scraper._write_checkpoint()

    restarted = PachecoScraper(request_delay=0)
    restarted._start_run()

    assert restarted.stats["http_403_attempts"] == 2
    assert restarted.stats["http_403_next_retry_at"] == "2026-11-21T12:05:00+00:00"
    assert restarted.stats["http_403_last_valid_checkpoint"] == {
        "last_completed_offset": 100
    }


def test_pacheco_legacy_limited_checkpoint_advances_past_completed_page():
    state = PachecoScraper._new_stats(
        {"status": "limited", "last_completed_offset": 0, "brands_processed": 0}
    )
    assert state["scan_cursor"] == {"stage": "root", "offset": 50}


def test_pacheco_starts_new_refresh_cycle_after_completed_legacy_run():
    state = PachecoScraper._new_stats(
        {"status": "complete", "last_completed_offset": 500, "brands_processed": 4}
    )
    assert state["scan_cursor"] == {"stage": "root", "offset": 0}


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
    monkeypatch.setattr(
        pacheco_module,
        "load_authorized_partner_feed",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("DIRECT mode must not load a feed")
        ),
    )
    page_rows = {
        0: [{"productId": f"first-{i}", "items": []} for i in range(50)],
        50: [{"productId": f"last-{i}", "items": []} for i in range(3)],
    }

    class Response:
        status_code = 206

        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}

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
            rows = page_rows[offset]
            if rows:
                return Response(
                    rows,
                    headers={"resources": f"{offset}-{offset + len(rows) - 1}/53"},
                )
            return Response(rows)

    session = Session()
    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    root_offsets = [
        int(parse_qs(urlsplit(url).query)["_from"][0])
        for url in session.requested_urls
        if "/EMS?" not in url and "/facets/" not in url
    ]
    assert root_offsets == [0, 50]
    root_urls = [
        url
        for url in session.requested_urls
        if "/EMS?" not in url and "/facets/" not in url
    ]
    assert parse_qs(urlsplit(root_urls[0]).query)["map"] == ["c"]
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
    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", lambda **_: session)
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

    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", lambda **_: Session())
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
async def test_shared_backoff_respects_retry_after_and_retries_429(monkeypatch):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    calls = 0
    waits = []

    async def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            response = Response(429)
            response.headers = {"Retry-After": "7"}
            return response
        return Response(200)

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(pacheco_module.asyncio, "sleep", no_wait)
    scraper = PachecoScraper(request_delay=0)
    response = await scraper.executar_com_backoff(request, initial_delay=0)

    assert response.status_code == 200
    assert calls == 2
    assert waits == [7]


@pytest.mark.asyncio
async def test_shared_backoff_caps_persistent_429_at_three_attempts(monkeypatch):
    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        return Response()

    async def no_wait(_):
        return None

    monkeypatch.setattr(pacheco_module.asyncio, "sleep", no_wait)
    response = await PachecoScraper(request_delay=0).executar_com_backoff(
        request, initial_delay=0
    )

    assert response.status_code == 429
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("first_result", ["timeout", 503])
async def test_shared_backoff_recovers_from_transient_timeout_or_503(
    monkeypatch, first_result
):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        if calls == 1 and first_result == "timeout":
            raise TimeoutError("simulated timeout")
        if calls == 1:
            return Response(503)
        return Response(200)

    async def no_wait(_):
        return None

    monkeypatch.setattr(pacheco_module.asyncio, "sleep", no_wait)
    response = await PachecoScraper(request_delay=0).executar_com_backoff(
        request, initial_delay=0
    )

    assert response.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_shared_backoff_does_not_retry_tls_errors(monkeypatch):
    import ssl

    calls = 0

    async def request():
        nonlocal calls
        calls += 1
        raise ssl.SSLError("simulated certificate failure")

    with pytest.raises(ssl.SSLError):
        await PachecoScraper(request_delay=0).executar_com_backoff(
            request, initial_delay=0
        )

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
    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert len(session.requests) == 1
    assert scraper.stats["last_http_status"] == 403
    assert scraper.stats["retryable"] is False
    assert scraper.stats["stop_reason"] == "http_403"


@pytest.mark.asyncio
async def test_pacheco_stops_after_429_and_records_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    async def no_wait(_):
        return None

    monkeypatch.setattr(pacheco_module.asyncio, "sleep", no_wait)

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
    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", lambda **_: session)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert len(session.requests) == 3
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

    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", unexpected_network)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert scraper.stats["stop_reason"] == "PACHECO_DIRECT_ACCESS_NOT_CONFIRMED"
    assert scraper.stats["products_enqueued"] == 0


@pytest.mark.asyncio
async def test_pacheco_disabled_mode_never_contacts_public_api(tmp_path, monkeypatch):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PACHECO_SOURCE_MODE", "DISABLED")

    def unexpected_network(**_kwargs):
        raise AssertionError("DISABLED mode must not contact the source.")

    monkeypatch.setattr(source_http_client.httpx, "AsyncClient", unexpected_network)
    scraper = PachecoScraper(request_delay=0)

    await scraper.run()

    assert scraper.stats["status"] == "disabled"
    assert scraper.stats["stop_reason"] == "source_disabled"
    assert scraper.stats["pages_requested"] == 0


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


@pytest.mark.asyncio
async def test_pacheco_rejects_incomplete_206_page_without_total_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))

    class Response:
        status_code = 206
        headers = {}

        def json(self):
            return [
                {"productId": f"partial-{index}", "items": []} for index in range(4)
            ]

    class Session:
        async def get(self, *_args, **_kwargs):
            return Response()

    scraper = PachecoScraper(request_delay=0)
    result = await scraper._scan_partition(
        Session(),
        "https://www.drogariaspacheco.com.br/api/search",
        partition="brand:test",
    )

    assert result is False
    assert scraper.stats["pages_succeeded"] == 0
    assert scraper.stats["last_completed_offset"] is None
    assert scraper.stats["failure_category"] == "parser_or_source_data_error"
    assert "HTTP 206 short page has no advertised total" in scraper.stats["errors"][0]


@pytest.mark.asyncio
async def test_pacheco_persists_and_resumes_next_offset_after_product_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PACHECO_STATE_DIR", str(tmp_path))
    offsets = []

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, offset):
            self.offset = offset

        def json(self):
            return [
                {"productId": f"product-{self.offset + index}"} for index in range(50)
            ]

    class Session:
        async def get(self, url, timeout):
            offset = int(parse_qs(urlsplit(url).query)["_from"][0])
            offsets.append(offset)
            return Response(offset)

    async def run_one_page(scraper, expected_offset):
        scraper._start_run()

        def queue(rows):
            scraper.stats["products_enqueued"] += len(rows)
            return [{"sku_interno": row["productId"]} for row in rows]

        scraper._queue_source_products = queue
        scraper.products_callback = _deliver
        cursor = scraper.stats["scan_cursor"]
        result = await scraper._scan_partition(
            Session(),
            "https://www.drogariaspacheco.com.br/api/search",
            partition="medicamentos",
            initial_offset=cursor["offset"],
            cursor_stage="root",
        )
        assert result is True
        assert scraper.stats["scan_cursor"]["offset"] == expected_offset + 50

    async def _deliver(_products):
        return None

    first = PachecoScraper(max_items=50, request_delay=0)
    await run_one_page(first, 0)
    restarted = PachecoScraper(max_items=50, request_delay=0)
    await run_one_page(restarted, 50)

    assert offsets == [0, 50]
    assert restarted.stats["resume_strategy"] == "durable_partition_and_offset_cursor"


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
