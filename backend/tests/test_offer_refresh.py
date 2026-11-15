"""Source identity and real observation checks for periodic offer refresh."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.historico import HistoricoPreco
from app.models.oferta_farmacia import OfertaFarmacia
from app.scrapers import offer_refresh_scheduler
from app.scrapers.offer_refresh import (
    IdentityMismatch,
    SourceAccessBlocked,
    SourceUnavailable,
    _get_with_recovery,
    parse_araujo,
    parse_indiana,
    parse_pacheco,
    record_observation,
    refresh,
    refresh_images,
)


@pytest.fixture(autouse=True)
def enable_direct_source_mode_for_tests(monkeypatch):
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "DIRECT")
    monkeypatch.setenv("PACHECO_SOURCE_MODE", "DIRECT")


def _offer(source="indiana"):
    sku = "IND132111" if source == "indiana" else "ARJ101392"
    url = (
        "https://www.farmaciaindiana.com.br/item/p"
        if source == "indiana"
        else "https://www.araujo.com.br/item/101392.html"
    )

    ean = "7896523227443" if source == "indiana" else "7897705203095"
    return SimpleNamespace(
        sku_interno=sku,
        url_origem=url,
        produto_catalogo=SimpleNamespace(codigo_barras=ean),
    )


@pytest.mark.parametrize(
    ("refresh_function", "source"),
    [(refresh, "araujo"), (refresh_images, "araujo"), (refresh_images, "pacheco")],
)
def test_recurring_refresh_fails_closed_without_source_authorization(
    refresh_function, source, monkeypatch
):
    monkeypatch.delenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", raising=False)
    monkeypatch.delenv("PACHECO_PUBLIC_API_AUTHORIZED", raising=False)

    def unexpected_database_access():
        raise AssertionError("database must not be accessed while source is disabled")

    monkeypatch.setattr(
        "app.scrapers.offer_refresh.SessionLocal", unexpected_database_access
    )

    result = refresh_function(source, limit=1, delay=0)

    assert result["stop_reason"] == "source_authorization_required"
    assert result["checked"] == 0


def test_offer_refresh_does_not_use_direct_endpoint_in_feed_mode(monkeypatch):
    monkeypatch.setenv("ARAUJO_SOURCE_MODE", "FEED")
    monkeypatch.setenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", "true")
    monkeypatch.setenv("ARAUJO_ACCESS_CONFIRMED", "true")

    def unexpected_database_access():
        raise AssertionError("Feed mode must not run the direct offer refresher")

    monkeypatch.setattr(
        "app.scrapers.offer_refresh.SessionLocal", unexpected_database_access
    )

    result = refresh("araujo", limit=1, delay=0)

    assert result["stop_reason"] == "source_mode_feed"
    assert result["checked"] == 0


@pytest.mark.parametrize("source", ["araujo", "images-araujo", "images-pacheco"])
def test_offer_refresh_scheduler_skips_sources_without_authorization(
    source, monkeypatch, tmp_path
):
    monkeypatch.setattr(offer_refresh_scheduler, "STATE_DIR", tmp_path)
    monkeypatch.delenv("ARAUJO_PUBLIC_CATALOG_AUTHORIZED", raising=False)
    monkeypatch.delenv("PACHECO_PUBLIC_API_AUTHORIZED", raising=False)

    assert not offer_refresh_scheduler._due(source, datetime.now(UTC), 30)


def test_successful_refresh_resets_consecutive_failure_count(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(offer_refresh_scheduler, "STATE_DIR", tmp_path)
    (tmp_path / "indiana-monitoring.json").write_text(
        json.dumps({"consecutive_failures": 5, "last_success_at": "old"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(offer_refresh_scheduler, "_catalog_metrics", lambda _: {})

    offer_refresh_scheduler._run_monitored(
        "indiana", "indiana", 60, lambda: {"checked": 1, "errors": []}
    )

    state = json.loads((tmp_path / "indiana-monitoring.json").read_text())
    assert state["success"] is True
    assert state["consecutive_failures"] == 0


def test_indiana_requires_exact_sku_ean_and_official_seller():
    offer = _offer()
    payload = [
        {
            "link": offer.url_origem,
            "items": [
                {
                    "itemId": "132111",
                    "ean": offer.produto_catalogo.codigo_barras,
                    "sellers": [
                        {
                            "sellerDefault": True,
                            "commertialOffer": {"Price": 8.63, "IsAvailable": True},
                        }
                    ],
                }
            ],
        }
    ]
    assert parse_indiana(offer, payload) == (Decimal("8.63"), True)
    payload[0]["items"][0]["ean"] = "7890000000000"
    with pytest.raises(IdentityMismatch, match="ean_mismatch"):
        parse_indiana(offer, payload)
    payload[0]["items"][0]["ean"] = offer.produto_catalogo.codigo_barras
    payload[0]["items"][0]["sellers"] = []
    with pytest.raises(SourceUnavailable, match="official_seller"):
        parse_indiana(offer, payload)


def test_araujo_uses_only_matching_structured_product():
    offer = _offer("araujo")
    product = {
        "@type": "Product",
        "sku": "101392",
        "gtin13": offer.produto_catalogo.codigo_barras,
        "offers": {
            "url": offer.url_origem,
            "priceCurrency": "BRL",
            "price": "999",
            "availability": "https://schema.org/InStock",
        },
    }
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'
    assert parse_araujo(offer, html) == (Decimal("999"), True)
    product["sku"] = "another-presentation"
    html = f'<script type="application/ld+json">{json.dumps(product)}</script>'
    with pytest.raises(IdentityMismatch, match="sku_mismatch"):
        parse_araujo(offer, html)


def test_first_observation_and_price_change_create_events_but_repeat_does_not():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        pharmacy = Farmacia(
            cnpj="00000000000100",
            razao_social="Indiana",
            nome_fantasia="Indiana",
            endereco_completo="Test",
        )
        medicine = CatalogoBase(
            codigo_barras="7896523227443",
            name_search="Tadalafila",
            principio_ativo="Tadalafila",
            laboratorio="Cimed",
        )
        db.add_all([pharmacy, medicine])
        db.flush()
        offer = OfertaFarmacia(
            sku_interno="IND132111",
            preco=Decimal("11.06"),
            disponivel=True,
            quantidade_estoque=1,
            url_origem="https://www.farmaciaindiana.com.br/item/p",
            farmacia_id=pharmacy.id,
            catalogo_id=medicine.id,
        )
        db.add(offer)
        db.commit()
        assert record_observation(db, offer, "indiana", Decimal("8.63"), True)
        assert not record_observation(db, offer, "indiana", Decimal("8.63"), True)
        assert record_observation(db, offer, "indiana", Decimal("8.20"), True)
        events = db.scalars(select(HistoricoPreco).order_by(HistoricoPreco.id)).all()
        assert [event.preco for event in events] == [Decimal("8.63"), Decimal("8.20")]
        assert all(event.oferta_id == offer.id for event in events)
        assert offer.observado_em is not None
    engine.dispose()


def test_scheduler_does_not_retry_a_forbidden_source(monkeypatch, tmp_path):
    monkeypatch.setattr(offer_refresh_scheduler, "STATE_DIR", tmp_path)
    (tmp_path / "araujo.json").write_text(
        json.dumps(
            {"stop_reason": "http_403", "updated_at": datetime.now(UTC).isoformat()}
        ),
        encoding="utf-8",
    )
    assert not offer_refresh_scheduler._due("araujo", datetime.now(UTC), 30)


@pytest.mark.parametrize(
    "status,expected_calls", [(200, 1), (403, 1), (429, 3), (503, 3)]
)
def test_http_recovery_stops_forbidden_and_bounds_transient_retries(
    monkeypatch, status, expected_calls
):
    calls = []
    monkeypatch.setattr("app.scrapers.offer_refresh.time.sleep", lambda _: None)

    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "1"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    if status == 200:
        assert _get_with_recovery(client, "https://source.test/item").status_code == 200
    elif status == 403:
        with pytest.raises(SourceAccessBlocked, match="http_403"):
            _get_with_recovery(client, "https://source.test/item")
    else:
        with pytest.raises(SourceUnavailable, match=f"http_{status}"):
            _get_with_recovery(client, "https://source.test/item")
    assert len(calls) == expected_calls
    client.close()


def test_pacheco_price_parser_requires_exact_offer_identity():
    offer = SimpleNamespace(
        sku_interno="PCH810090",
        url_origem="https://www.drogariaspacheco.com.br/produto/p",
        produto_catalogo=SimpleNamespace(codigo_barras="7890000000000"),
    )
    payload = [
        {
            "linkText": "produto",
            "items": [
                {
                    "itemId": "810090",
                    "ean": "7890000000000",
                    "sellers": [
                        {
                            "sellerDefault": True,
                            "commertialOffer": {"Price": 12.5, "IsAvailable": True},
                        }
                    ],
                }
            ],
        }
    ]
    assert parse_pacheco(offer, payload) == (Decimal("12.5"), True)
    payload[0]["items"][0]["ean"] = "7891111111111"
    with pytest.raises(IdentityMismatch, match="ean_mismatch"):
        parse_pacheco(offer, payload)


def test_daily_scheduler_uses_brasilia_slots_and_persists_idempotence(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(offer_refresh_scheduler, "STATE_DIR", tmp_path)
    now = datetime(2026, 11, 14, 11, 30, tzinfo=UTC)  # 08:30 in Brasilia
    due, slot = offer_refresh_scheduler._daily_slot_due(
        "araujo", now, ["08:00", "20:00"]
    )
    assert due and slot.startswith("2026-11-14T08:00:00")
    offer_refresh_scheduler._mark_daily_slot("araujo", slot)
    assert (
        offer_refresh_scheduler._daily_slot_due("araujo", now, ["08:00", "20:00"])[0]
        is False
    )
