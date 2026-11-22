"""Independent direct-collection runner with page-sized Azure synchronization."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

# The collector imports shared scraper modules but never uses a database.
os.environ["DATABASE_URL"] = "sqlite://"

import httpx

from app.scrapers.scraper_batch_client import BatchSyncError, post_batch
from app.scrapers.scraper_image_client import (
    download_and_upload_product_image,
    pending_image_outbox_status,
    sync_pending_image_outbox,
)
from app.scrapers.source_modes import (
    SourceHTTPError,
    SourceMode,
    direct_access_ready,
    source_mode,
)


def _state_dir() -> Path:
    return Path(
        os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector")
    ).resolve()


def _source_state_path(source: str) -> Path:
    return _state_dir() / f"{source}.json"


def _outbox_path(source: str) -> Path:
    return _state_dir() / "outbox" / f"{source}.pending.json"


def _rejected_products_path(source: str) -> Path:
    return _state_dir() / "rejected" / f"{source}.json"


def _stop_requested() -> bool:
    marker = os.getenv("SCRAPER_COLLECTOR_STOP_FILE", "").strip()
    return bool(marker and Path(marker).exists())


def _sync_failure_state(error: BatchSyncError) -> tuple[str, str]:
    if error.status_code == 403:
        return "SYNC_BLOCKED", "access_denied"
    if error.status_code == 401:
        return "SYNC_AUTH_FAILED", "hmac_invalid"
    if error.status_code == 409:
        return "SYNC_CONFLICT", "batch_conflict"
    if error.status_code == 429:
        return "RATE_LIMITED", "rate_limited"
    if error.status_code in {400, 422}:
        return "SYNC_INVALID", "validation_error"
    if error.status_code in {500, 502, 503, 504}:
        return "SYNC_UNAVAILABLE", "server_error"
    if error.status_code is None:
        reason = str(error)
        if "timeout" in reason:
            return "SYNC_FAILED", "timeout"
        if "transport" in reason:
            return "SYNC_FAILED", "connection_error"
    return "SYNC_FAILED", "http_rejected"


def _select_page_products(
    products: list[dict[str, Any]], selected: int, limit: int | None
) -> list[dict[str, Any]]:
    if limit is None:
        return products
    return products[: max(0, limit - selected)]


def _configured_product_limit() -> int | None:
    raw_limit = os.getenv("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN", "").strip()
    scheduled = os.getenv("SCRAPER_COLLECTOR_SCHEDULED", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not raw_limit:
        if scheduled:
            raise RuntimeError("SCHEDULED_PRODUCT_LIMIT_REQUIRED")
        return None
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN_INVALID") from error
    if limit < 1 or (scheduled and limit > 50):
        raise RuntimeError("SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN_INVALID")
    return limit


def _read_state(source: str) -> dict[str, Any]:
    path = _source_state_path(source)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{source.upper()}_COLLECTOR_CHECKPOINT_INVALID") from error


def _write_state(source: str, state: dict[str, Any]) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{source}-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _source_state_path(source))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_outbox(source: str, payload: dict[str, Any]) -> None:
    path = _outbox_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{source}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _persist_rejected_products(
    source: str, batch: dict[str, Any], rejected: list[dict[str, Any]]
) -> None:
    if not rejected:
        return
    path = _rejected_products_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("SCRAPER_REJECTED_QUEUE_INVALID") from error
    indexed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("source") or source),
            str(item.get("sku_interno") or ""),
            str(item.get("reason") or ""),
            str(item.get("link") or ""),
        )
        indexed.setdefault(key, item)
    products = {
        (str(product.get("sku_interno")), str(product.get("link"))): product
        for product in batch.get("products", [])
    }
    for item in rejected:
        sku = str(item.get("sku_interno") or "")
        link = str(item.get("link") or "")
        product = products.get((sku, link))
        reason = str(item.get("reason") or "")
        key = (source, sku, reason, link)
        if key in indexed:
            continue
        indexed[key] = {
            "batch_id": batch.get("batch_id"),
            "source": source,
            "rejected_at": datetime.now(UTC).isoformat(),
            "reason": reason,
            "product_id": item.get("product_id"),
            "sku_interno": sku,
            "ean": item.get("ean"),
            "link": link,
            "product": product,
        }
    fd, temporary = tempfile.mkstemp(
        prefix=f".{source}-rejected-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(list(indexed.values()), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if lock_file.read(1) == b"":
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("SCRAPER_COLLECTOR_ALREADY_RUNNING") from error
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _log(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "service": "scraper-collector",
                "event": event,
                "time": datetime.now(UTC).isoformat(),
                **fields,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _first_pending_image_sku(source: str) -> str | None:
    directory = _state_dir() / "outbox" / "images" / source
    for manifest in sorted(directory.glob("*.json")):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        sku = str(value.get("sku") or "").strip()
        if sku:
            return sku[:80]
    return None


def _record_image_failure(
    state: dict[str, Any],
    source: str,
    *,
    sku: str | None,
    category: str,
    reason: str,
) -> None:
    # Reasons come from controlled error labels; discard anything that could
    # contain a URL, response body, or credential before persisting/logging.
    safe_reason = (
        "".join(
            character
            for character in str(reason)
            if character.isalnum() or character in "_.:-"
        )[:120]
        or "image_failure"
    )
    state["image_sync_status"] = "PARTIAL"
    if category == "download":
        state["image_download_failures"] += 1
    elif category == "upload":
        state["image_upload_failures"] += 1
    else:
        state["pending_image_replay_failures"] += 1
    failures = state.setdefault("image_failures", [])
    failures.append(
        {
            "sku": str(sku or "")[:80] or None,
            "category": category,
            "reason": safe_reason,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    del failures[:-50]
    _write_state(source, state)
    _log(
        "image_failure_nonfatal",
        source=source,
        sku=str(sku or "")[:80] or None,
        category=category,
        reason=safe_reason,
    )


def _require_direct_collection(source: str) -> None:
    if os.getenv("SCRAPER_COLLECTOR_ENABLED", "false").casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("SCRAPER_COLLECTOR_DISABLED")
    if os.getenv("SCRAPER_COLLECTION_ENV_AUTHORIZED", "false").casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("SCRAPER_COLLECTION_ENVIRONMENT_NOT_CONFIRMED")
    if source_mode(source) is not SourceMode.DIRECT or not direct_access_ready(source):
        raise RuntimeError(f"{source.upper()}_DIRECT_ACCESS_NOT_CONFIRMED")
    previous = _read_state(source)
    if previous.get("status") in {
        "ACCESS_BLOCKED",
        "SYNC_BLOCKED",
        "SYNC_AUTH_FAILED",
        "SYNC_CONFLICT",
        "SYNC_INVALID",
    }:
        raise RuntimeError(
            f"{source.upper()}_{previous['status']}_MANUAL_RESET_REQUIRED"
        )
    if previous.get("status") == "RATE_LIMITED":
        try:
            finished = datetime.fromisoformat(previous["finished_at"])
            retry_at = finished + timedelta(
                seconds=max(0, int(previous.get("retry_after_seconds") or 0))
            )
        except (KeyError, TypeError, ValueError):
            retry_at = datetime.now(UTC)
        if datetime.now(UTC) < retry_at:
            raise RuntimeError(f"{source.upper()}_RETRY_AFTER_ACTIVE")
    if not os.getenv("SCRAPER_INGEST_URL", "").strip():
        raise RuntimeError("SCRAPER_INGEST_URL_NOT_CONFIGURED")
    if len(os.getenv("SCRAPER_INGEST_HMAC_SECRET", "").encode("utf-8")) < 32:
        raise RuntimeError("SCRAPER_INGEST_SECRET_NOT_CONFIGURED")


async def _collect_and_sync(source: str) -> dict[str, Any]:
    _require_direct_collection(source)
    max_products_per_run = _configured_product_limit()
    products_selected = 0
    product_limit_reached = False
    previous = _read_state(source)
    state: dict[str, Any] = {
        "source": source,
        "status": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
        "attempt": int(previous.get("attempt", 0)) + 1,
        "batches_sent": 0,
        "products_sent": 0,
        "products_rejected": 0,
        "rejected_items": [],
        "catalog_created": 0,
        "offers_created": 0,
        "offers_updated": 0,
        "price_changes": 0,
        "images_registered": 0,
        "images_missing": 0,
        "image_blobs_uploaded": 0,
        "image_download_failures": 0,
        "image_upload_failures": 0,
        "pending_image_replay_failures": 0,
        "image_failures": [],
        "image_sync_status": "OK",
        "images_skipped_generic": 0,
        "generic_fallback_images_uploaded": 0,
    }
    _write_state(source, state)
    timeout = float(os.getenv("SCRAPER_INGEST_TIMEOUT_SECONDS", "30"))
    ca_bundle = os.getenv("SCRAPER_INGEST_CA_BUNDLE", "").strip()
    async with httpx.AsyncClient(
        verify=ca_bundle or True,
        follow_redirects=False,
        timeout=timeout,
        trust_env=False,
    ) as client:

        async def sync_payload(payload: dict[str, Any]) -> dict[str, Any]:
            _write_outbox(source, payload)
            response = await post_batch(client, payload)
            rejected_items = response.get("rejected_items") or []
            _persist_rejected_products(source, payload, rejected_items)
            _outbox_path(source).unlink(missing_ok=True)
            accepted_count = int(
                response.get(
                    "accepted_count",
                    response.get("product_count", len(payload["products"])),
                )
            )
            rejected_count = int(response.get("rejected_count", len(rejected_items)))
            state["batches_sent"] += 1
            state["products_sent"] += accepted_count
            state["products_rejected"] += rejected_count
            state["rejected_items"].extend(rejected_items)
            state["catalog_created"] += int(response.get("catalog_created", 0))
            state["offers_created"] += int(response.get("offers_created", 0))
            state["offers_updated"] += int(response.get("offers_updated", 0))
            state["price_changes"] += int(response.get("price_changes", 0))
            state["images_registered"] += int(response.get("images_registered", 0))
            state["images_missing"] += int(response.get("images_missing", 0))
            state["last_batch_id"] = payload["batch_id"]
            if rejected_count:
                state["status"] = "PARTIAL"
                state["stop_reason"] = "identity_conflicts_quarantined"
            _write_state(source, state)
            return response

        pending_path = _outbox_path(source)
        if pending_path.exists():
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            try:
                await sync_payload(pending)
            except BatchSyncError as error:
                state["status"], state["failure_category"] = _sync_failure_state(error)
                state["stop_reason"] = str(error)
                if error.retry_after_seconds is not None:
                    state["retry_after_seconds"] = error.retry_after_seconds
                _write_state(source, state)
                raise
            _log("pending_batch_replayed", source=source, batch_id=pending["batch_id"])

        try:
            replayed_images = await sync_pending_image_outbox(client, source)
            state["image_blobs_uploaded"] += replayed_images
            image_outbox_status = pending_image_outbox_status(source)
            state["image_outbox_pending"] = image_outbox_status["pending"]
            state["image_outbox_dead_letter"] = image_outbox_status["dead_letter"]
            state["image_outbox_backoff"] = image_outbox_status["backoff"]
            if replayed_images:
                _write_state(source, state)
                _log("pending_images_replayed", source=source, images=replayed_images)
        except (BatchSyncError, OSError) as error:
            _record_image_failure(
                state,
                source,
                sku=_first_pending_image_sku(source),
                category="pending_replay",
                reason=(
                    str(error)
                    if isinstance(error, BatchSyncError)
                    else "image_outbox_local_io_error"
                ),
            )

        async def send_page(products: list[dict[str, Any]]) -> int:
            nonlocal products_selected, product_limit_reached
            if max_products_per_run is not None:
                products = _select_page_products(
                    products, products_selected, max_products_per_run
                )
                if not products:
                    product_limit_reached = True
                    return 0
                products_selected += len(products)
                product_limit_reached = products_selected >= max_products_per_run
            observed_at = datetime.now(UTC)
            normalized = []
            for product in products:
                record = dict(product)
                record.setdefault("fonte_observado_em", observed_at)
                timestamp = record["fonte_observado_em"]
                if isinstance(timestamp, datetime):
                    record["fonte_observado_em"] = timestamp.astimezone(UTC).isoformat()
                normalized.append(record)
            payload = {
                "batch_id": str(uuid4()),
                "source": source,
                "observed_at": observed_at.isoformat(),
                "products": normalized,
            }
            try:
                response = await sync_payload(payload)
            except BatchSyncError as error:
                state["status"], state["failure_category"] = _sync_failure_state(error)
                state["stop_reason"] = str(error)
                if error.retry_after_seconds is not None:
                    state["retry_after_seconds"] = error.retry_after_seconds
                _write_state(source, state)
                raise
            rejected_skus = {
                str(item.get("sku_interno") or "")
                for item in response.get("rejected_items", [])
            }
            for product in normalized:
                if str(product.get("sku_interno") or "") in rejected_skus:
                    continue
                if not product.get("imagem_url"):
                    _record_image_failure(
                        state,
                        source,
                        sku=str(product.get("sku_interno") or ""),
                        category="download",
                        reason="source_image_url_missing",
                    )
                    continue
                try:
                    image_result = await download_and_upload_product_image(
                        client, source, product
                    )
                except (BatchSyncError, OSError) as error:
                    _record_image_failure(
                        state,
                        source,
                        sku=str(product.get("sku_interno") or ""),
                        category="upload",
                        reason=(
                            str(error)
                            if isinstance(error, BatchSyncError)
                            else "image_outbox_local_io_error"
                        ),
                    )
                    continue
                if image_result and image_result.get("image_failure_reason"):
                    _record_image_failure(
                        state,
                        source,
                        sku=str(product.get("sku_interno") or ""),
                        category="download",
                        reason=str(image_result["image_failure_reason"]),
                    )
                    continue
                if image_result is None:
                    _record_image_failure(
                        state,
                        source,
                        sku=str(product.get("sku_interno") or ""),
                        category="download",
                        reason="image_result_missing",
                    )
                    continue
                if image_result.get("skipped") in {
                    "generic_asset",
                    "generic_already_stored",
                }:
                    state["images_skipped_generic"] += 1
                    _write_state(source, state)
                    continue
                if image_result.get("is_generic_fallback"):
                    state["generic_fallback_images_uploaded"] += 1
                state["image_blobs_uploaded"] += 1
                state["image_bytes_uploaded"] = int(
                    state.get("image_bytes_uploaded", 0)
                ) + int(image_result["normalized_size_bytes"])
                _write_state(source, state)
            _log(
                "page_synced",
                source=source,
                batch_id=payload["batch_id"],
                products=int(response.get("accepted_count", len(normalized))),
                rejected=int(response.get("rejected_count", 0)),
                status=response.get("status"),
            )
            return len(normalized)

        def stop_source_collection() -> bool:
            return product_limit_reached or _stop_requested()

        try:
            if source == "araujo":
                from app.scrapers.araujo import AraujoScraper

                scraper = AraujoScraper(
                    request_delay=float(os.getenv("ARAUJO_REQUEST_DELAY_SECONDS", "1")),
                    max_pages=int(os.getenv("ARAUJO_MAX_PAGES", "200")),
                    allow_page_limit=(
                        os.getenv("ARAUJO_COLLECTOR_ALLOW_PAGE_LIMIT", "false")
                        .strip()
                        .casefold()
                        in {"1", "true", "yes", "on"}
                    ),
                    stop_requested_callback=stop_source_collection,
                )
                scraper.products_callback = send_page
                with scraper._run_lock():
                    await scraper._extract_direct_catalog()
                state["source_products"] = scraper.products_queued
                if product_limit_reached:
                    state["source_products"] = products_selected
                    state["status"] = "PARTIAL"
                    state["stop_reason"] = "configured_product_limit"
                elif _stop_requested():
                    state["status"] = "STOPPED"
                    state["stop_reason"] = "safe_stop_requested_after_synced_page"
                if (
                    scraper.allow_page_limit
                    and scraper.products_queued
                    and int(os.getenv("ARAUJO_MAX_PAGES", "200")) == 1
                ):
                    state["status"] = "PARTIAL"
                    state["stop_reason"] = "configured_page_limit"
            else:
                from app.scrapers.pacheco import PachecoScraper

                scraper = PachecoScraper(
                    request_delay=float(
                        os.getenv("PACHECO_REQUEST_DELAY_SECONDS", "1")
                    ),
                    max_items=(
                        max_products_per_run
                        if max_products_per_run is not None
                        else int(os.environ["PACHECO_COLLECTOR_MAX_ITEMS"])
                        if os.getenv("PACHECO_COLLECTOR_MAX_ITEMS", "").strip()
                        else None
                    ),
                    source_mode=SourceMode.DIRECT,
                )
                scraper.products_callback = send_page
                scraper.stop_requested_callback = stop_source_collection
                with scraper._run_lock():
                    scraper._start_run()
                    try:
                        await scraper.extrair_catalogo()
                    finally:
                        scraper.stats["finished_at"] = datetime.now(UTC).isoformat()
                        http_status = scraper.stats.get("last_http_status")
                        if http_status == 403:
                            scraper.stats["status"] = "blocked"
                        elif http_status == 429:
                            scraper.stats["status"] = "rate_limited"
                        elif scraper.collector_delivery_failed:
                            scraper.stats["status"] = "incomplete"
                        elif scraper.stats.get("complete"):
                            scraper.stats["status"] = "complete"
                        elif (
                            scraper.stats.get("stop_reason") == "configured_item_limit"
                        ):
                            scraper.stats["status"] = "limited"
                        else:
                            scraper.stats["status"] = "partial"
                        scraper._write_checkpoint()
                if scraper.stats.get("last_http_status") == 403:
                    state["status"] = "ACCESS_BLOCKED"
                    state["stop_reason"] = "http_403"
                elif scraper.stats.get("last_http_status") == 429:
                    state["status"] = "RATE_LIMITED"
                    state["stop_reason"] = "http_429"
                    state["retry_after_seconds"] = scraper.stats.get(
                        "retry_after_seconds"
                    )
                elif scraper.collector_delivery_failed:
                    state["status"] = state.get("status", "SYNC_FAILED")
                    state["stop_reason"] = state.get("stop_reason", "sync_failed")
                elif product_limit_reached:
                    state["status"] = "PARTIAL"
                    state["stop_reason"] = "configured_product_limit"
                    state["source_products"] = products_selected
                elif scraper.stats.get("stop_reason") == "safe_stop_requested":
                    state["status"] = "STOPPED"
                    state["stop_reason"] = "safe_stop_requested_after_synced_page"
                elif scraper.stats.get("complete"):
                    state["status"] = "COMPLETE"
                else:
                    state["status"] = "PARTIAL"
                state["source_products"] = scraper.stats.get("products_enqueued", 0)
                state["source_stats"] = {
                    "pages_requested": scraper.stats.get("pages_requested", 0),
                    "pages_succeeded": scraper.stats.get("pages_succeeded", 0),
                    "products_skipped": scraper.stats.get("products_skipped", 0),
                }

            if state["status"] == "RUNNING":
                state["status"] = "COMPLETE"
            if state.get("products_rejected", 0) and state["status"] in {
                "RUNNING",
                "COMPLETE",
            }:
                state["status"] = "PARTIAL"
                state["stop_reason"] = "identity_conflicts_quarantined"
            if state["status"] == "ACCESS_BLOCKED":
                state["stop_reason"] = "http_403"
            state["finished_at"] = datetime.now(UTC).isoformat()
            _write_state(source, state)
            _log(
                "run_finished",
                source=source,
                status=state["status"],
                products=state["products_sent"],
                batches=state["batches_sent"],
            )
            return state
        except SourceHTTPError as error:
            state["status"] = (
                "ACCESS_BLOCKED"
                if error.status_code == 403
                else "RATE_LIMITED"
                if error.status_code == 429
                else "FAILED"
            )
            state["stop_reason"] = f"http_{error.status_code}"
            state["retry_after_seconds"] = error.retry_after_seconds
            state["finished_at"] = datetime.now(UTC).isoformat()
            _write_state(source, state)
            _log(
                "run_stopped",
                source=source,
                status=state["status"],
                http_status=error.status_code,
            )
            return state
        except Exception as error:
            if state.get("status") not in {
                "SYNC_BLOCKED",
                "SYNC_AUTH_FAILED",
                "SYNC_CONFLICT",
                "SYNC_INVALID",
                "SYNC_UNAVAILABLE",
                "SYNC_FAILED",
            }:
                state["status"] = "FAILED"
                state["stop_reason"] = type(error).__name__
            state["finished_at"] = datetime.now(UTC).isoformat()
            _write_state(source, state)
            _log(
                "run_failed",
                source=source,
                status=state["status"],
                error_type=type(error).__name__,
            )
            return state


async def collect_source(source: str) -> dict[str, Any]:
    if source not in {"araujo", "pacheco"}:
        raise ValueError("source must be araujo or pacheco")
    lock_path = _state_dir() / "locks" / f"{source}.lock"
    with _exclusive_lock(lock_path):
        if _stop_requested():
            state = {
                "source": source,
                "status": "STOPPED",
                "stop_reason": "safe_stop_requested_before_source_start",
                "finished_at": datetime.now(UTC).isoformat(),
            }
            _write_state(source, state)
            return state
        return await _collect_and_sync(source)


async def sync_source_images(source: str) -> int:
    """Replay only already-normalized images; never contact a pharmacy source."""
    if source not in {"araujo", "pacheco"}:
        raise ValueError("source must be araujo or pacheco")
    endpoint = os.getenv("SCRAPER_INGEST_URL", "").strip()
    secret = os.getenv("SCRAPER_INGEST_HMAC_SECRET", "")
    if not endpoint.startswith("https://"):
        raise RuntimeError("SCRAPER_INGEST_URL_MUST_BE_HTTPS")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("SCRAPER_INGEST_SECRET_NOT_CONFIGURED")
    lock_path = _state_dir() / "locks" / f"{source}.lock"
    with _exclusive_lock(lock_path):
        timeout = float(os.getenv("SCRAPER_INGEST_TIMEOUT_SECONDS", "30"))
        ca_bundle = os.getenv("SCRAPER_INGEST_CA_BUNDLE", "").strip()
        async with httpx.AsyncClient(
            verify=ca_bundle or True,
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
        ) as client:
            uploaded = await sync_pending_image_outbox(client, source)
    _log("image_outbox_replay_finished", source=source, images_uploaded=uploaded)
    return uploaded


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta direta e sincronização por lotes"
    )
    parser.add_argument("source", choices=("araujo", "pacheco"))
    parser.add_argument(
        "--images-only",
        action="store_true",
        help=(
            "Replay outbox de imagens já coletadas sem acessar fontes "
            "ou sincronizar ofertas"
        ),
    )
    args = parser.parse_args()
    if args.images_only:
        uploaded = await sync_source_images(args.source)
        print(json.dumps({"source": args.source, "images_uploaded": uploaded}))
        return
    try:
        result = await collect_source(args.source)
    except RuntimeError as error:
        if str(error) == "SCRAPER_COLLECTOR_ALREADY_RUNNING":
            raise SystemExit(9) from None
        raise SystemExit(7) from None
    exit_codes = {
        "COMPLETE": 0,
        "ACCESS_BLOCKED": 2,
        "PARTIAL": 3,
        "STOPPED": 4,
        "RATE_LIMITED": 5,
        "SYNC_BLOCKED": 6,
        "SYNC_AUTH_FAILED": 6,
        "SYNC_CONFLICT": 6,
        "SYNC_INVALID": 6,
        "SYNC_UNAVAILABLE": 6,
        "SYNC_FAILED": 6,
        "FAILED": 7,
        "ACCESS_DENIED": 2,
        "DISABLED": 8,
    }
    raise SystemExit(exit_codes.get(str(result.get("status", "FAILED")).upper(), 7))


if __name__ == "__main__":
    asyncio.run(main())
