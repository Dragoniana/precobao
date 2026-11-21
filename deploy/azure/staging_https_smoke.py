"""Guarded HTTPS/HMAC smoke test for a disposable scraper-ingestion staging stack."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import text

sys.path.insert(0, "/app")

from app.core.database import engine
from app.scrapers import scraper_batch_client
from app.scrapers.scraper_batch_client import post_batch, post_image_upload
from app.services.medicine_images import normalize_medicine_image
from app.services.scraper_hmac import (
    HMACSecretConfigurationError,
    get_scraper_hmac_secret,
)


HOST = "staging.precobao.test"
BATCH_URL = f"https://{HOST}/backend/api/v1/internal/scraper-batches"
IMAGE_URL = f"https://{HOST}/backend/api/v1/internal/medicine-images"
OUTBOX = Path(os.getenv("STAGING_OUTBOX_DIR", "/run/staging-outbox"))
SOURCES = {
    "araujo": {
        "id": "990000001",
        "sku": "ARJ990000001",
        "link": "https://www.araujo.com.br/produto/990000001-homologacao.html",
        "image": "https://i3-imagens-prd.araujo.com.br/990000001/staging.jpg",
    },
    "pacheco": {
        "id": "990000002",
        "sku": "PCH990000002",
        "link": "https://www.drogariaspacheco.com.br/produto/990000002-homologacao.html",
        "image": "https://drogariaspacheco.vteximg.com.br/arquivos/staging.jpg",
    },
}


def _guard() -> None:
    if os.getenv("STAGING_WRITES_CONFIRMED") != "isolated-copy-only":
        raise SystemExit("Refusing writes: explicit isolated-staging confirmation is missing.")
    database = urlsplit(os.getenv("DATABASE_URL", ""))
    if database.hostname != "restore-db" or database.path != "/preco_bao_staging":
        raise SystemExit("Refusing writes: DATABASE_URL is not the isolated restore database.")
    if os.getenv("SCRAPER_INGEST_URL") != BATCH_URL:
        raise SystemExit("Refusing requests: endpoint must be the private HTTPS staging hostname.")
    try:
        os.environ["SCRAPER_INGEST_HMAC_SECRET"] = get_scraper_hmac_secret().decode("utf-8")
    except (HMACSecretConfigurationError, UnicodeDecodeError) as error:
        raise SystemExit("Staging HMAC secret is missing or unusable.") from error


def _batch(source: str) -> dict[str, object]:
    item = SOURCES[source]
    return {
        "batch_id": str(uuid4()),
        "source": source,
        "observed_at": datetime.now(UTC).isoformat(),
        "products": [
            {
                "id": item["id"],
                "sku_interno": item["sku"],
                "ean": None,
                "name_search": f"Produto sintético de homologação {source} 99000000",
                "preco": 31.25 if source == "araujo" else 42.75,
                "disponivel": True,
                "quantidade_estoque": 1,
                "link": item["link"],
                "imagem_url": item["image"],
                "fonte_observado_em": datetime.now(UTC).isoformat(),
            }
        ],
    }


def _write_marker(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _image_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (36, 48), (40, 112, 190)).save(output, format="PNG")
    return normalize_medicine_image(
        output.getvalue(), declared_content_type="image/png", max_bytes=512 * 1024
    ).content


async def _test_simulated_timeout_retry() -> None:
    attempts = 0
    original_sleep = scraper_batch_client._sleep

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("simulated staging transport timeout")
        return httpx.Response(200, json={"status": "COMPLETE"})

    scraper_batch_client._sleep = no_wait
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await post_batch(client, {"batch_id": str(uuid4())})
        assert result["status"] == "COMPLETE" and attempts == 2
    finally:
        scraper_batch_client._sleep = original_sleep


async def _https_client() -> httpx.AsyncClient:
    ca_file = os.getenv("SSL_CERT_FILE", "")
    if not ca_file or not Path(ca_file).is_file():
        raise SystemExit("Trusted staging CA file is missing; certificate verification stays enabled.")
    context = ssl.create_default_context(cafile=ca_file)
    return httpx.AsyncClient(verify=context, timeout=httpx.Timeout(12.0))


async def _initial() -> None:
    if any(OUTBOX.glob("*.json")):
        raise SystemExit("Staging outbox already has pending markers; refusing to overwrite them.")
    await _test_simulated_timeout_retry()
    OUTBOX.mkdir(parents=True, exist_ok=True)

    batches = {source: _batch(source) for source in SOURCES}
    image_content = _image_bytes()
    image_requests: dict[str, dict[str, object]] = {}
    for source, item in SOURCES.items():
        metadata = {
            "upload_id": str(uuid4()),
            "source": source,
            "sku": item["sku"],
            "ean": None,
            "product_url": item["link"],
            "source_image_url": item["image"],
            "content_type": "image/webp" if image_content.startswith(b"RIFF") else "image/jpeg",
            "content_sha256": hashlib.sha256(image_content).hexdigest(),
        }
        image_requests[source] = {
            "metadata": metadata,
            "content_b64": base64.b64encode(image_content).decode("ascii"),
        }

    for source, batch in batches.items():
        _write_marker(OUTBOX / f"batch-{source}.json", batch)
    for source, payload in image_requests.items():
        _write_marker(OUTBOX / f"image-{source}.json", payload)

    with engine.connect() as connection:
        starting_batches = connection.execute(
            text("SELECT count(*) FROM preco_bao_app.scraper_import_batches")
        ).scalar_one()
    async with await _https_client() as client:
        health = await client.get(f"https://{HOST}/backend/health/ready")
        assert health.status_code == 200 and health.json().get("status") == "ready"

        invalid_body = json.dumps(batches["araujo"], separators=(",", ":")).encode()
        invalid_headers = scraper_batch_client._signed_headers(
            invalid_body, "deliberately-invalid-staging-signature"
        )
        invalid = await client.post(BATCH_URL, content=invalid_body, headers=invalid_headers)
        assert invalid.status_code == 401

        for source, batch in batches.items():
            result = await post_batch(client, batch)
            assert result.get("status") == "COMPLETE" and not result.get("duplicate")
            # Keep the durable marker even though this harness discards the receipt.

        for source, payload in image_requests.items():
            content = base64.b64decode(payload["content_b64"])
            result = await post_image_upload(client, payload["metadata"], content)
            assert result.get("status") == "STORED" and not result.get("duplicate")

        with engine.connect() as connection:
            batch_count = connection.execute(text("SELECT count(*) FROM preco_bao_app.scraper_import_batches")).scalar_one()
            deduplicated_blob_count = connection.execute(
                text("SELECT count(*) FROM preco_bao_app.medicine_image_blobs WHERE sha256=:sha256"),
                {"sha256": hashlib.sha256(image_content).hexdigest()},
            ).scalar_one()
            linked_test_offers = connection.execute(
                text("SELECT count(*) FROM preco_bao_app.medicine_image_links WHERE sku IN (:araujo,:pacheco)"),
                {"araujo": SOURCES["araujo"]["sku"], "pacheco": SOURCES["pacheco"]["sku"]},
            ).scalar_one()
            test_uploads = connection.execute(
                text("SELECT count(*) FROM preco_bao_app.medicine_image_uploads WHERE request_id IN (:araujo,:pacheco)"),
                {"araujo": image_requests["araujo"]["metadata"]["upload_id"], "pacheco": image_requests["pacheco"]["metadata"]["upload_id"]},
            ).scalar_one()
            assert batch_count == starting_batches + 2
            assert deduplicated_blob_count == 1 and linked_test_offers == 2 and test_uploads == 2
            catalog_ids = {
                row[0]: row[1]
                for row in connection.execute(
                    text("SELECT sku_interno, catalogo_id FROM preco_bao_app.ofertas_farmacia WHERE sku_interno IN (:araujo, :pacheco)"),
                    {"araujo": SOURCES["araujo"]["sku"], "pacheco": SOURCES["pacheco"]["sku"]},
                )
            }
        assert set(catalog_ids) == {item["sku"] for item in SOURCES.values()}
        for catalog_id in catalog_ids.values():
            response = await client.get(f"https://{HOST}/backend/catalogo/medicamentos/{catalog_id}/imagem")
            assert response.status_code == 200 and response.headers.get("etag")
            conditional = await client.get(
                f"https://{HOST}/backend/catalogo/medicamentos/{catalog_id}/imagem",
                headers={"If-None-Match": response.headers["etag"]},
            )
            assert conditional.status_code == 304
    print("TLS-valid HTTPS, HMAC rejection/acceptance, two-source ingestion, BYTEA dedupe, ETag and timeout-retry simulation passed.")
    print("Outbox markers were intentionally retained for the post-restart replay phase.")


async def _replay() -> None:
    markers = sorted(OUTBOX.glob("*.json"))
    if len(markers) != 4:
        raise SystemExit(f"Expected four durable staging outbox markers, found {len(markers)}.")
    async with await _https_client() as client:
        for marker in markers:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if marker.name.startswith("batch-"):
                result = await post_batch(client, payload)
                assert result.get("duplicate") is True
            else:
                content = base64.b64decode(payload["content_b64"])
                result = await post_image_upload(client, payload["metadata"], content)
                assert result.get("duplicate") is True
            marker.unlink()
    assert not list(OUTBOX.glob("*.json"))
    print("All four durable batch/image outbox markers replayed after the API restart as idempotent duplicates.")


async def main() -> None:
    _guard()
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("initial", "replay"), required=True)
    args = parser.parse_args()
    if args.phase == "initial":
        await _initial()
    else:
        await _replay()


if __name__ == "__main__":
    asyncio.run(main())
