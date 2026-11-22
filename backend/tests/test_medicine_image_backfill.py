from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models import Base, CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.medicine_images import normalize_medicine_image

SECRET = "test-image-backfill-hmac-secret-with-more-than-32-bytes"
INVENTORY = "/api/v1/internal/medicine-images/backfill-inventory"
QUEUE = "/api/v1/internal/medicine-images/backfill-queue"


@pytest.fixture
def backfill_client(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET", SECRET)
    monkeypatch.delenv("SCRAPER_INGEST_HMAC_SECRET_FILE", raising=False)
    monkeypatch.setenv("SCRAPER_INGEST_ALLOWED_SOURCES", "araujo,pacheco")
    monkeypatch.setenv(
        "IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "image-references.json")
    )

    with sessions() as db:
        araujo = Farmacia(
            cnpj="12345678000195",
            razao_social="Araujo S.A.",
            nome_fantasia="Drogaria Araújo",
            endereco_completo="Belo Horizonte - MG",
        )
        pacheco = Farmacia(
            cnpj="98765432000198",
            razao_social="Pacheco S.A.",
            nome_fantasia="Drogarias Pacheco",
            endereco_completo="Rio de Janeiro - RJ",
        )
        missing = CatalogoBase(
            codigo_barras="7897705203095",
            name_search="Produto sem imagem",
            principio_ativo="Substância",
            laboratorio="Laboratório",
            categorias=[],
        )
        stored = CatalogoBase(
            codigo_barras="7896023703583",
            name_search="Produto com imagem",
            principio_ativo="Substância",
            laboratorio="Laboratório",
            categorias=[],
        )
        db.add_all([araujo, pacheco, missing, stored])
        db.flush()
        missing_offers = [
            OfertaFarmacia(
                sku_interno="ARJ101392",
                preco=12.50,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.araujo.com.br/produto/101392.html",
                imagem_url="https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
                contexto_chave="global",
                farmacia_id=araujo.id,
                catalogo_id=missing.id,
            ),
            OfertaFarmacia(
                sku_interno="PCH155349",
                preco=14.50,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.drogariaspacheco.com.br/produto/155349",
                imagem_url="https://drogariaspacheco.vteximg.com.br/arquivos/7897705203095.webp",
                contexto_chave="global",
                farmacia_id=pacheco.id,
                catalogo_id=missing.id,
            ),
            OfertaFarmacia(
                sku_interno="PCH155350",
                preco=15.50,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.drogariaspacheco.com.br/produto/155350",
                imagem_url=None,
                contexto_chave="global",
                farmacia_id=pacheco.id,
                catalogo_id=stored.id,
            ),
        ]
        db.add_all(missing_offers)
        db.flush()
        from io import BytesIO

        image_io = BytesIO()
        Image.new("RGB", (80, 100), (10, 20, 30)).save(image_io, format="PNG")
        normalized = normalize_medicine_image(
            image_io.getvalue(), declared_content_type="image/png"
        )
        blob = MedicineImageBlob(
            sha256=normalized.sha256,
            content_type=normalized.content_type,
            image_data=normalized.content,
            size_bytes=len(normalized.content),
            width=normalized.width,
            height=normalized.height,
        )
        db.add(blob)
        db.flush()
        db.add(
            MedicineImageLink(
                blob_id=blob.id,
                catalog_id=stored.id,
                pharmacy_id=pacheco.id,
                offer_id=missing_offers[2].id,
                sku="PCH155350",
                ean=stored.codigo_barras,
                source="pacheco",
                source_image_url="https://drogariaspacheco.vteximg.com.br/arquivos/7896023703583.webp",
                is_primary=True,
                is_generic=False,
            )
        )
        db.commit()
        missing_id = missing.id
        stored_id = stored.id

    try:
        yield (
            TestClient(app, base_url="https://testserver"),
            sessions,
            missing_id,
            stored_id,
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _headers(body: bytes, valid: bool = True) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not valid:
        signature = "0" * 64
    return {
        "X-Scraper-Timestamp": timestamp,
        "X-Scraper-Signature": f"sha256={signature}",
        "Content-Type": "application/json",
    }


def test_backfill_queue_is_hmac_paged_and_groups_sources(backfill_client):
    client, _sessions, missing_id, stored_id = backfill_client
    body = json.dumps(
        {"after_catalog_id": 0, "limit": 100}, separators=(",", ":")
    ).encode()
    response = client.post(QUEUE, content=body, headers=_headers(body))
    assert response.status_code == 200
    entries = response.json()["items"]
    missing = next(item for item in entries if item["catalog_id"] == missing_id)
    assert missing["status"] == "EXTERNAL_URL_CANDIDATE"
    assert missing["eligible_for_backfill"] is True
    assert {item["source"] for item in missing["sources"]} == {"araujo", "pacheco"}
    assert [item["catalog_id"] for item in entries] == [missing_id]
    assert stored_id not in [item["catalog_id"] for item in entries]


def test_backfill_endpoints_reject_invalid_hmac(backfill_client):
    client, _sessions, _missing_id, _stored_id = backfill_client
    body = b"{}"
    response = client.post(INVENTORY, content=body, headers=_headers(body, valid=False))
    assert response.status_code == 401


def test_inventory_reports_bytea_and_url_candidate_categories(backfill_client):
    client, _sessions, _missing_id, _stored_id = backfill_client
    body = b"{}"
    response = client.post(INVENTORY, content=body, headers=_headers(body))
    assert response.status_code == 200
    result = response.json()
    assert result["catalog_total"] == 2
    assert result["status_counts"]["EXTERNAL_URL_CANDIDATE"] == 1
    assert result["status_counts"]["BYTEA_REAL_VALID"] == 1
    assert result["source_counts"] == {
        "araujo": {"EXTERNAL_URL_CANDIDATE": 1},
        "pacheco": {"EXTERNAL_URL_CANDIDATE": 1},
    }
