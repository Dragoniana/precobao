from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import scraper_ingest
from app.core.database import get_db
from app.main import app
from app.models import Base, CatalogoBase, Farmacia, HistoricoPreco, OfertaFarmacia
from app.scrapers import base as scraper_base
from app.services.enriquecimento import ServicoEnriquecimentoFarmacologico

SECRET = "test-collector-hmac-secret-with-more-than-32-bytes"
ENDPOINT = "/api/v1/internal/scraper-batches"


@pytest.fixture
def ingest_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = testing_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(scraper_base, "SessionLocal", testing_sessions)
    monkeypatch.setattr(
        scraper_base,
        "upsert_image_references",
        lambda references: [
            bool(reference.get("image_url")) for reference in references
        ],
    )
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", SECRET)
    monkeypatch.delenv("SCRAPER_INGEST_HMAC_SECRET_FILE", raising=False)
    monkeypatch.setenv("SCRAPER_INGEST_ALLOWED_SOURCES", "araujo,pacheco")
    monkeypatch.setenv("SCRAPER_INGEST_RATE_LIMIT_PER_HOUR", "100")
    ServicoEnriquecimentoFarmacologico._cache_ean.clear()
    try:
        yield TestClient(app, base_url="https://testserver"), testing_sessions
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _product(*, price: float = 12.5) -> dict:
    return {
        "id": "101392",
        "sku_interno": "ARJ101392",
        "ean": "7897705203095",
        "name_search": "Medicamento de teste 1 mg",
        "preco": price,
        "disponivel": True,
        "quantidade_estoque": 1,
        "link": "https://www.araujo.com.br/produto/101392.html",
        "imagem_url": "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
        "fonte_observado_em": "2026-11-15T10:00:00-03:00",
    }


def _body(*, batch_id: str = "b2e4c2b2-23bd-4137-99d4-d0130a3a82b9", price=12.5):
    return json.dumps(
        {
            "batch_id": batch_id,
            "source": "araujo",
            "observed_at": "2026-11-15T10:00:00-03:00",
            "products": [_product(price=price)],
        },
        separators=(",", ":"),
    ).encode()


def _headers(
    body: bytes,
    *,
    timestamp: int | None = None,
    secret: str = SECRET,
) -> dict[str, str]:
    issued_at = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(
        secret.encode(), issued_at.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Scraper-Timestamp": issued_at,
        "X-Scraper-Signature": f"sha256={digest}",
    }


def test_ingest_is_authenticated_audited_and_idempotent(ingest_client):
    client, sessions = ingest_client
    body = _body()

    first = client.post(ENDPOINT, content=body, headers=_headers(body))
    repeated = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert first.status_code == 200
    assert first.json()["status"] == "COMPLETE"
    assert first.json()["catalog_created"] == 1
    assert first.json()["catalog_created"] == 1
    assert first.json()["offers_created"] == 1
    assert first.json()["price_observations"] == 1
    assert first.json()["images_registered"] == 1
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True
    with sessions() as db:
        offer = db.query(OfertaFarmacia).one()
        assert offer.fonte_observado_em.isoformat().startswith("2026-11-15T13:00:00")
        assert db.query(OfertaFarmacia).count() == 1
        assert db.query(HistoricoPreco).count() == 1


def test_ingest_rejects_reused_batch_id_with_different_payload(ingest_client):
    client, sessions = ingest_client
    first = _body()
    client.post(ENDPOINT, content=first, headers=_headers(first))
    changed = _body(price=13.0)

    response = client.post(ENDPOINT, content=changed, headers=_headers(changed))

    assert response.status_code == 409
    assert response.json()["detail"] == "batch_id_payload_conflict"
    with sessions() as db:
        offer = db.query(OfertaFarmacia).one()
        assert float(offer.preco) == 12.5
        assert db.query(HistoricoPreco).count() == 1


def test_new_batch_with_unchanged_price_does_not_add_price_history(ingest_client):
    client, sessions = ingest_client
    first = _body()
    client.post(ENDPOINT, content=first, headers=_headers(first))
    second = _body(batch_id="4515f77c-d8c0-4ac8-8cf8-71f33c468132")

    response = client.post(ENDPOINT, content=second, headers=_headers(second))

    assert response.status_code == 200
    assert response.json()["offers_updated"] == 1
    assert response.json()["price_changes"] == 0
    with sessions() as db:
        assert db.query(OfertaFarmacia).count() == 1
        assert db.query(HistoricoPreco).count() == 1


def test_ingest_rejects_bad_signature_stale_timestamp_and_non_https(ingest_client):
    client, _sessions = ingest_client
    body = _body()
    bad_signature = _headers(body)
    bad_signature["X-Scraper-Signature"] = "sha256=" + "0" * 64
    assert client.post(ENDPOINT, content=body, headers=bad_signature).status_code == 401

    stale = _headers(body, timestamp=int(time.time()) - 600)
    assert client.post(ENDPOINT, content=body, headers=stale).status_code == 401

    http_client = TestClient(app, base_url="http://testserver")
    assert (
        http_client.post(ENDPOINT, content=body, headers=_headers(body)).status_code
        == 400
    )


def test_ingest_uses_secret_file_and_fails_closed_without_leaking(
    ingest_client, monkeypatch, tmp_path, caplog
):
    client, sessions = ingest_client
    file_secret = "file-secret-that-is-not-the-environment-value-32-bytes"
    secret_file = tmp_path / "scraper-hmac"
    secret_file.write_text(file_secret, encoding="utf-8")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET_FILE", str(secret_file))
    body = _body()

    accepted = client.post(
        ENDPOINT, content=body, headers=_headers(body, secret=file_secret)
    )
    assert accepted.status_code == 200

    secret_file.unlink()
    secret_file.mkdir()
    caplog.set_level(logging.DEBUG)
    rejected = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert rejected.status_code == 503
    assert rejected.json() == {"detail": "scraper_ingest_not_configured"}
    assert file_secret not in caplog.text
    with sessions() as db:
        assert db.query(scraper_ingest.ScraperImportBatch).count() == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["products"][0].update(
            link="https://example.test/unrelated"
        ),
        lambda payload: payload["products"][0].update(sku_interno="PCH101392"),
        lambda payload: payload["products"][0].update(preco=0),
    ],
)
def test_ingest_rejects_source_identity_and_invalid_price(ingest_client, mutation):
    client, sessions = ingest_client
    payload = json.loads(_body())
    mutation(payload)
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 422
    with sessions() as db:
        assert db.query(OfertaFarmacia).count() == 0


def test_ingest_enforces_batch_size_and_secret_configuration(
    ingest_client, monkeypatch
):
    client, _sessions = ingest_client
    body = _body()
    monkeypatch.setenv("SCRAPER_INGEST_MAX_BODY_BYTES", "10")
    assert (
        client.post(ENDPOINT, content=body, headers=_headers(body)).status_code == 413
    )

    monkeypatch.setenv("SCRAPER_INGEST_MAX_BODY_BYTES", "1000000")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", "short")
    assert (
        client.post(ENDPOINT, content=body, headers=_headers(body)).status_code == 503
    )


def test_ingest_streaming_limit_applies_when_content_length_is_understated(
    ingest_client, monkeypatch
):
    client, _sessions = ingest_client
    body = _body()
    monkeypatch.setenv("SCRAPER_INGEST_MAX_BODY_BYTES", "100")
    request_headers = _headers(body)
    request_headers["Content-Length"] = "1"

    response = client.post(ENDPOINT, content=body, headers=request_headers)

    assert response.status_code == 413


def test_ingest_rejects_source_when_server_allowlist_is_empty(
    ingest_client, monkeypatch
):
    client, sessions = ingest_client
    monkeypatch.setenv("SCRAPER_INGEST_ALLOWED_SOURCES", "")
    body = _body()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 403
    with sessions() as db:
        assert db.query(scraper_ingest.ScraperImportBatch).count() == 0


def test_ingest_clears_invalid_ean_before_persistence(ingest_client):
    client, sessions = ingest_client
    payload = json.loads(_body())
    payload["products"][0]["ean"] = "7897705203094"
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 200
    with sessions() as db:
        assert db.query(CatalogoBase).one().codigo_barras is None


def test_ingest_does_not_register_generic_pacheco_image(ingest_client):
    client, sessions = ingest_client
    payload = {
        "batch_id": "a7791c70-28cc-49bd-9a65-152e1adfc35a",
        "source": "pacheco",
        "observed_at": "2026-11-15T10:00:00-03:00",
        "products": [
            {
                "id": "155349",
                "sku_interno": "PCH155349",
                "ean": "7891721027468",
                "name_search": "Glifage XR 500mg 30 comprimidos",
                "preco": 9.4,
                "disponivel": True,
                "quantidade_estoque": 1,
                "link": "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
                "imagem_url": "https://drogariaspacheco.vteximg.com.br/arquivos/de-referencia-tarja-vermelha-em-comprimido-pacheco.jpg",
                "fonte_observado_em": "2026-11-15T10:00:00-03:00",
            }
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 200
    assert response.json()["images_registered"] == 0
    assert response.json()["images_missing"] == 1
    with sessions() as db:
        assert db.query(OfertaFarmacia).one().sku_interno == "PCH155349"


@pytest.mark.parametrize(
    (
        "existing_sku",
        "existing_url",
        "existing_ean",
        "incoming_sku",
        "incoming_url",
        "incoming_ean",
        "expected_status",
    ),
    [
        (
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            "7891721201806",
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            "7891721027468",
            200,
        ),
        (
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            "7891721201806",
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos-alternativo/p",
            "7891721201806",
            200,
        ),
        (
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            "7891721201806",
            "PCH155349",
            "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            "7891721201806",
            200,
        ),
    ],
    ids=["ean-conflict", "sku-url-conflict", "matching-identity"],
)
def test_ingest_checks_existing_pacheco_offer_identity_before_writes(
    ingest_client,
    existing_sku,
    existing_url,
    existing_ean,
    incoming_sku,
    incoming_url,
    incoming_ean,
    expected_status,
):
    client, sessions = ingest_client
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="33438250000167",
            razao_social="Drogarias Pacheco S.A.",
            nome_fantasia="Pacheco",
            endereco_completo="Loja online",
        )
        catalog = CatalogoBase(
            codigo_barras=existing_ean,
            name_search="Glifage XR 500mg 30 comprimidos",
            principio_ativo="cloridrato de metformina",
            laboratorio="Merck S/A",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        db.add(
            OfertaFarmacia(
                sku_interno=existing_sku,
                preco=10.37,
                quantidade_estoque=1,
                disponivel=True,
                url_origem=existing_url,
                contexto_chave="global",
                farmacia_id=pharmacy.id,
                catalogo_id=catalog.id,
            )
        )
        db.commit()

    payload = {
        "batch_id": "a7791c70-28cc-49bd-9a65-152e1adfc35b",
        "source": "pacheco",
        "observed_at": "2026-11-15T10:00:00-03:00",
        "products": [
            {
                "id": "155349",
                "sku_interno": incoming_sku,
                "ean": incoming_ean,
                "name_search": "Glifage XR 500mg 30 comprimidos",
                "preco": 9.40,
                "disponivel": True,
                "quantidade_estoque": 1,
                "link": incoming_url,
                "imagem_url": None,
                "fonte_observado_em": "2026-11-15T10:00:00-03:00",
            }
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == expected_status
    if incoming_ean != existing_ean or incoming_url != existing_url:
        assert response.json()["status"] == "PARTIAL"
        assert response.json()["accepted_count"] == 0
        assert response.json()["rejected_count"] == 1
    with sessions() as db:
        assert db.query(CatalogoBase).count() == 1
        assert db.query(OfertaFarmacia).count() == 1
        assert db.query(HistoricoPreco).count() == (
            0 if incoming_ean != existing_ean or incoming_url != existing_url else 1
        )
        assert db.query(scraper_ingest.ScraperImportBatch).count() == (1)


def test_partial_batch_accepts_valid_siblings_and_is_idempotent(ingest_client):
    client, sessions = ingest_client
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="33438250000167",
            razao_social="Drogarias Pacheco S.A.",
            nome_fantasia="Pacheco",
            endereco_completo="Loja online",
        )
        catalog = CatalogoBase(
            codigo_barras="7891721201806",
            name_search="Glifage XR 500mg 30 comprimidos",
            principio_ativo="metformina",
            laboratorio="Merck",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        db.add(
            OfertaFarmacia(
                sku_interno="PCH155349",
                preco=10.37,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
                contexto_chave="global",
                farmacia_id=pharmacy.id,
                catalogo_id=catalog.id,
            )
        )
        db.commit()

    products = []
    for index in range(50):
        product_id = 155349 if index == 0 else 800000 + index
        products.append(
            {
                "id": str(product_id),
                "sku_interno": f"PCH{product_id}",
                "ean": "7891721027468" if index == 0 else None,
                "name_search": f"Produto teste Pacheco {product_id}",
                "preco": 10.0 + index,
                "disponivel": True,
                "quantidade_estoque": 1,
                "link": (
                    "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p"
                    if index == 0
                    else f"https://www.drogariaspacheco.com.br/produto/{product_id}/p"
                ),
                "imagem_url": None,
                "fonte_observado_em": "2026-11-15T10:00:00-03:00",
            }
        )
    payload = {
        "batch_id": "d7791c70-28cc-49bd-9a65-152e1adfc350",
        "source": "pacheco",
        "observed_at": "2026-11-15T10:00:00-03:00",
        "products": products,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    first = client.post(ENDPOINT, content=body, headers=_headers(body)).json()
    repeated = client.post(ENDPOINT, content=body, headers=_headers(body)).json()

    assert first["status"] == "PARTIAL"
    assert first["product_count"] == 50
    assert first["accepted_count"] == 49
    assert first["rejected_count"] == 1
    assert first["rejected_items"][0]["sku_interno"] == "PCH155349"
    assert first["rejected_items"][0]["reason"] == "ean_catalog_mismatch"
    assert repeated["duplicate"] is True
    assert repeated["rejected_items"] == first["rejected_items"]
    with sessions() as db:
        assert db.query(OfertaFarmacia).count() == 50
        assert db.query(HistoricoPreco).count() == 49
        audit = db.query(scraper_ingest.ScraperImportBatch).one()
        assert audit.status == "PARTIAL"
        assert len(audit.rejected_items) == 1


def test_partial_batch_reports_multiple_conflicts(ingest_client):
    client, sessions = ingest_client
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="33438250000167",
            razao_social="Drogarias Pacheco S.A.",
            nome_fantasia="Pacheco",
            endereco_completo="Loja online",
        )
        catalog = CatalogoBase(
            codigo_barras="7891721201806",
            name_search="Glifage XR 500mg",
            principio_ativo="metformina",
            laboratorio="Merck",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        db.add(
            OfertaFarmacia(
                sku_interno="PCH155349",
                preco=10.37,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
                contexto_chave="global",
                farmacia_id=pharmacy.id,
                catalogo_id=catalog.id,
            )
        )
        second_catalog = CatalogoBase(
            codigo_barras=None,
            name_search="Produto Pacheco 155352",
            principio_ativo="não informado",
            laboratorio="não informado",
            categorias=[],
        )
        db.add(second_catalog)
        db.flush()
        db.add(
            OfertaFarmacia(
                sku_interno="PCH155352",
                preco=8.0,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.drogariaspacheco.com.br/produto/155352/p",
                contexto_chave="global",
                farmacia_id=pharmacy.id,
                catalogo_id=second_catalog.id,
            )
        )
        db.commit()
    products = [
        {
            "id": str(product_id),
            "sku_interno": f"PCH{product_id}",
            "ean": ean,
            "name_search": f"Produto {product_id}",
            "preco": 11.0,
            "disponivel": True,
            "quantidade_estoque": 1,
            "link": url,
            "imagem_url": None,
            "fonte_observado_em": "2026-11-15T10:00:00-03:00",
        }
        for product_id, ean, url in [
            (
                155349,
                "7891721027468",
                "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
            ),
            (
                155352,
                None,
                "https://www.drogariaspacheco.com.br/produto/155352-alternativo/p",
            ),
            (
                155351,
                None,
                "https://www.drogariaspacheco.com.br/produto/155351/p",
            ),
        ]
    ]
    payload = {
        "batch_id": "e7791c70-28cc-49bd-9a65-152e1adfc350",
        "source": "pacheco",
        "observed_at": "2026-11-15T10:00:00-03:00",
        "products": products,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    result = client.post(ENDPOINT, content=body, headers=_headers(body)).json()

    assert result["status"] == "PARTIAL"
    assert result["accepted_count"] == 1
    assert result["rejected_count"] == 2
    assert {item["reason"] for item in result["rejected_items"]} == {
        "ean_catalog_mismatch",
        "sku_url_mismatch",
    }


def test_ingest_accepts_fifty_valid_products(ingest_client):
    client, sessions = ingest_client
    payload = {
        "batch_id": "f7791c70-28cc-49bd-9a65-152e1adfc350",
        "source": "pacheco",
        "observed_at": "2026-11-15T10:00:00-03:00",
        "products": [
            {
                "id": str(900000 + index),
                "sku_interno": f"PCH{900000 + index}",
                "ean": None,
                "name_search": f"Produto válido {index}",
                "preco": 12.0 + index,
                "disponivel": True,
                "quantidade_estoque": 1,
                "link": (
                    "https://www.drogariaspacheco.com.br/produto/"
                    f"{900000 + index}/p"
                ),
                "imagem_url": None,
                "fonte_observado_em": "2026-11-15T10:00:00-03:00",
            }
            for index in range(50)
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()

    response = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETE"
    assert response.json()["product_count"] == 50
    assert response.json()["accepted_count"] == 50
    assert response.json()["rejected_count"] == 0
    with sessions() as db:
        assert db.query(OfertaFarmacia).count() == 50


def test_failed_batch_retries_with_same_id_after_recovery(ingest_client, monkeypatch):
    client, sessions = ingest_client
    body = _body()
    calls = 0

    async def fail_once(_batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated persistence interruption")
        return {
            "persistence_errors": 0,
            "unassociated_products": 0,
            "catalog_items_created": 0,
            "offers_created": 0,
            "offers_updated": 1,
        }

    monkeypatch.setattr(scraper_ingest, "_persist_batch", fail_once)
    failed = client.post(ENDPOINT, content=body, headers=_headers(body))
    recovered = client.post(ENDPOINT, content=body, headers=_headers(body))

    assert failed.status_code == 503
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "COMPLETE"
    with sessions() as db:
        batch = db.query(scraper_ingest.ScraperImportBatch).one()
        assert batch.attempt_count == 2
        assert batch.status == "COMPLETE"
