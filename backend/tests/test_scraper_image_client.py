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
async def test_image_outbox_removes_orphaned_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_COLLECTOR_STATE_DIR", str(tmp_path))
    outbox = tmp_path / "outbox" / "images" / "pacheco"
    outbox.mkdir(parents=True)
    orphan = outbox / "interrupted.bin"
    orphan.write_bytes(b"partial pre-manifest data")

    async with httpx.AsyncClient() as client:
        assert (
            await scraper_image_client.sync_pending_image_outbox(client, "pacheco") == 0
        )
    assert not orphan.exists()
