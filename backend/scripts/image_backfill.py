"""Image-only recovery from the HMAC-authenticated production queue."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

# The imported identity parsers live beside the normal offer-refresh worker.
# Pin its unused SQLAlchemy engine to an in-memory SQLite URL so this
# image-only process can never attach to a development or production database.
os.environ["DATABASE_URL"] = "sqlite://"

from app.scrapers.offer_refresh import (
    IdentityMismatch,
    SourceUnavailable,
    _source_url,
    parse_araujo_image,
    parse_pacheco_image,
)
from app.scrapers.scraper_batch_client import (
    BatchSyncError,
    fetch_image_backfill_inventory,
    fetch_image_backfill_page,
)
from app.scrapers.scraper_image_client import (
    download_and_upload_product_image,
    pending_image_outbox_status,
    sync_pending_image_outbox,
)
from app.scrapers.source_http_client import open_source_http_client
from app.scrapers.source_rate_limiter import (
    ExternalRequestDeferred,
    _exclusive_file_lock,
    external_request_purpose,
    rate_budget_status,
    source_cooldown_active,
)
from app.services.imagens import is_generic_image_url

BACKFILL_OUTBOX = "image-backfill"
_TRANSIENT_REASONS = {
    "image_download_timeout",
    "image_download_transport_error",
    "image_local_io_error",
    "invalid_source_image_content",
    "PRODUCT_LOOKUP_TIMEOUT",
    "PRODUCT_LOOKUP_TRANSPORT_ERROR",
}


def _state_dir() -> Path:
    root = Path(os.getenv("SCRAPER_COLLECTOR_STATE_DIR", "data/scraper_collector"))
    return root.resolve() / "image-backfill"


def _checkpoint_path(source: str | None, phase: str) -> Path:
    key = source or "all"
    return _state_dir() / f"progress-{key}-{phase}.json"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema": "preco-bao.image-backfill-progress",
            "version": 1,
            "after_catalog_id": 0,
            "retry_items": [],
            "updated_at": None,
        }
    if (
        not isinstance(state, dict)
        or state.get("schema") != "preco-bao.image-backfill-progress"
        or state.get("version") != 1
        or not isinstance(state.get("after_catalog_id"), int)
        or not isinstance(state.get("retry_items"), list)
    ):
        raise RuntimeError("IMAGE_BACKFILL_CHECKPOINT_INVALID_FAIL_CLOSED")
    return state


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def _check_execution_budget() -> dict[str, Any]:
    status = rate_budget_status()
    if not status["enabled"]:
        raise RuntimeError("IMAGE_BACKFILL_EXTERNAL_RATE_LIMIT_DISABLED")
    if (
        status["limit"] > 50
        or status["window_seconds"] < 900
        or status["per_source_limit"] > 25
        or status["image_backfill_per_source_limit"] > 10
    ):
        raise RuntimeError("IMAGE_BACKFILL_RATE_LIMIT_CONFIGURATION_UNSAFE")
    if os.getenv("ARAUJO_TLS_VERIFY", "true").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    } or os.getenv("PACHECO_TLS_VERIFY", "true").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("IMAGE_BACKFILL_SOURCE_TLS_VERIFICATION_REQUIRED")
    backfill_limit = int(
        os.getenv("SCRAPER_EXTERNAL_RATE_LIMIT_IMAGE_BACKFILL_PER_SOURCE", "10")
    )
    if backfill_limit > 10:
        raise RuntimeError("IMAGE_BACKFILL_RATE_LIMIT_CONFIGURATION_UNSAFE")
    if status["image_backfill_per_source_limit"] < 1:
        raise RuntimeError("IMAGE_BACKFILL_RATE_LIMIT_CONFIGURATION_DISABLED")
    if len(os.getenv("SCRAPER_INGEST_HMAC_SECRET", "").encode()) < 32:
        raise RuntimeError("IMAGE_BACKFILL_HMAC_NOT_CONFIGURED")
    return status


async def _fetch_source_image_url(
    source: str, entry: dict[str, Any]
) -> tuple[str | None, str]:
    if source_cooldown_active(source):
        return None, "SOURCE_COOLDOWN"
    offer = SimpleNamespace(
        sku_interno=entry["sku"],
        url_origem=entry["product_url"],
        produto_catalogo=SimpleNamespace(codigo_barras=entry["ean"]),
    )
    request_url = _source_url(offer, source)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "User-Agent": "PrecoBao authorized image recovery/1.0",
    }
    try:
        async with open_source_http_client(source, headers=headers) as client:
            response = await client.get(request_url)
    except ExternalRequestDeferred:
        raise
    except httpx.TimeoutException:
        return None, "PRODUCT_LOOKUP_TIMEOUT"
    except httpx.HTTPError:
        return None, "PRODUCT_LOOKUP_TRANSPORT_ERROR"
    if response.status_code == 403:
        return None, "SOURCE_HTTP_403"
    if response.status_code == 429:
        return None, "SOURCE_HTTP_429"
    if 500 <= response.status_code <= 599:
        return None, f"PRODUCT_LOOKUP_HTTP_{response.status_code}"
    # A partial VTEX product response is not enough to establish product/SKU identity.
    if response.status_code != 200:
        return None, f"SOURCE_HTTP_{response.status_code}"
    try:
        if source == "araujo":
            candidate = parse_araujo_image(offer, response.text)
        else:
            candidate = parse_pacheco_image(offer, response.json())
    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        SourceUnavailable,
        IdentityMismatch,
    ):
        return None, "PRODUCT_IDENTITY_OR_PAYLOAD_INVALID"
    return candidate, "IMAGE_FOUND" if candidate else "PRODUCT_HAS_NO_VALID_IMAGE"


async def _try_source_offer(
    client: httpx.AsyncClient,
    source: str,
    ean: str,
    offer: dict[str, Any],
    *,
    include_generic: bool,
    allow_source_lookup: bool,
) -> tuple[dict[str, Any] | None, str]:
    attempted: set[str] = set()
    candidate_urls = list(offer.get("image_urls") or [])
    source_was_blocked = False

    async def try_url(url_record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        nonlocal source_was_blocked
        url = str(url_record.get("url") or "").strip()
        if not url or url in attempted:
            return None, "DUPLICATE_OR_EMPTY_URL"
        attempted.add(url)
        if bool(url_record.get("generic")) and not include_generic:
            return None, "GENERIC_IMAGE_SKIPPED"
        if source_cooldown_active(source):
            source_was_blocked = True
            return None, "SOURCE_COOLDOWN"
        with external_request_purpose("image_backfill"):
            result = await download_and_upload_product_image(
                client,
                source,
                {
                    "imagem_url": url,
                    "link": offer["product_url"],
                    "sku_interno": offer["sku"],
                    "ean": ean,
                },
                only_if_missing=True,
                outbox_namespace=BACKFILL_OUTBOX,
            )
        if result.get("status") in {"STORED", "SKIPPED_EXISTING_IMAGE"}:
            return result, "STORED" if result.get(
                "status"
            ) == "STORED" else "ALREADY_STORED"
        reason = str(result.get("image_failure_reason") or "IMAGE_REJECTED")
        if reason == "image_source_http_403" or source_cooldown_active(source):
            source_was_blocked = True
        return None, reason

    for candidate in candidate_urls:
        if not isinstance(candidate, dict):
            continue
        result, reason = await try_url(candidate)
        if result:
            return result, reason
        if reason == "image_source_http_429":
            return None, "SOURCE_HTTP_429"
        if reason in _TRANSIENT_REASONS:
            return None, reason
        if source_was_blocked:
            break

    if (
        allow_source_lookup
        and not source_was_blocked
        and not source_cooldown_active(source)
    ):
        with external_request_purpose("image_backfill"):
            candidate_url, lookup_status = await _fetch_source_image_url(
                source,
                {
                    "sku": offer["sku"],
                    "product_url": offer["product_url"],
                    "ean": ean,
                },
            )
        if lookup_status == "SOURCE_HTTP_429":
            return None, lookup_status
        if (
            lookup_status == "PRODUCT_LOOKUP_TIMEOUT"
            or lookup_status == "PRODUCT_LOOKUP_TRANSPORT_ERROR"
            or lookup_status.startswith("PRODUCT_LOOKUP_HTTP_5")
        ):
            return None, lookup_status
        if lookup_status == "SOURCE_HTTP_403" or source_cooldown_active(source):
            source_was_blocked = True
        if candidate_url and candidate_url not in attempted:
            generic = is_generic_image_url(candidate_url)
            result, reason = await try_url({"url": candidate_url, "generic": generic})
            if result:
                return result, reason
            if reason == "image_source_http_429":
                return None, "SOURCE_HTTP_429"
            if reason in _TRANSIENT_REASONS:
                return None, reason
    if source_was_blocked:
        return None, "SOURCE_HTTP_403_COOLDOWN"
    if (
        any(
            bool(item.get("generic"))
            for item in candidate_urls
            if isinstance(item, dict)
        )
        and not include_generic
    ):
        return None, "GENERIC_ONLY"
    return None, "NO_VALID_IMAGE"


async def _process_entry(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    *,
    source_filter: str | None,
    include_generic: bool,
    phase: str,
) -> dict[str, Any]:
    status = str(item.get("status") or "UNKNOWN")
    if status in {"BYTEA_REAL_VALID", "BYTEA_GENERIC_VALID"}:
        return {"status": status}
    if status == "IDENTITY_CONFLICT":
        return {"status": "IDENTITY_CONFLICT"}
    sources = [
        source_entry
        for source_entry in item.get("sources", [])
        if source_filter is None or source_entry.get("source") == source_filter
    ]
    if not sources:
        return {
            "status": status
            if status != "EXTERNAL_URL_CANDIDATE"
            else "NO_SOURCE_OFFER"
        }
    transient_reason: str | None = None
    source_results: list[dict[str, str]] = []
    for source_entry in sources:
        source = str(source_entry.get("source") or "")
        if source not in {"araujo", "pacheco"}:
            continue
        for offer in source_entry.get("offers", []):
            result, reason = await _try_source_offer(
                client,
                source,
                str(item.get("ean") or ""),
                offer,
                include_generic=include_generic,
                allow_source_lookup=phase == "source_lookup",
            )
            source_results.append({"source": source, "status": reason})
            if result:
                returned_catalog_id = result.get("catalog_id")
                if returned_catalog_id is not None and int(returned_catalog_id) != int(
                    item["catalog_id"]
                ):
                    return {
                        "status": "UPLOAD_CATALOG_ID_MISMATCH",
                        "source_results": source_results
                        + [{"source": source, "status": "UPLOAD_CATALOG_ID_MISMATCH"}],
                    }
                return {
                    "status": reason,
                    "source": source,
                    "catalog_id": int(item["catalog_id"]),
                    "sha256": result.get("normalized_sha256") or result.get("sha256"),
                    "size_bytes": result.get("normalized_size_bytes")
                    or result.get("size_bytes"),
                    "source_results": source_results,
                }
            if reason == "SOURCE_HTTP_429":
                return {"status": "DEFERRED_RATE_LIMIT", "retry": True}
            is_transient = (
                reason in _TRANSIENT_REASONS
                or reason.startswith("image_source_http_5")
                or reason.startswith("PRODUCT_LOOKUP_HTTP_5")
                or reason
                in {
                    "PRODUCT_LOOKUP_TIMEOUT",
                    "PRODUCT_LOOKUP_TRANSPORT_ERROR",
                }
            )
            if is_transient:
                transient_reason = reason
            if source_cooldown_active(source):
                break
    if transient_reason:
        return {
            "status": "RETRY_PENDING",
            "retry": True,
            "reason": transient_reason,
            "source_results": source_results,
        }
    return {"status": "NO_VALID_IMAGE", "source_results": source_results}


def _add_retry(
    state: dict[str, Any], item: dict[str, Any], outcome: dict[str, Any]
) -> None:
    catalog_id = int(item["catalog_id"])
    retries = state["retry_items"]
    previous = next(
        (
            record
            for record in retries
            if int(record.get("catalog_id", -1)) == catalog_id
        ),
        None,
    )
    attempts = int(previous.get("attempts", 0)) + 1 if previous else 1
    wait = min(900 * (2 ** min(attempts - 1, 4)), 4 * 60 * 60)
    record = {
        "catalog_id": catalog_id,
        "attempts": attempts,
        "next_retry_at": (datetime.now(UTC) + timedelta(seconds=wait)).isoformat(),
        "reason": outcome.get("reason", outcome.get("status")),
        "item": item,
    }
    if previous:
        retries[retries.index(previous)] = record
    elif len(retries) < 1000:
        retries.append(record)


def _ready_retries(state: dict[str, Any]) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    due: list[dict[str, Any]] = []
    for record in state["retry_items"]:
        try:
            retry_at = datetime.fromisoformat(record["next_retry_at"])
        except (TypeError, ValueError, KeyError):
            continue
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        if retry_at.astimezone(UTC) <= now:
            due.append(record)
    return due


async def _run(args: argparse.Namespace) -> int:
    headers = {
        "Accept": "application/json",
        "User-Agent": "PrecoBao image recovery/1.0",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30),
        follow_redirects=False,
        headers=headers,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        trust_env=False,
    ) as api_client:
        if args.inventory:
            result = await fetch_image_backfill_inventory(api_client)
            _emit({"operation": "inventory", **result})
            return 0
        if not args.execute:
            result = await fetch_image_backfill_page(
                api_client,
                after_catalog_id=args.after_catalog_id,
                limit=args.limit,
                source=args.source,
                phase=args.phase,
            )
            _emit(
                {
                    "operation": "dry_run",
                    "phase": args.phase,
                    "page_size": result.get("page_size"),
                    "has_more": result.get("has_more"),
                    "status_counts": result.get("status_counts"),
                    "items_selected_for_execution": sum(
                        bool(item.get("eligible_for_backfill"))
                        for item in result.get("items", [])
                    ),
                    "external_requests": 0,
                    "production_writes": 0,
                }
            )
            return 0

        budget_before = _check_execution_budget()
        state_path = _checkpoint_path(args.source, args.phase)
        lock_path = _state_dir() / "image-backfill.running.lock"
        with _exclusive_file_lock(lock_path):
            state = _load_state(state_path)
            state["status"] = "RUNNING"
            state["updated_at"] = datetime.now(UTC).isoformat()
            _atomic_json(state_path, state)
            report = {
                "operation": "execute",
                "source": args.source or "both",
                "processed": 0,
                "stored": 0,
                "already_stored": 0,
                "existing_valid": 0,
                "generic_skipped": 0,
                "identity_conflicts": 0,
                "no_valid_image": 0,
                "retry_pending": 0,
                "blocked_sources": 0,
                "stored_by_source": {"araujo": 0, "pacheco": 0},
                "stored_items": [],
                "reason_counts": {},
                "rate_limited": False,
            }
            max_items = args.limit
            for source in (args.source,) if args.source else ("araujo", "pacheco"):
                try:
                    await sync_pending_image_outbox(
                        api_client, source, namespace=BACKFILL_OUTBOX
                    )
                except BatchSyncError:
                    pass
            source_outboxes = (args.source,) if args.source else ("araujo", "pacheco")
            if any(
                pending_image_outbox_status(source, namespace=BACKFILL_OUTBOX)[
                    "pending"
                ]
                for source in source_outboxes
            ):
                report["pending_outbox_hold"] = True
                report["processed"] = 0
                report["rate_limited"] = True

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30),
                follow_redirects=False,
                headers={
                    "Accept": "image/webp,image/jpeg,image/png",
                    "User-Agent": "PrecoBao image recovery/1.0",
                },
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                trust_env=False,
            ) as image_client:
                retry_records = (
                    [] if report["pending_outbox_hold"] else _ready_retries(state)
                )
                for retry_record in retry_records:
                    if report["processed"] >= max_items:
                        break
                    item = retry_record["item"]
                    try:
                        outcome = await _process_entry(
                            image_client,
                            item,
                            source_filter=args.source,
                            include_generic=args.include_generic_fallback,
                            phase=args.phase,
                        )
                    except ExternalRequestDeferred:
                        report["rate_limited"] = True
                        break
                    report["processed"] += 1
                    _count_reasons(report, outcome)
                    if outcome.get("retry"):
                        _add_retry(state, item, outcome)
                        report["retry_pending"] += 1
                    else:
                        state["retry_items"].remove(retry_record)
                        _count_outcome(report, outcome)
                    _atomic_json(state_path, state)
                if not report["rate_limited"]:
                    while report["processed"] < max_items:
                        page = await fetch_image_backfill_page(
                            api_client,
                            after_catalog_id=int(state["after_catalog_id"]),
                            limit=min(100, max_items - report["processed"]),
                            source=args.source,
                            phase=args.phase,
                        )
                        items = page.get("items", [])
                        if not items:
                            if not page.get("has_more"):
                                state["status"] = "COMPLETE"
                                _atomic_json(state_path, state)
                            break
                        stop_page = False
                        for item in items:
                            try:
                                outcome = await _process_entry(
                                    image_client,
                                    item,
                                    source_filter=args.source,
                                    include_generic=args.include_generic_fallback,
                                    phase=args.phase,
                                )
                            except ExternalRequestDeferred:
                                report["rate_limited"] = True
                                stop_page = True
                                break
                            report["processed"] += 1
                            _count_reasons(report, outcome)
                            if outcome.get("retry"):
                                _add_retry(state, item, outcome)
                                report["retry_pending"] += 1
                            else:
                                _count_outcome(report, outcome)
                            state["after_catalog_id"] = int(item["catalog_id"])
                            state["status"] = "RUNNING"
                            state["updated_at"] = datetime.now(UTC).isoformat()
                            _atomic_json(state_path, state)
                            if report["processed"] >= max_items:
                                break
                        if stop_page or report["processed"] >= max_items:
                            break
                        if not page.get("has_more"):
                            state["status"] = "COMPLETE"
                            _atomic_json(state_path, state)
                            break
            budget_after = rate_budget_status()
            state["last_run"] = {
                **report,
                "started_at": state.get("updated_at"),
                "finished_at": datetime.now(UTC).isoformat(),
                "budget_before": {
                    "total": budget_before["total"],
                    "by_source_purpose": budget_before["by_source_purpose"],
                },
                "budget_after": {
                    "total": budget_after["total"],
                    "by_source_purpose": budget_after["by_source_purpose"],
                },
                "pending_outbox": {
                    source: pending_image_outbox_status(
                        source, namespace=BACKFILL_OUTBOX
                    )
                    for source in (
                        (args.source,) if args.source else ("araujo", "pacheco")
                    )
                },
                "local_backfill_bytes": _backfill_temp_bytes(),
                "local_backfill_files": _backfill_temp_files(),
            }
            state["status"] = (
                "PAUSED_RATE_LIMIT"
                if report["rate_limited"]
                else state.get("status", "RUNNING")
            )
            state["updated_at"] = datetime.now(UTC).isoformat()
            _atomic_json(state_path, state)
            _emit(state["last_run"])
            return 0


def _count_outcome(report: dict[str, Any], outcome: dict[str, Any]) -> None:
    status = str(outcome.get("status") or "UNKNOWN")
    if status == "STORED":
        report["stored"] += 1
        source = outcome.get("source")
        if source in report["stored_by_source"]:
            report["stored_by_source"][source] += 1
        report["stored_items"].append(
            {
                "catalog_id": outcome.get("catalog_id"),
                "source": source,
                "sha256": outcome.get("sha256"),
                "size_bytes": outcome.get("size_bytes"),
            }
        )
    elif status == "ALREADY_STORED":
        report["already_stored"] += 1
    elif status in {"BYTEA_REAL_VALID", "BYTEA_GENERIC_VALID"}:
        report["existing_valid"] += 1
    elif status == "GENERIC_ONLY":
        report["generic_skipped"] += 1
    elif status in {"IDENTITY_CONFLICT", "UPLOAD_CATALOG_ID_MISMATCH"}:
        report["identity_conflicts"] += 1
    elif status in {"SOURCE_HTTP_403_COOLDOWN", "SOURCE_COOLDOWN"}:
        report["blocked_sources"] += 1
    else:
        report["no_valid_image"] += 1


def _count_reasons(report: dict[str, Any], outcome: dict[str, Any]) -> None:
    for source_result in outcome.get("source_results", []):
        reason = str(source_result.get("status") or "UNKNOWN")
        counters = report["reason_counts"]
        counters[reason] = int(counters.get(reason, 0)) + 1


def _backfill_temp_files() -> int:
    root = _state_dir() / "outbox" / "images" / BACKFILL_OUTBOX
    if not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())


def _backfill_temp_bytes() -> int:
    root = _state_dir() / "outbox" / "images" / BACKFILL_OUTBOX
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory", action="store_true", help="Read aggregate inventory from Azure"
    )
    parser.add_argument(
        "--source", choices=("araujo", "pacheco"), help="Limit work to one pharmacy"
    )
    parser.add_argument(
        "--phase",
        choices=("known_references", "source_lookup"),
        default="known_references",
        help="Use known image URLs first, then use source product lookups",
    )
    parser.add_argument(
        "--after-catalog-id",
        type=int,
        default=0,
        help="Dry-run cursor; never a development database ID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max production catalog items inspected in this run",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Enable external image downloads and HMAC image uploads",
    )
    parser.add_argument(
        "--include-generic-fallback",
        action="store_true",
        help="Allow explicitly tagged generic fallback images",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit deve estar entre 1 e 100")
    if args.execute and args.limit > 10:
        parser.error("--execute limita cada execução manual a 10 medicamentos")
    if args.after_catalog_id < 0:
        parser.error("--after-catalog-id não pode ser negativo")
    try:
        return asyncio.run(_run(args))
    except (BatchSyncError, ExternalRequestDeferred) as error:
        _emit({"operation": "stopped", "reason": str(error)[:120]})
        return 5
    except RuntimeError as error:
        _emit({"operation": "stopped", "reason": str(error)[:120]})
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
