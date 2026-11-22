"""Local download, optimization, durable outbox, and HMAC upload for images."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.utils import validar_ean13
from app.scrapers.scraper_batch_client import BatchSyncError, post_image_upload
from app.scrapers.source_rate_limiter import (
    acquire_external_request,
    note_source_response,
    source_cooldown_active,
)
from app.services.imagens import (
    is_generic_image_url,
    offer_image_delivery_rejection_reason,
)
from app.services.medicine_images import InvalidMedicineImage, normalize_medicine_image

logger = logging.getLogger(__name__)
_MAX_IMAGE_UPLOAD_ATTEMPTS = 8
_IMAGE_RETRY_BASE_SECONDS = 60
_IMAGE_RETRY_MAX_SECONDS = 12 * 60 * 60


def _image_outbox_dir(source: str) -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "outbox" / "images" / source


def _generic_image_cache_path() -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "generic-image-cache.json"


def _confirmed_image_audit_dir() -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "confirmed-images"


def _blocked_source_images_path() -> Path:
    state_dir = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return state_dir.resolve() / "blocked-source-images.json"


def _source_image_key(source: str, image_url: str) -> str:
    return hashlib.sha256(f"{source}\n{image_url}".encode("utf-8")).hexdigest()


def _source_image_is_blocked(source: str, image_url: str) -> bool:
    try:
        entries = json.loads(_blocked_source_images_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return _source_image_key(source, image_url) in entries


def _remember_blocked_source_image(
    source: str, image_url: str, sku: str, reason: str
) -> None:
    path = _blocked_source_images_path()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, dict):
            entries = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        entries = {}
    entries[_source_image_key(source, image_url)] = {
        "source": source,
        "sku": sku,
        "reason": reason,
        "blocked_at": datetime.now(UTC).isoformat(),
    }
    _atomic_write(path, json.dumps(entries, ensure_ascii=False).encode("utf-8"))


def _generic_image_cache_key(metadata: dict[str, Any]) -> str:
    identity = "\n".join(
        str(metadata.get(field) or "")
        for field in ("source", "sku", "product_url", "source_image_url")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _failure_reason(value: str, fallback: str) -> str:
    """Keep diagnostics useful without recording a URL or response body."""
    normalized = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", value).strip("_")[:120]
    return normalized or fallback


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


def _record_confirmed_image(metadata: dict[str, Any], response: dict[str, Any]) -> None:
    upload_id = str(metadata.get("upload_id") or "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", upload_id):
        raise BatchSyncError("scraper_image_upload_id_unconfirmed")
    status = response.get("status")
    linked = (
        response.get("catalog_id") is not None or response.get("offer_id") is not None
    )
    if status == "STORED":
        if response.get("sha256") != metadata.get("content_sha256") or not linked:
            raise BatchSyncError("scraper_image_upload_storage_unconfirmed")
    elif status == "SKIPPED_EXISTING_IMAGE":
        if response.get("confirmed_existing_image") is not True or not linked:
            raise BatchSyncError("scraper_image_existing_link_unconfirmed")
    else:
        raise BatchSyncError("scraper_image_upload_not_confirmed")
    record = {
        "upload_id": upload_id,
        "source": metadata.get("source"),
        "sku": metadata.get("sku"),
        "ean": metadata.get("ean"),
        "candidate_sha256": metadata.get("content_sha256"),
        "stored_sha256": response.get("sha256"),
        "catalog_id": response.get("catalog_id"),
        "offer_id": response.get("offer_id"),
        "status": response.get("status"),
        "size_bytes": response.get("size_bytes"),
        "confirmed_at": datetime.now(UTC).isoformat(),
    }
    content = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    _atomic_write(_confirmed_image_audit_dir() / f"{upload_id}.json", content)


def _record_local_image_cleanup(*, files: int, bytes_freed: int) -> None:
    if files <= 0:
        return
    path = (
        Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
        / "image-cleanup-counters.json"
    )
    try:
        counters = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(counters, dict):
            counters = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        counters = {}
    counters["confirmed_files_removed"] = (
        int(counters.get("confirmed_files_removed", 0)) + files
    )
    counters["confirmed_bytes_freed"] = (
        int(counters.get("confirmed_bytes_freed", 0)) + bytes_freed
    )
    counters["updated_at"] = datetime.now(UTC).isoformat()
    try:
        _atomic_write(
            path,
            json.dumps(counters, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
        )
    except OSError:
        logger.warning("scraper_image_cleanup_counter_write_failed")


def _has_confirmed_upload(upload_id: str) -> bool:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", upload_id):
        return False
    try:
        record = json.loads(
            (_confirmed_image_audit_dir() / f"{upload_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return (
        record.get("upload_id") == upload_id
        and record.get("status") in {"STORED", "SKIPPED_EXISTING_IMAGE"}
        and bool(record.get("catalog_id") or record.get("offer_id"))
    )


def _remove_confirmed_orphan(path: Path, upload_id: str) -> bool:
    if not _has_confirmed_upload(upload_id):
        return False
    try:
        size = path.stat().st_size
        path.unlink()
    except OSError:
        return False
    _record_local_image_cleanup(files=1, bytes_freed=size)
    return True


def _finalize_confirmed_pending_image(
    metadata_path: Path, metadata: dict[str, Any]
) -> dict[str, Any]:
    confirmation = metadata.get("confirmation")
    if not isinstance(confirmation, dict):
        raise BatchSyncError("scraper_image_confirmation_record_missing")
    _record_confirmed_image(metadata, confirmation)
    content_path = metadata_path.with_suffix(".bin")
    removed_files = 0
    bytes_freed = 0
    if content_path.is_file():
        size = content_path.stat().st_size
        content_path.unlink()
        removed_files += 1
        bytes_freed += size
    if metadata_path.is_file():
        size = metadata_path.stat().st_size
        metadata_path.unlink()
        removed_files += 1
        bytes_freed += size
    _record_local_image_cleanup(files=removed_files, bytes_freed=bytes_freed)
    return confirmation


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
    client: httpx.AsyncClient, image_url: str, source: str
) -> tuple[bytes, str | None]:
    limit = int(os.getenv("SCRAPER_IMAGE_DOWNLOAD_MAX_BYTES", str(8 * 1024 * 1024)))
    await acquire_external_request(source, image_url)
    async with client.stream(
        "GET",
        image_url,
        headers={"Accept": "image/webp,image/jpeg,image/png"},
        follow_redirects=False,
    ) as response:
        note_source_response(source, image_url, response)
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
    if metadata.get("status") == "CONFIRMED":
        return _finalize_confirmed_pending_image(metadata_path, metadata)
    if metadata.get("status") == "DEAD_LETTER":
        raise BatchSyncError("scraper_image_upload_dead_letter")
    next_retry_at = metadata.get("next_retry_at")
    if next_retry_at:
        try:
            retry_at = datetime.fromisoformat(next_retry_at)
        except (TypeError, ValueError) as error:
            raise BatchSyncError("scraper_image_outbox_invalid_retry_time") from error
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        if datetime.now(UTC) < retry_at.astimezone(UTC):
            raise BatchSyncError("scraper_image_upload_backoff_active")
    content_path = metadata_path.with_suffix(".bin")
    if not content_path.is_file():
        raise BatchSyncError("scraper_image_outbox_content_missing")
    attempt_count = int(metadata.get("attempt_count", 0)) + 1
    metadata.update(
        {
            "attempt_count": attempt_count,
            "last_attempt_at": datetime.now(UTC).isoformat(),
            "status": "UPLOADING",
            "next_retry_at": None,
        }
    )
    _atomic_write(
        metadata_path,
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    try:
        internal_fields = {
            "attempt_count",
            "last_attempt_at",
            "status",
            "next_retry_at",
            "last_error",
        }
        upload_metadata = {
            key: value for key, value in metadata.items() if key not in internal_fields
        }
        # A malformed source EAN is evidence that must stay in the local manifest,
        # but it must not prevent the image from being associated by its exact
        # source SKU and product URL. Never replace it with a guessed catalog EAN.
        ean = upload_metadata.get("ean")
        if ean and not validar_ean13(str(ean)):
            upload_metadata["ean"] = None
        result = await post_image_upload(
            client, upload_metadata, content_path.read_bytes()
        )
    except BatchSyncError as error:
        permanent = (
            error.status_code is not None
            and 400 <= error.status_code < 500
            and error.status_code != 429
        )
        exhausted = attempt_count >= _MAX_IMAGE_UPLOAD_ATTEMPTS
        if permanent or exhausted:
            metadata["status"] = "DEAD_LETTER"
            metadata["next_retry_at"] = None
        else:
            delay = min(
                _IMAGE_RETRY_BASE_SECONDS * (2 ** min(attempt_count - 1, 12)),
                _IMAGE_RETRY_MAX_SECONDS,
            )
            if error.retry_after_seconds is not None:
                delay = max(delay, error.retry_after_seconds)
            metadata["status"] = "PENDING"
            metadata["next_retry_at"] = (
                datetime.now(UTC) + timedelta(seconds=delay)
            ).isoformat()
        metadata["last_error"] = error.error_code or str(error)[:120]
        _atomic_write(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
        )
        logger.warning(
            "scraper_image_upload_failed",
            extra={
                "source": metadata.get("source"),
                "sku": metadata.get("sku"),
                "attempt": attempt_count,
                "status": metadata["status"],
                "reason": metadata["last_error"],
            },
        )
        raise
    _record_confirmed_image(metadata, result)
    _remember_generic_image(metadata)
    # Persist confirmation before local cleanup. If interrupted, replay performs
    # cleanup only and does not issue another upload.
    metadata["status"] = "CONFIRMED"
    metadata["confirmation"] = result
    _atomic_write(
        metadata_path,
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    return _finalize_confirmed_pending_image(metadata_path, metadata)


async def sync_pending_image_outbox(client: httpx.AsyncClient, source: str) -> int:
    directory = _image_outbox_dir(source)
    if not directory.exists():
        return 0
    manifests = {path.stem for path in directory.glob("*.json")}
    orphan_directory = directory / "quarantine" / "orphaned"
    for content_path in directory.glob("*.bin"):
        if content_path.stem not in manifests:
            if _remove_confirmed_orphan(content_path, content_path.stem):
                continue
            orphan_directory.mkdir(parents=True, exist_ok=True)
            destination = orphan_directory / f"{content_path.stem}-{uuid4().hex}.bin"
            content_path.replace(destination)
    if orphan_directory.exists():
        for content_path in orphan_directory.glob("*.bin"):
            upload_id = content_path.name[:36]
            if content_path.name[36:37] == "-":
                _remove_confirmed_orphan(content_path, upload_id)
    uploaded = 0
    for metadata_path in sorted(directory.glob("*.json")):
        try:
            current = json.loads(metadata_path.read_text(encoding="utf-8"))
            if current.get("status") == "DEAD_LETTER":
                continue
            next_retry_at = current.get("next_retry_at")
            if next_retry_at:
                retry_at = datetime.fromisoformat(next_retry_at)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                if datetime.now(UTC) < retry_at.astimezone(UTC):
                    continue
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("scraper_image_outbox_invalid", extra={"source": source})
            continue
        try:
            result = await _submit_pending_image(client, metadata_path)
        except (OSError, json.JSONDecodeError):
            logger.warning("scraper_image_outbox_invalid", extra={"source": source})
            continue
        except BatchSyncError:
            continue
        else:
            uploaded += result.get("status") == "STORED"
    return uploaded


def pending_image_outbox_status(source: str) -> dict[str, int]:
    directory = _image_outbox_dir(source)
    result = {"pending": 0, "dead_letter": 0, "backoff": 0}
    if not directory.exists():
        return result
    now = datetime.now(UTC)
    for path in directory.glob("*.json"):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result["pending"] += 1
            continue
        if metadata.get("status") == "DEAD_LETTER":
            result["dead_letter"] += 1
            continue
        result["pending"] += 1
        retry = metadata.get("next_retry_at")
        if retry:
            try:
                retry_at = datetime.fromisoformat(retry)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                if retry_at.astimezone(UTC) > now:
                    result["backoff"] += 1
            except (TypeError, ValueError):
                pass
    return result


async def download_and_upload_product_image(
    client: httpx.AsyncClient, source: str, product: dict[str, Any]
) -> dict[str, Any]:
    image_url = str(product.get("imagem_url") or "").strip()
    if not image_url:
        return {"image_failure_reason": "source_image_url_missing"}
    if source_cooldown_active(source):
        return {"image_failure_reason": "source_http_403_cooldown"}
    product_url = str(product.get("link") or "").strip()
    pharmacy_name = "Araujo" if source == "araujo" else "Pacheco"
    rejection = offer_image_delivery_rejection_reason(
        image_url, product_url, pharmacy_name
    )
    if rejection:
        return {
            "image_failure_reason": _failure_reason(rejection, "image_source_rejected")
        }
    generic = is_generic_image_url(image_url)
    if _source_image_is_blocked(source, image_url):
        return {"image_failure_reason": "image_source_previously_blocked"}
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
        raw, source_content_type = await _download_image(client, image_url, source)
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
    except httpx.TimeoutException:
        return {"image_failure_reason": "image_download_timeout"}
    except httpx.HTTPError:
        return {"image_failure_reason": "image_download_transport_error"}
    except InvalidMedicineImage as error:
        reason = _failure_reason(str(error), "invalid_source_image")
        if reason in {"image_source_http_403", "image_source_http_404"}:
            _remember_blocked_source_image(
                source,
                image_url,
                str(product.get("sku_interno") or ""),
                reason,
            )
        return {"image_failure_reason": reason}
    except OSError:
        return {"image_failure_reason": "image_local_io_error"}
    except ValueError:
        return {"image_failure_reason": "invalid_source_image_content"}

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
