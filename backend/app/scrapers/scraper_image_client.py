"""Local download, optimization, durable outbox, and HMAC upload for images."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.scrapers.scraper_batch_client import BatchSyncError, post_image_upload
from app.services.imagens import (
    is_generic_image_url,
    offer_image_delivery_rejection_reason,
)
from app.services.medicine_images import InvalidMedicineImage, normalize_medicine_image


def _image_outbox_dir(source: str) -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "outbox" / "images" / source


def _generic_image_cache_path() -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "generic-image-cache.json"


def _generic_image_cache_key(metadata: dict[str, Any]) -> str:
    identity = "\n".join(
        str(metadata.get(field) or "")
        for field in ("source", "sku", "product_url", "source_image_url")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _generic_image_is_cached(metadata: dict[str, Any]) -> bool:
    try:
        cache = json.loads(_generic_image_cache_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError):
        return False
    return _generic_image_cache_key(metadata) in cache


def _remember_generic_image(metadata: dict[str, Any]) -> None:
    if not is_generic_image_url(str(metadata.get("source_image_url") or "")):
        return
    path = _generic_image_cache_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        cache = {}
    cache[_generic_image_cache_key(metadata)] = {
        "source": metadata.get("source"),
        "sku": metadata.get("sku"),
        "source_image_url": metadata.get("source_image_url"),
    }
    _atomic_write(
        path,
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def _download_image(
    client: httpx.AsyncClient, image_url: str
) -> tuple[bytes, str | None]:
    limit = int(os.getenv("SCRAPER_IMAGE_DOWNLOAD_MAX_BYTES", str(8 * 1024 * 1024)))
    async with client.stream(
        "GET",
        image_url,
        headers={"Accept": "image/webp,image/jpeg,image/png"},
        follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise InvalidMedicineImage(f"image_source_http_{response.status_code}")
        declared_length = response.headers.get("content-length")
        if declared_length:
            try:
                if int(declared_length) > limit:
                    raise InvalidMedicineImage("source_image_too_large")
            except ValueError as error:
                raise InvalidMedicineImage("invalid_image_content_length") from error
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > limit:
                raise InvalidMedicineImage("source_image_too_large")
            content.extend(chunk)
        content_type = (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        return bytes(content), content_type or None


async def _submit_pending_image(
    client: httpx.AsyncClient, metadata_path: Path
) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    content_path = metadata_path.with_suffix(".bin")
    if not content_path.is_file():
        raise BatchSyncError("scraper_image_outbox_content_missing")
    result = await post_image_upload(client, metadata, content_path.read_bytes())
    _remember_generic_image(metadata)
    # The manifest is the outbox commit marker. Remove it first so an
    # interruption can leave only a harmless orphaned binary, never a broken
    # manifest that blocks the next replay.
    metadata_path.unlink(missing_ok=True)
    content_path.unlink(missing_ok=True)
    return result


async def sync_pending_image_outbox(client: httpx.AsyncClient, source: str) -> int:
    directory = _image_outbox_dir(source)
    if not directory.exists():
        return 0
    manifests = {path.stem for path in directory.glob("*.json")}
    orphan_directory = directory / "quarantine" / "orphaned"
    for content_path in directory.glob("*.bin"):
        if content_path.stem not in manifests:
            orphan_directory.mkdir(parents=True, exist_ok=True)
            destination = orphan_directory / f"{content_path.stem}-{uuid4().hex}.bin"
            content_path.replace(destination)
    uploaded = 0
    for metadata_path in sorted(directory.glob("*.json")):
        try:
            await _submit_pending_image(client, metadata_path)
        except (OSError, json.JSONDecodeError) as error:
            raise BatchSyncError("scraper_image_outbox_invalid") from error
        else:
            uploaded += 1
    return uploaded


async def download_and_upload_product_image(
    client: httpx.AsyncClient, source: str, product: dict[str, Any]
) -> dict[str, Any] | None:
    image_url = str(product.get("imagem_url") or "").strip()
    if not image_url:
        return None
    product_url = str(product.get("link") or "").strip()
    pharmacy_name = "Araujo" if source == "araujo" else "Pacheco"
    if offer_image_delivery_rejection_reason(image_url, product_url, pharmacy_name):
        return None
    generic = is_generic_image_url(image_url)
    if generic and _generic_image_is_cached(
        {
            "source": source,
            "sku": str(product.get("sku_interno") or ""),
            "product_url": product_url,
            "source_image_url": image_url,
        }
    ):
        return {"skipped": "generic_already_stored", "is_generic_fallback": True}
    try:
        raw, source_content_type = await _download_image(client, image_url)
        normalized = normalize_medicine_image(
            raw,
            declared_content_type=(
                source_content_type
                if source_content_type in {"image/jpeg", "image/png", "image/webp"}
                else None
            ),
            max_bytes=int(os.getenv("SCRAPER_IMAGE_MAX_BYTES", str(512 * 1024))),
            max_dimension=int(os.getenv("SCRAPER_IMAGE_MAX_DIMENSION", "1200")),
        )
    except (httpx.HTTPError, InvalidMedicineImage, OSError, ValueError):
        return None

    metadata = {
        "upload_id": str(uuid4()),
        "source": source,
        "sku": str(product.get("sku_interno") or ""),
        "ean": str(product.get("ean") or "") or None,
        "product_url": product_url,
        "source_image_url": image_url,
        "is_generic_fallback": generic,
        "content_type": normalized.content_type,
        "content_sha256": normalized.sha256,
    }
    directory = _image_outbox_dir(source)
    metadata_path = directory / f"{metadata['upload_id']}.json"
    content_path = metadata_path.with_suffix(".bin")
    _atomic_write(content_path, normalized.content)
    _atomic_write(
        metadata_path,
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    result = await _submit_pending_image(client, metadata_path)
    return {
        **result,
        "is_generic_fallback": metadata["is_generic_fallback"],
        "source_size_bytes": len(raw),
        "normalized_size_bytes": len(normalized.content),
        "normalized_sha256": normalized.sha256,
    }
