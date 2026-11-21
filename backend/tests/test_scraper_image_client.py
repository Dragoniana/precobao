from __future__ import annotations

import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.scrapers import scraper_image_client
from app.scrapers.scraper_batch_client import BatchSyncError


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 48), (220, 210, 200)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_image_outbox_retries_same_binary_and_upload_id(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    source_url = "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg"
    product = {
        "sku_interno": "ARJ101392",
        "ean": "7897705203095",
        "link": "https://www.araujo.com.br/produto/101392.html",
        "imagem_url": source_url,
    }
    image = _png()

    async def image_response(_request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=image)

    attempts: list[tuple[dict, bytes]] = []

    async def upload(_client, metadata, content):
        attempts.append((metadata, content))
        if len(attempts) == 1:
            raise BatchSyncError("network_interrupted")
        return {"status": "STORED"}

    monkeypatch.setattr(scraper_image_client, "post_image_upload", upload)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(image_response)
    ) as client:
        with pytest.raises(BatchSyncError, match="network_interrupted"):
            await scraper_image_client.download_and_upload_product_image(
                client, "araujo", product
            )

        outbox = tmp_path / "outbox" / "images" / "araujo"
        manifest = next(outbox.glob("*.json"))
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        image_path = manifest.with_suffix(".bin")
        saved_content = image_path.read_bytes()
        assert metadata["upload_id"]
        assert metadata["content_sha256"]
        assert len(saved_content) <= 512 * 1024

        assert (
            await scraper_image_client.sync_pending_image_outbox(client, "araujo") == 1
        )

    assert attempts[0][0] == attempts[1][0] == metadata
    assert attempts[0][1] == attempts[1][1] == saved_content
    assert not manifest.exists()
    assert not image_path.exists()


@pytest.mark.asyncio
async def test_image_outbox_quarantines_orphaned_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    outbox = tmp_path / "outbox" / "images" / "pacheco"
    outbox.mkdir(parents=True)
    orphan = outbox / "interrupted.bin"
    orphan.write_bytes(b"partial pre-manifest data")

    async with httpx.AsyncClient() as client:
        assert (
            await scraper_image_client.sync_pending_image_outbox(client, "pacheco") == 0
        )
    quarantined = list((outbox / "quarantine" / "orphaned").glob("*.bin"))
    assert not orphan.exists()
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"partial pre-manifest data"


@pytest.mark.asyncio
async def test_http_409_keeps_pending_image_for_manual_review(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    outbox = tmp_path / "outbox" / "images" / "pacheco"
    outbox.mkdir(parents=True)
    manifest = outbox / "pending.json"
    content = outbox / "pending.bin"
    metadata = {
        "upload_id": "5ea270b4-3985-432d-a5a5-ada953993ca2",
        "source": "pacheco",
        "sku": "PCH155349",
        "ean": "7891721027468",
        "product_url": "https://www.drogariaspacheco.com.br/produto/155349/p",
        "source_image_url": "https://images.example.test/pacheco-155349.jpg",
    }
    manifest.write_text(json.dumps(metadata), encoding="utf-8")
    content.write_bytes(b"pending image bytes")
    attempts = []

    async def conflict(_client, sent_metadata, sent_content):
        attempts.append((sent_metadata, sent_content))
        raise BatchSyncError("scraper_image_upload_rejected:409")

    monkeypatch.setattr(scraper_image_client, "post_image_upload", conflict)
    async with httpx.AsyncClient() as client:
        with pytest.raises(BatchSyncError, match="rejected:409"):
            await scraper_image_client.sync_pending_image_outbox(client, "pacheco")

    assert attempts == [(metadata, b"pending image bytes")]
    assert json.loads(manifest.read_text(encoding="utf-8")) == metadata
    assert content.read_bytes() == b"pending image bytes"


@pytest.mark.asyncio
async def test_generic_source_image_is_downloaded_and_uploaded_as_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    requests: list[httpx.Request] = []

    async def image_response(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_png(),
        )

    uploads = []

    async def upload(_client, metadata, content):
        uploads.append((metadata, content))
        return {"status": "STORED", "is_generic": True}

    monkeypatch.setattr(scraper_image_client, "post_image_upload", upload)
    product = {
        "sku_interno": "ARJ87819",
        "ean": None,
        "link": "https://www.araujo.com.br/produto/87819.html",
        "imagem_url": "https://www.araujo.com.br/on/demandware.static/Sites-Araujo-Site/-/default/dw4f56ef98/images/generico.webp",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(image_response)
    ) as client:
        result = await scraper_image_client.download_and_upload_product_image(
            client, "araujo", product
        )

    assert result["status"] == "STORED"
    assert result["is_generic_fallback"] is True
    assert len(requests) == 1
    assert len(uploads) == 1
    assert uploads[0][0]["is_generic_fallback"] is True
    assert uploads[0][1].startswith(b"RIFF")
    cached = await scraper_image_client.download_and_upload_product_image(
        client, "araujo", product
    )
    assert cached == {
        "skipped": "generic_already_stored",
        "is_generic_fallback": True,
    }
    assert len(requests) == len(uploads) == 1
    assert not list((tmp_path / "outbox" / "images" / "araujo").glob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_status", "reason"),
    [(403, "image_source_http_403"), (404, "image_source_http_404")],
)
async def test_unavailable_source_image_is_nonfatal_and_does_not_create_outbox(
    monkeypatch, tmp_path, response_status, reason
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))

    async def missing_image(_request):
        return httpx.Response(response_status)

    async def upload(*_args, **_kwargs):
        raise AssertionError("unavailable images must not be uploaded")

    monkeypatch.setattr(scraper_image_client, "post_image_upload", upload)
    product = {
        "sku_interno": "ARJ101392",
        "ean": "7897705203095",
        "link": "https://www.araujo.com.br/produto/101392.html",
        "imagem_url": "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(missing_image)
    ) as client:
        result = await scraper_image_client.download_and_upload_product_image(
            client, "araujo", product
        )

    assert result == {"image_failure_reason": reason}
    assert not (tmp_path / "outbox" / "images" / "araujo").exists()


@pytest.mark.asyncio
async def test_source_image_timeout_is_recorded_without_retries(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    requests = []

    async def timeout(_request):
        requests.append(1)
        raise httpx.ReadTimeout("simulated timeout")

    async def upload(*_args, **_kwargs):
        raise AssertionError("a timed out image must not be uploaded")

    monkeypatch.setattr(scraper_image_client, "post_image_upload", upload)
    product = {
        "sku_interno": "ARJ101392",
        "link": "https://www.araujo.com.br/produto/101392.html",
        "imagem_url": "https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout)
    ) as client:
        result = await scraper_image_client.download_and_upload_product_image(
            client, "araujo", product
        )

    assert result == {"image_failure_reason": "image_download_timeout"}
    assert requests == [1]


@pytest.mark.asyncio
async def test_generic_pacheco_outbox_replays_and_is_removed_after_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    outbox = tmp_path / "outbox" / "images" / "pacheco"
    outbox.mkdir(parents=True)
    manifest = outbox / "pending.json"
    content = outbox / "pending.bin"
    manifest.write_text(
        json.dumps(
            {
                "upload_id": "5ea270b4-3985-432d-a5a5-ada953993ca1",
                "source": "pacheco",
                "sku": "PCH155349",
                "ean": "7891721027468",
                "product_url": "https://www.drogariaspacheco.com.br/glifage-xr-500mg-merck-sharp-30-comprimidos/p",
                "source_image_url": "https://drogariaspacheco.vteximg.com.br/arquivos/ids/1138787/de-referencia-tarja-vermelha-em-comprimido-pacheco.jpg?v=638297968647200000",
            }
        ),
        encoding="utf-8",
    )
    content.write_bytes(b"preserved image bytes")
    attempts = []

    async def upload(_client, metadata, image_bytes):
        attempts.append((metadata, image_bytes))
        return {"status": "STORED", "is_generic": True}

    monkeypatch.setattr(scraper_image_client, "post_image_upload", upload)

    async with httpx.AsyncClient() as client:
        first_run = await scraper_image_client.sync_pending_image_outbox(
            client, "pacheco"
        )
        second_run = await scraper_image_client.sync_pending_image_outbox(
            client, "pacheco"
        )
        assert first_run == 1
        assert second_run == 0

    assert attempts[0][0]["upload_id"] == "5ea270b4-3985-432d-a5a5-ada953993ca1"
    assert attempts[0][0]["ean"] == "7891721027468"
    assert attempts[0][1] == b"preserved image bytes"
    assert not manifest.exists()
    assert not content.exists()
