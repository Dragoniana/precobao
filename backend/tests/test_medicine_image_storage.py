from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models import Base, CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import (
    MedicineImageBlob,
    MedicineImageLink,
    MedicineImageUpload,
)
from app.services.medicine_images import normalize_medicine_image

SECRET = "test-collector-hmac-secret-with-more-than-32-bytes"
IMAGE_UPLOAD_ENDPOINT = "/api/v1/internal/medicine-images"
IMAGE_MIGRATION_CHECK_ENDPOINT = "/api/v1/internal/medicine-images/migration-check"


@pytest.fixture
def image_client(monkeypatch):
    postgres_url = os.getenv("SCRAPER_IMAGE_TEST_DATABASE_URL")
    if postgres_url:
        parsed = urlsplit(postgres_url)
        if (
            parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != "/preco_bao_image_test"
        ):
            raise RuntimeError(
                "SCRAPER_IMAGE_TEST_DATABASE_URL must target the local "
                "image-test database"
            )
        engine = create_engine(postgres_url, pool_pre_ping=True)
        with engine.connect() as connection:
            version = connection.exec_driver_sql("select version()").scalar_one()
            if not version.startswith("PostgreSQL 15."):
                raise RuntimeError("Image integration tests require PostgreSQL 15")
    else:
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
        "SCRAPER_IMAGE_INGEST_ALLOWED_SOURCES", "araujo,pacheco,indiana"
    )
    monkeypatch.setenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))
    monkeypatch.setenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="12345678000195",
            razao_social="Drogaria Araujo S.A.",
            nome_fantasia="Drogaria Araújo",
            endereco_completo="Belo Horizonte - MG",
        )
        catalog = CatalogoBase(
            codigo_barras="7897705203095",
            name_search="Medicamento de teste",
            principio_ativo="Substância teste",
            laboratorio="Laboratório teste",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        offer = OfertaFarmacia(
            sku_interno="ARJ101392",
            preco=12.50,
            quantidade_estoque=1,
            disponivel=True,
            url_origem="https://www.araujo.com.br/produto/101392.html",
            imagem_url="https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
            contexto_chave="global",
            farmacia_id=pharmacy.id,
            catalogo_id=catalog.id,
        )
        db.add(offer)
        db.commit()
        catalog_id = catalog.id

    try:
        yield TestClient(app, base_url="https://testserver"), sessions, catalog_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _image_bytes(color: tuple[int, int, int] = (230, 220, 200)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (480, 640), color).save(output, format="PNG")
    normalized = normalize_medicine_image(
        output.getvalue(), declared_content_type="image/png"
    )
    assert normalized.content_type in {"image/webp", "image/jpeg"}
    assert len(normalized.content) <= 512 * 1024
    return normalized.content


def _request_body(
    content: bytes,
    *,
    upload_id: str | None = None,
    source_image_url: str = "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
    content_type: str | None = None,
    source: str = "araujo",
    sku: str = "ARJ101392",
    ean: str | None = "7897705203095",
    product_url: str = "https://www.araujo.com.br/produto/101392.html",
    is_generic_fallback: bool = False,
    only_if_missing: bool = False,
    migration_mode: bool = False,
) -> bytes:
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    else:
        detected = "image/jpeg"
    metadata = {
        "upload_id": upload_id or str(uuid4()),
        "source": source,
        "sku": sku,
        "ean": ean,
        "product_url": product_url,
        "source_image_url": source_image_url,
        "is_generic_fallback": is_generic_fallback,
        "only_if_missing": only_if_missing,
        "migration_mode": migration_mode,
        "content_type": content_type or detected,
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }
    return (
        json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
        + content
    )


def _headers(
    body: bytes, *, valid: bool = True, secret: str = SECRET
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not valid:
        signature = "0" * 64
    return {
        "Content-Type": "application/vnd.preco-bao.image-upload",
        "X-Scraper-Timestamp": timestamp,
        "X-Scraper-Signature": f"sha256={signature}",
    }


def test_hmac_binary_upload_persists_bytea_deduplicates_and_serves_with_etag(
    image_client,
):
    client, sessions, catalog_id = image_client
    content = _image_bytes()
    request_id = str(uuid4())
    body = _request_body(content, upload_id=request_id)

    first = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))
    duplicate = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))

    assert first.status_code == 200
    assert first.json()["status"] == "STORED"
    assert first.json()["size_bytes"] <= 512 * 1024
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True

    with sessions() as db:
        blob = db.execute(select(MedicineImageBlob)).scalar_one()
        link = db.execute(select(MedicineImageLink)).scalar_one()
        assert blob.image_data == content
        assert hashlib.sha256(blob.image_data).hexdigest() == blob.sha256
        assert len(blob.image_data) <= 512 * 1024
        stored_content = blob.image_data
        stored_sha256 = blob.sha256
        assert link.catalog_id == catalog_id
        assert link.sku == "ARJ101392"
        assert link.ean == "7897705203095"
        assert link.is_primary is True
        assert db.query(MedicineImageUpload).count() == 1

    catalog = client.get(f"/catalogo/medicamentos/{catalog_id}")
    assert catalog.status_code == 200
    assert catalog.json()["imagem_armazenada"] is True
    assert catalog.json()["imagem_ref"].startswith(
        f"/catalogo/medicamentos/{catalog_id}/imagem?v="
    )
    assert "image_data" not in catalog.json()

    image_response = client.get(f"/catalogo/medicamentos/{catalog_id}/imagem")
    assert image_response.status_code == 200
    assert image_response.content == stored_content
    assert image_response.headers["content-type"] in {"image/webp", "image/jpeg"}
    assert image_response.headers["etag"] == f'"{stored_sha256}"'
    assert "max-age=86400" in image_response.headers["cache-control"]
    cached = client.get(
        f"/catalogo/medicamentos/{catalog_id}/imagem",
        headers={"If-None-Match": image_response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""

    container = os.getenv("SCRAPER_IMAGE_TEST_PG_CONTAINER")
    if container:
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "dropdb",
                "-U",
                "postgres",
                "--if-exists",
                "image_restore_check",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "pg_dump",
                "-U",
                "postgres",
                "-Fc",
                "-d",
                "preco_bao_image_test",
                "-f",
                "/tmp/medicine-image-test.dump",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "createdb",
                "-U",
                "postgres",
                "image_restore_check",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "pg_restore",
                "-U",
                "postgres",
                "--no-owner",
                "--no-acl",
                "-d",
                "image_restore_check",
                "/tmp/medicine-image-test.dump",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        restored = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "postgres",
                "-d",
                "image_restore_check",
                "-At",
                "-c",
                "select encode(image_data, 'hex') from medicine_image_blobs",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        restored_bytes = bytes.fromhex(restored.stdout.strip())
        assert restored_bytes == stored_content
        assert hashlib.sha256(restored_bytes).hexdigest() == stored_sha256


def test_image_endpoint_uses_the_shared_secret_file(
    image_client, monkeypatch, tmp_path
):
    client, _sessions, _catalog_id = image_client
    file_secret = "image-route-file-secret-with-at-least-32-bytes"
    secret_file = tmp_path / "scraper-hmac"
    secret_file.write_text(file_secret, encoding="utf-8")
    monkeypatch.setenv("SCRAPER_INGEST_HMAC_SECRET_FILE", str(secret_file))
    body = _request_body(_image_bytes())

    response = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body, secret=file_secret)
    )

    assert response.status_code == 200


def test_same_bytes_with_new_upload_id_reuse_one_blob(image_client):
    client, sessions, _catalog_id = image_client
    content = _image_bytes()
    first = _request_body(content)
    second = _request_body(content)

    assert (
        client.post(
            IMAGE_UPLOAD_ENDPOINT, content=first, headers=_headers(first)
        ).status_code
        == 200
    )
    duplicate = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=second, headers=_headers(second)
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "SKIPPED_EXISTING_IMAGE"
    with sessions() as db:
        assert db.query(MedicineImageBlob).count() == 1
        assert db.query(MedicineImageLink).count() == 1
        assert db.query(MedicineImageUpload).count() == 1


def test_upload_never_replaces_existing_catalog_image(image_client):
    client, sessions, catalog_id = image_client
    original = _image_bytes()
    replacement = _image_bytes((15, 90, 180))
    first_body = _request_body(original)
    first = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=first_body, headers=_headers(first_body)
    )
    assert first.status_code == 200
    original_sha = first.json()["sha256"]

    replacement_body = _request_body(replacement)
    response = client.post(
        IMAGE_UPLOAD_ENDPOINT,
        content=replacement_body,
        headers=_headers(replacement_body),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SKIPPED_EXISTING_IMAGE"
    assert response.json()["confirmed_existing_image"] is True
    assert response.json()["sha256"] == original_sha
    assert response.json()["catalog_id"] == catalog_id

    with sessions() as db:
        blobs = db.execute(select(MedicineImageBlob)).scalars().all()
        links = db.execute(select(MedicineImageLink)).scalars().all()
        assert len(blobs) == 1
        assert blobs[0].image_data == original
        assert len(links) == 1


def test_migration_check_treats_legacy_external_url_as_candidate_and_is_read_only(
    image_client,
):
    client, sessions, catalog_id = image_client
    content = _image_bytes()
    candidate = {
        "source": "araujo",
        "sku": "ARJ101392",
        "ean": "7897705203095",
        "product_url": "https://www.araujo.com.br/produto/101392.html",
        "source_image_url": "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
        "content_type": "image/webp",
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "is_generic_fallback": False,
    }
    body = json.dumps({"candidates": [candidate]}, separators=(",", ":")).encode()
    rejected = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT,
        content=body,
        headers=_headers(body, valid=False),
    )
    assert rejected.status_code == 401
    response = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT, content=body, headers=_headers(body)
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "ELIGIBLE"
    assert response.json()["results"][0]["catalog_id"] == catalog_id
    with sessions() as db:
        assert db.query(MedicineImageBlob).count() == 0
        assert db.query(MedicineImageLink).count() == 0
        assert db.query(OfertaFarmacia).count() == 1

    upload_body = _request_body(content, migration_mode=True, only_if_missing=True)
    uploaded = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=upload_body, headers=_headers(upload_body)
    )
    assert uploaded.status_code == 200
    stored_check = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT, content=body, headers=_headers(body)
    )
    assert stored_check.status_code == 200
    assert stored_check.json()["results"][0]["status"] == "IMAGE_ALREADY_EXISTS"


def test_indiana_migration_image_requires_its_own_source_identity(
    image_client, monkeypatch
):
    client, sessions, _catalog_id = image_client
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="12345678000276",
            razao_social="Farmácia Indiana S.A.",
            nome_fantasia="Farmácia Indiana",
            endereco_completo="Governador Valadares - MG",
        )
        catalog = CatalogoBase(
            codigo_barras="7896714231143",
            name_search="Medicamento Indiana de teste",
            principio_ativo="Substância teste",
            laboratorio="Laboratório teste",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        db.add(
            OfertaFarmacia(
                sku_interno="IND12345",
                preco=10.25,
                quantidade_estoque=1,
                disponivel=True,
                url_origem="https://www.farmaciaindiana.com.br/produto/ind12345",
                imagem_url="https://farmaciaindiana.vteximg.com.br/arquivos/7896714231143.jpg",
                contexto_chave="global",
                farmacia_id=pharmacy.id,
                catalogo_id=catalog.id,
            )
        )
        db.commit()

    content = _image_bytes((55, 130, 210))
    candidate = {
        "source": "indiana",
        "sku": "IND12345",
        "ean": "7896714231143",
        "product_url": "https://www.farmaciaindiana.com.br/produto/ind12345",
        "source_image_url": "https://farmaciaindiana.vteximg.com.br/arquivos/7896714231143.jpg",
        "content_type": "image/webp",
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "is_generic_fallback": False,
    }
    check_body = json.dumps({"candidates": [candidate]}, separators=(",", ":")).encode()
    monkeypatch.setenv("SCRAPER_IMAGE_INGEST_ALLOWED_SOURCES", "araujo,pacheco")
    checked = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT,
        content=check_body,
        headers=_headers(check_body),
    )
    assert checked.status_code == 200
    assert checked.json()["results"][0]["status"] == "SOURCE_NOT_ENABLED"

    monkeypatch.setenv(
        "SCRAPER_IMAGE_INGEST_ALLOWED_SOURCES", "araujo,pacheco,indiana"
    )
    checked = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT,
        content=check_body,
        headers=_headers(check_body),
    )
    assert checked.status_code == 200
    assert checked.json()["results"][0]["status"] == "ELIGIBLE"

    upload_body = _request_body(
        content,
        source="indiana",
        sku="IND12345",
        ean="7896714231143",
        product_url="https://www.farmaciaindiana.com.br/produto/ind12345",
        source_image_url="https://farmaciaindiana.vteximg.com.br/arquivos/7896714231143.jpg",
        migration_mode=True,
        only_if_missing=True,
    )
    uploaded = client.post(
        IMAGE_UPLOAD_ENDPOINT,
        content=upload_body,
        headers=_headers(upload_body),
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "STORED"
    with sessions() as db:
        link = db.execute(
            select(MedicineImageLink).where(MedicineImageLink.source == "indiana")
        ).scalar_one()
        blob = db.get(MedicineImageBlob, link.blob_id)
        assert blob is not None and blob.image_data == content
        assert db.query(OfertaFarmacia).count() == 2
        assert db.query(CatalogoBase).count() == 2


def test_migration_upload_maps_by_ean_without_changing_offer(image_client):
    client, sessions, catalog_id = image_client
    with sessions() as db:
        offer = db.execute(select(OfertaFarmacia)).scalar_one()
        offer.imagem_url = None
        original_price = offer.preco
        original_available = offer.disponivel
        original_offer_count = db.query(OfertaFarmacia).count()
        db.commit()
    content = _image_bytes()
    body = _request_body(content, migration_mode=True, only_if_missing=True)
    response = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))
    assert response.status_code == 200
    assert response.json()["status"] == "STORED"
    assert response.json()["catalog_id"] == catalog_id
    with sessions() as db:
        offer = db.execute(select(OfertaFarmacia)).scalar_one()
        assert offer.preco == original_price
        assert offer.disponivel is original_available
        assert db.query(OfertaFarmacia).count() == original_offer_count
        link = db.execute(select(MedicineImageLink)).scalar_one()
        assert link.catalog_id == catalog_id


def test_migration_allows_real_photo_when_only_generic_fallback_exists(image_client):
    client, sessions, catalog_id = image_client
    generic_content = _image_bytes((90, 150, 210))
    generic_url = (
        "https://www.araujo.com.br/on/demandware.static/Sites-Araujo-Site/"
        "-/default/dw4f56ef98/images/generico.webp"
    )
    with sessions() as db:
        offer = db.execute(select(OfertaFarmacia)).scalar_one()
        pharmacy = db.execute(select(Farmacia)).scalar_one()
        generic_blob = MedicineImageBlob(
            sha256=hashlib.sha256(generic_content).hexdigest(),
            content_type="image/webp"
            if generic_content.startswith(b"RIFF")
            else "image/jpeg",
            image_data=generic_content,
            size_bytes=len(generic_content),
            width=480,
            height=640,
        )
        db.add(generic_blob)
        db.flush()
        db.add(
            MedicineImageLink(
                blob_id=generic_blob.id,
                catalog_id=None,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id,
                sku="ARJ101392",
                ean="7897705203095",
                source="araujo",
                source_image_url=generic_url,
                is_primary=False,
                is_generic=True,
            )
        )
        db.commit()

    content = _image_bytes((230, 220, 200))
    upload_body = _request_body(content, migration_mode=True, only_if_missing=True)
    metadata = json.loads(upload_body.split(b"\n", 1)[0])
    candidate = {
        key: metadata[key]
        for key in (
            "source",
            "sku",
            "ean",
            "product_url",
            "source_image_url",
            "content_type",
            "content_sha256",
            "is_generic_fallback",
        )
    }
    check_body = json.dumps(
        {"candidates": [candidate]}, separators=(",", ":"), sort_keys=True
    ).encode()
    checked = client.post(
        IMAGE_MIGRATION_CHECK_ENDPOINT,
        content=check_body,
        headers=_headers(check_body),
    )
    assert checked.status_code == 200
    assert checked.json()["results"][0]["status"] == "ELIGIBLE"

    uploaded = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=upload_body, headers=_headers(upload_body)
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "STORED"
    assert uploaded.json()["catalog_id"] == catalog_id
    with sessions() as db:
        links = db.execute(select(MedicineImageLink)).scalars().all()
        assert len(links) == 2
        assert sum(link.is_generic for link in links) == 1
        assert sum(link.catalog_id == catalog_id for link in links) == 1


def test_image_upload_rejects_bad_hmac_and_non_image_content(image_client):
    client, sessions, _catalog_id = image_client
    content = _image_bytes()
    body = _request_body(content)
    assert (
        client.post(
            IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body, valid=False)
        ).status_code
        == 401
    )

    invalid = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    invalid_body = _request_body(invalid, content_type="image/webp")
    response = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=invalid_body, headers=_headers(invalid_body)
    )
    assert response.status_code == 422
    with sessions() as db:
        assert db.query(MedicineImageBlob).count() == 0


def test_generic_image_with_divergent_ean_is_offer_scoped_and_served_as_fallback(
    image_client,
):
    client, sessions, _catalog_id = image_client
    content = _image_bytes()
    specific_body = _request_body(content)
    specific = client.post(
        IMAGE_UPLOAD_ENDPOINT,
        content=specific_body,
        headers=_headers(specific_body),
    )
    assert specific.status_code == 200
    assert specific.json()["is_generic"] is False
    source_url = "https://i3-imagens-prd.araujo.com.br/128666_7896026306751_1.webp"
    body = _request_body(
        content,
        source_image_url=source_url,
        is_generic_fallback=True,
    )

    response = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))

    assert response.status_code == 200
    assert response.json()["is_generic"] is True
    assert response.json()["catalog_id"] is None
    assert response.json()["primary"] is False
    with sessions() as db:
        link = db.execute(
            select(MedicineImageLink).where(MedicineImageLink.is_generic.is_(True))
        ).scalar_one()
        assert link.ean == "7897705203095"
        assert link.source_image_url == source_url
        assert link.catalog_id is None
        assert link.offer_id is not None
        assert link.is_primary is False
        specific_link = db.execute(
            select(MedicineImageLink).where(
                MedicineImageLink.offer_id == link.offer_id,
                MedicineImageLink.is_generic.is_(False),
            )
        ).scalar_one()
        assert specific_link.catalog_id == _catalog_id
        assert specific_link.is_primary is True
        catalog = client.get(f"/catalogo/medicamentos/{_catalog_id}").json()
        assert catalog["imagem_armazenada"] is True
        assert catalog["imagem_ref"].startswith(
            f"/catalogo/medicamentos/{_catalog_id}/imagem?v="
        )
        assert catalog["ofertas"][0]["imagem_ref"].startswith(
            f"/catalogo/ofertas/{link.offer_id}/imagem?v="
        )
        assert catalog["ofertas"][0]["imagem_generica"] is True
        assert "image_data" not in json.dumps(catalog)
    image = client.get(catalog["ofertas"][0]["imagem_ref"].split("?", 1)[0])
    assert image.status_code == 200
    assert image.content == content
    assert image.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'
    assert (
        client.get(
            image.request.url.path,
            headers={"If-None-Match": image.headers["etag"]},
        ).status_code
        == 304
    )


def test_pacheco_generic_upload_allows_ean_conflict_without_catalog_association(
    image_client,
):
    client, sessions, _catalog_id = image_client
    with sessions() as db:
        pharmacy = Farmacia(
            cnpj="98765432000198",
            razao_social="Drogarias Pacheco S.A.",
            nome_fantasia="Drogarias Pacheco",
            endereco_completo="Rio de Janeiro - RJ",
        )
        catalog = db.execute(
            select(CatalogoBase).where(CatalogoBase.codigo_barras == "7897705203095")
        ).scalar_one()
        offer = OfertaFarmacia(
            sku_interno="PCH155349",
            preco=18.00,
            quantidade_estoque=1,
            disponivel=True,
            url_origem="https://www.drogariaspacheco.com.br/produto/155349/p",
            imagem_url=None,
            contexto_chave="global",
            farmacia=pharmacy,
            catalogo_id=catalog.id,
        )
        db.add(offer)
        db.flush()
        offer_id = offer.id
        db.commit()
    source_url = (
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/1138787/"
        "de-referencia-tarja-vermelha-em-comprimido-pacheco.jpg"
    )
    body = _request_body(
        _image_bytes(),
        source="pacheco",
        sku="PCH155349",
        ean="7891721027468",
        product_url="https://www.drogariaspacheco.com.br/produto/155349/p",
        source_image_url=source_url,
        is_generic_fallback=True,
    )
    response = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))
    assert response.status_code == 200
    assert response.json()["offer_id"] == offer_id
    assert response.json()["catalog_id"] is None
    with sessions() as db:
        link = db.execute(
            select(MedicineImageLink).where(
                MedicineImageLink.offer_id == offer_id,
                MedicineImageLink.is_generic.is_(True),
            )
        ).scalar_one()
        assert link.ean == "7891721027468"
        assert link.catalog_id is None
        assert db.query(MedicineImageBlob).count() == 1
        assert db.query(MedicineImageLink).count() == 1


def test_generic_flag_cannot_bypass_identity_for_a_specific_image(image_client):
    client, sessions, _catalog_id = image_client
    with sessions() as db:
        offer = db.execute(select(OfertaFarmacia)).scalar_one()
        product_url = offer.url_origem
        sku = offer.sku_interno
    body = _request_body(
        _image_bytes(),
        sku=sku,
        product_url=product_url,
        ean="7891721027468",
        is_generic_fallback=True,
    )
    response = client.post(IMAGE_UPLOAD_ENDPOINT, content=body, headers=_headers(body))
    assert response.status_code == 422
    assert response.json()["detail"] == "generic_image_not_recognized"
    with sessions() as db:
        assert db.query(MedicineImageLink).count() == 0


def test_upload_id_cannot_be_replayed_with_different_payload(image_client):
    client, _sessions, _catalog_id = image_client
    content = _image_bytes()
    request_id = str(uuid4())
    first = _request_body(content, upload_id=request_id)
    changed = _request_body(
        content,
        upload_id=request_id,
        source_image_url="https://i3-imagens-prd.araujo.com.br/101392/7897705203095-v2.jpg",
    )

    assert (
        client.post(
            IMAGE_UPLOAD_ENDPOINT, content=first, headers=_headers(first)
        ).status_code
        == 200
    )
    conflict = client.post(
        IMAGE_UPLOAD_ENDPOINT, content=changed, headers=_headers(changed)
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "image_upload_id_payload_conflict"
