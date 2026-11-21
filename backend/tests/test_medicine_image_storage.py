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


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (480, 640), (230, 220, 200)).save(output, format="PNG")
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
) -> bytes:
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected = "image/webp"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    else:
        detected = "image/jpeg"
    metadata = {
        "upload_id": upload_id or str(uuid4()),
        "source": "araujo",
        "sku": "ARJ101392",
        "ean": "7897705203095",
        "product_url": "https://www.araujo.com.br/produto/101392.html",
        "source_image_url": source_image_url,
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
    assert (
        client.post(
            IMAGE_UPLOAD_ENDPOINT, content=second, headers=_headers(second)
        ).status_code
        == 200
    )
    with sessions() as db:
        assert db.query(MedicineImageBlob).count() == 1
        assert db.query(MedicineImageLink).count() == 1
        assert db.query(MedicineImageUpload).count() == 2


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
