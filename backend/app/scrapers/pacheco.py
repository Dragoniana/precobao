"""Scraper da Drogarias Pacheco pela busca publica de produtos da VTEX."""

from __future__ import annotations

import argparse
import asyncio
import email.utils
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.scrapers.base import BasePharmacyScraper
from app.scrapers.partner_feed import (
    PartnerFeedForbidden,
    PartnerFeedRateLimited,
    load_authorized_partner_feed,
)
from app.services.imagens import image_delivery_rejection_reason


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = int((retry_at - datetime.now(UTC)).total_seconds())
    return max(0, seconds)


class PachecoScraper(BasePharmacyScraper):
    """Paginate VTEX search and persist offers through the shared scraper base."""

    ITEMS_PER_PAGE = 50
    API_BASE = (
        "https://www.drogariaspacheco.com.br"
        "/api/catalog_system/pub/products/search/medicamentos"
    )
    API_SEARCH = (
        "https://www.drogariaspacheco.com.br/api/catalog_system/pub/products/search"
    )
    FACETS_API = (
        "https://www.drogariaspacheco.com.br"
        "/api/catalog_system/pub/facets/search/medicamentos?map=c"
    )
    MAX_BROAD_SEARCH_OFFSET = 2500
    STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "pacheco"

    def __init__(
        self,
        num_workers: int = 3,
        batch_size: int = 50,
        max_items: int | None = None,
        request_delay: float | None = None,
    ):
        if max_items is not None and max_items < 1:
            raise ValueError("max_items deve ser maior que zero.")
        if request_delay is None:
            request_delay = float(os.getenv("PACHECO_REQUEST_DELAY_SECONDS", "1"))
        if request_delay < 0:
            raise ValueError("request_delay nao pode ser negativo.")

        super().__init__(
            farmacia_cnpj="33438250000167",
            farmacia_razao_social="Drogarias Pacheco S.A.",
            farmacia_nome_fantasia="Pacheco",
            farmacia_endereco="Loja online — drogariaspacheco.com.br",
            num_workers=num_workers,
            batch_size=batch_size,
        )
        self.max_items = max_items
        self.request_delay = request_delay
        self.state_dir = Path(os.getenv("PACHECO_STATE_DIR", str(self.STATE_DIR)))
        self.checkpoint_path = self.state_dir / "checkpoint.json"
        self.stats: dict[str, Any] = self._new_stats({})
        self.items_enqueued = 0
        self._seen_source_ids: set[str] = set()

    @staticmethod
    def _new_stats(previous: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ"),
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "status": "running",
            "complete": False,
            "resumed_after_interruption": previous.get("status") == "running",
            "previous_run_id": previous.get("run_id"),
            "resume_strategy": "replay_from_first_page",
            "last_completed_offset": None,
            "pages_requested": 0,
            "pages_succeeded": 0,
            "pages_failed": 0,
            "products_seen": 0,
            "unique_source_products": 0,
            "duplicate_rows_skipped": 0,
            "products_enqueued": 0,
            "unique_skus": [],
            "valid_eans": [],
            "products_with_image_url": 0,
            "products_without_image": 0,
            "products_skipped": 0,
            "facet_reported_total": None,
            "brand_facets_count": 0,
            "brand_facets_reported_total": 0,
            "brands_processed": 0,
            "brands_unique_partitions": 0,
            "duplicate_brand_facets_skipped": 0,
            "source_coverage_gap": None,
            "root_search_capped": False,
            "retryable": False,
            "last_http_status": None,
            "retry_after_seconds": None,
            "partition_errors": [],
            "partition_discrepancies": [],
            "persistence": {},
            "errors": [],
            "stop_reason": None,
        }

    def _start_run(self) -> None:
        previous = self._read_checkpoint()
        self.stats = self._new_stats(previous)
        self.items_enqueued = 0
        self.persistence_stats = {key: 0 for key in self.persistence_stats}
        self._seen_source_ids.clear()
        self._write_checkpoint()

    def _read_checkpoint(self) -> dict[str, Any]:
        try:
            return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_checkpoint(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".pacheco-checkpoint-", suffix=".tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.stats, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.checkpoint_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @contextmanager
    def _run_lock(self):
        """Prevent concurrent manual and scheduled Pacheco runs on this volume."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_dir / "scraper.lock"
        with lock_path.open("a+b") as lock_file:
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
                raise RuntimeError(
                    "Outra coleta Pacheco ja esta em execucao."
                ) from error
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    async def run(self) -> None:
        """Run with a shared file lock and durable progress checkpoints."""
        with self._run_lock():
            self._start_run()
            print(f"Pacheco run {self.stats['run_id']} started")
            try:
                await super().run()
            except Exception as error:
                self.stats["status"] = "error"
                self.stats["errors"].append(f"{type(error).__name__}: {error}")
                self.stats["stop_reason"] = "unhandled_scraper_error"
                raise
            finally:
                self.stats["persistence"] = self.persistence_stats.copy()
                self.stats["finished_at"] = datetime.now(UTC).isoformat()
                if self.stats["status"] == "running":
                    persistence_ok = not any(
                        self.persistence_stats[key]
                        for key in ("persistence_errors", "image_errors")
                    )
                    if self.stats["complete"] and persistence_ok:
                        self.stats["status"] = "complete"
                    elif self.stats["stop_reason"] == "configured_item_limit":
                        self.stats["status"] = "limited"
                    elif (
                        not self.stats["retryable"]
                        and self.stats["source_coverage_gap"]
                    ):
                        self.stats["status"] = "partial"
                    else:
                        self.stats["status"] = "incomplete"
                self._write_checkpoint()
                print(
                    "Pacheco run finished: "
                    f"status={self.stats['status']} "
                    f"pages={self.stats['pages_succeeded']}/"
                    f"{self.stats['pages_requested']} "
                    f"products={self.stats['products_enqueued']}"
                )

    async def extrair_catalogo(self) -> None:
        """Scan the broad category, then its official brand partitions."""
        try:
            feed_products = await load_authorized_partner_feed(
                prefix="PACHECO_PARTNER_FEED", sku_prefix="PCH"
            )
        except PartnerFeedForbidden as error:
            self.stats["last_http_status"] = 403
            self.stats["retryable"] = False
            self.stats["stop_reason"] = "http_403"
            self.stats["errors"].append(str(error))
            return
        except PartnerFeedRateLimited as error:
            self.stats["last_http_status"] = 429
            self.stats["retry_after_seconds"] = error.retry_after_seconds
            self.stats["retryable"] = False
            self.stats["stop_reason"] = "http_429"
            self.stats["errors"].append(str(error))
            return
        if feed_products is not None:
            for product in feed_products:
                await self.fila.put(product)
            self.stats["products_seen"] = len(feed_products)
            self.stats["unique_source_products"] = len(feed_products)
            self.stats["products_enqueued"] = len(feed_products)
            self.items_enqueued = len(feed_products)
            self.stats["complete"] = True
            self.stats["stop_reason"] = "authorized_partner_feed_validated"
            self._write_checkpoint()
            print(f"Pacheco authorized feed validated: {len(feed_products)} products.")
            return

        if os.getenv("PACHECO_PUBLIC_API_AUTHORIZED", "false").casefold() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            self._page_failed(
                "source_authorization_required: configure an authorized partner feed",
                retryable=False,
            )
            return

        headers = {
            "Accept": "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        async with httpx.AsyncClient(
            headers=headers,
            verify=True,
            follow_redirects=False,
            timeout=30.0,
        ) as session:
            root_complete = await self._scan_partition(
                session,
                self.API_BASE,
                partition="medicamentos",
                max_offset=self.MAX_BROAD_SEARCH_OFFSET,
            )
            if self._limit_reached():
                return
            if not root_complete:
                self.stats["partition_errors"].append("medicamentos")
                if self.stats.get("last_http_status") in {403, 429}:
                    return

            self.stats["facet_requests"] = self.stats.get("facet_requests", 0) + 1
            try:
                facets_response = await self.executar_com_backoff(
                    session.get, self.FACETS_API, timeout=30.0
                )
                if facets_response.status_code not in (200, 206):
                    self._record_http_failure(
                        facets_response,
                        f"Facet request HTTP {facets_response.status_code}",
                    )
                    return
                facets = facets_response.json()
                departments = facets.get("Departments") or []
                brands = facets.get("Brands") or []
                if departments:
                    self.stats["facet_reported_total"] = int(
                        departments[0].get("Quantity", 0)
                    )
                self.stats["brand_facets_count"] = len(brands)
                self.stats["brand_facets_reported_total"] = sum(
                    int(brand.get("Quantity", 0)) for brand in brands
                )
                self._write_checkpoint()
            except Exception as error:
                self._page_failed(
                    f"Facet request failed: {type(error).__name__}: {error}"
                )
                return

            if not brands:
                self._page_failed("Facet response contained no medicine brands")
                return

            seen_brand_paths: set[str] = set()
            for brand in brands:
                if self._limit_reached():
                    break
                link = brand.get("Link") or ""
                path = urlsplit(link).path
                quantity = int(brand.get("Quantity", 0))
                if path in seen_brand_paths:
                    self.stats["duplicate_brand_facets_skipped"] += 1
                    continue
                seen_brand_paths.add(path)
                if not path.startswith("/medicamentos/") or quantity <= 0:
                    self._page_failed(
                        f"Invalid brand partition link or quantity: {brand.get('Name')}"
                    )
                    self.stats["partition_errors"].append(str(brand.get("Name")))
                    continue
                partition = f"brand:{brand.get('Name', path)}"
                partition_url = f"{self.API_SEARCH}{path}"
                succeeded = await self._scan_partition(
                    session,
                    partition_url,
                    partition=partition,
                    expected_count=quantity,
                    max_offset=self.MAX_BROAD_SEARCH_OFFSET,
                    query_map="c,b",
                )
                if succeeded:
                    self.stats["brands_processed"] += 1
                else:
                    self.stats["partition_errors"].append(str(brand.get("Name")))
                    if self.stats.get("last_http_status") in {403, 429}:
                        break
                self._write_checkpoint()

            if self._limit_reached():
                return
            self.stats["brands_unique_partitions"] = len(seen_brand_paths)

            expected_total = self.stats["facet_reported_total"]
            found_total = self.stats["unique_source_products"]
            if expected_total is not None:
                self.stats["source_coverage_gap"] = max(0, expected_total - found_total)
            partitions_ok = not self.stats["partition_errors"]
            source_covered = (
                expected_total is not None and found_total >= expected_total
            )
            if partitions_ok and source_covered:
                self.stats["complete"] = True
                self.stats["stop_reason"] = "facet_total_reconciled"
            elif not self._limit_reached():
                self.stats["complete"] = False
                self.stats["retryable"] = bool(self.stats["partition_errors"])
                self.stats["stop_reason"] = (
                    "source_coverage_gap:"
                    f"{self.stats['source_coverage_gap']} products not enumerated"
                )
            self._write_checkpoint()

    async def _scan_partition(
        self,
        session: Any,
        base_url: str,
        *,
        partition: str,
        expected_count: int | None = None,
        max_offset: int | None = None,
        query_map: str = "c",
    ) -> bool:
        """Read a VTEX partition using its advertised item count when available."""
        offset = 0
        while True:
            if self._limit_reached():
                return True
            if max_offset is not None and offset > max_offset:
                self.stats["root_search_capped"] = True
                self.stats["source_limits"] = self.stats.get("source_limits", [])
                self.stats["source_limits"].append(
                    f"{partition} pagination capped at offset {max_offset}"
                )
                self._write_checkpoint()
                return True

            end = offset + self.ITEMS_PER_PAGE - 1
            params = f"map={query_map}&_from={offset}&_to={end}"
            url = f"{base_url}?{params}"
            self.stats["pages_requested"] += 1
            print(f"Pacheco {partition}: items {offset}-{end}")
            try:
                response = await self.executar_com_backoff(
                    session.get, url, timeout=30.0
                )
                if response.status_code not in (200, 206):
                    self._record_http_failure(
                        response,
                        f"HTTP {response.status_code} at {partition} offset {offset}",
                    )
                    return False
                raw_products = response.json()
                if not isinstance(raw_products, list):
                    self._page_failed(f"Non-list JSON at {partition} offset {offset}")
                    return False
                if not raw_products:
                    if expected_count is not None and offset < expected_count:
                        self.stats["partition_discrepancies"].append(
                            "Empty page before advertised end: "
                            f"{partition} offset {offset}"
                        )
                        return True
                    if expected_count is None:
                        self.stats["complete"] = True
                        self.stats["stop_reason"] = "empty_page"
                    return True
                if len(raw_products) > self.ITEMS_PER_PAGE:
                    self._page_failed(
                        f"Page exceeded {self.ITEMS_PER_PAGE} items at {partition}"
                    )
                    return False

                signature = self._page_signature(raw_products)
                previous_page_keys = self.stats.setdefault("page_signatures", {})
                previous = previous_page_keys.setdefault(partition, [])
                if signature in previous:
                    self._page_failed(
                        f"Repeated result page at {partition} offset {offset}"
                    )
                    return False
                previous.append(signature)

                self.stats["pages_succeeded"] += 1
                self.stats["last_completed_offset"] = offset
                self.stats["products_seen"] += len(raw_products)
                self._queue_source_products(raw_products)
                self._write_checkpoint()
                print(
                    f"Pacheco {partition}: {len(raw_products)} source rows; "
                    f"{self.stats['products_enqueued']} total offers queued"
                )
                if self.request_delay:
                    await asyncio.sleep(self.request_delay)

                if self._limit_reached():
                    return True
                if expected_count is None and len(raw_products) < self.ITEMS_PER_PAGE:
                    self.stats["complete"] = True
                    self.stats["stop_reason"] = "short_page_end_of_partition"
                    return True
                if len(raw_products) < self.ITEMS_PER_PAGE:
                    if (
                        expected_count is not None
                        and offset + len(raw_products) < expected_count
                    ):
                        self.stats["partition_discrepancies"].append(
                            f"Short page before advertised end: "
                            f"{partition} offset {offset}"
                        )
                        offset += self.ITEMS_PER_PAGE
                        if self.request_delay:
                            await asyncio.sleep(self.request_delay)
                        continue
                    if expected_count is None:
                        self.stats["complete"] = True
                        self.stats["stop_reason"] = "short_page_end_of_partition"
                    return True
                offset += self.ITEMS_PER_PAGE
            except Exception as error:
                self._page_failed(
                    f"{type(error).__name__} at {partition} offset {offset}: {error}"
                )
                return False
        return True

    def _queue_source_products(self, raw_products: list[dict[str, Any]]) -> None:
        for item in raw_products:
            source_id = str(item.get("productId") or "")
            items = item.get("items") or []
            sku_id = str(items[0].get("itemId") or "") if items else ""
            identity = source_id or sku_id
            if identity:
                if identity in self._seen_source_ids:
                    self.stats["duplicate_rows_skipped"] += 1
                    continue
                self._seen_source_ids.add(identity)
                self.stats["unique_source_products"] += 1

            try:
                product = self._extrair_produto(item)
                if product is None:
                    self.stats["products_skipped"] += 1
                    continue
                if self.max_items is not None and (
                    self.stats["products_enqueued"] >= self.max_items
                ):
                    return
                self.fila.put_nowait(product)
                self.stats["products_enqueued"] += 1
                self.items_enqueued += 1
                if product.get("imagem_url"):
                    self.stats["products_with_image_url"] += 1
                else:
                    self.stats["products_without_image"] += 1
                if product.get("ean"):
                    self.stats["valid_eans"].append(product["ean"])
                if product.get("sku_interno"):
                    self.stats["unique_skus"].append(product["sku_interno"])
            except Exception as error:
                self.stats["products_skipped"] += 1
                self.stats["errors"].append(
                    f"Could not parse {item.get('productName', '?')}: "
                    f"{type(error).__name__}: {error}"
                )
        self.stats["valid_eans"] = sorted(set(self.stats["valid_eans"]))
        self.stats["unique_skus"] = sorted(set(self.stats["unique_skus"]))

    def _limit_reached(self) -> bool:
        if self.max_items is None or self.stats["products_enqueued"] < self.max_items:
            return False
        self.stats["stop_reason"] = "configured_item_limit"
        return True

    def _record_http_failure(self, response: Any, reason: str) -> None:
        status = int(response.status_code)
        if status in {403, 429}:
            self.stats["last_http_status"] = status
            self.stats["retryable"] = False
            self.stats["stop_reason"] = f"http_{status}"
            if status == 429:
                self.stats["retry_after_seconds"] = _retry_after_seconds(
                    getattr(response, "headers", {}).get("Retry-After")
                )
            self.stats["pages_failed"] += 1
            self.stats["errors"].append(reason)
            self._write_checkpoint()
            print(
                f"Pacheco scan stopped after HTTP {status}; no further source requests."
            )
            return
        self._page_failed(reason)

    def _page_failed(self, reason: str, *, retryable: bool = True) -> None:
        self.stats["pages_failed"] += 1
        self.stats["retryable"] = retryable
        self.stats["errors"].append(reason)
        self.stats["stop_reason"] = reason
        self._write_checkpoint()
        print(f"Pacheco scan incomplete: {reason}")

    @staticmethod
    def _page_signature(products: list[dict[str, Any]]) -> str:
        identifiers = [str(item.get("productId") or "") for item in products]
        if any(identifiers):
            value = "\n".join(identifiers)
        else:
            value = json.dumps(products, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _adaptar_resposta_api(
        self, produtos_brutos: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert each source row independently; skipped rows do not end paging."""
        catalog: list[dict[str, Any]] = []
        for item in produtos_brutos:
            try:
                product = self._extrair_produto(item)
                if product:
                    catalog.append(product)
                else:
                    self.stats["products_skipped"] += 1
            except Exception as error:
                self.stats["products_skipped"] += 1
                name = item.get("productName", "?")
                self.stats["errors"].append(
                    f"Could not parse {name}: {type(error).__name__}: {error}"
                )
                print(f"Pacheco product parse error ({name}): {error}")
        return catalog

    def _extrair_produto(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Extract identifiers and the first store seller's actual offer."""
        items = item.get("items")
        if not items:
            return None

        sku_data = items[0]
        sellers = sku_data.get("sellers")
        if not sellers:
            return None
        offer = sellers[0].get("commertialOffer") or {}
        price = offer.get("Price")
        if price is None or float(price) <= 0:
            return None

        available_quantity = offer.get("AvailableQuantity")
        available_flag = offer.get("IsAvailable")
        if isinstance(available_flag, bool):
            available = available_flag
        elif available_quantity is not None:
            available = int(available_quantity) > 0
        else:
            available = True
        quantity = (
            max(0, int(available_quantity))
            if available and available_quantity is not None
            else 1
            if available
            else 0
        )

        sku_id = str(sku_data.get("itemId") or item.get("productId") or "")
        if not sku_id:
            return None

        name = sku_data.get("name") or item.get("productName") or ""
        ean = sku_data.get("ean") or None
        if isinstance(ean, str):
            ean = ean.strip().replace("-", "").replace(" ", "") or None

        link_text = item.get("linkText") or ""
        if not link_text:
            return None
        product_url = f"https://www.drogariaspacheco.com.br/{link_text}/p"

        return {
            "id": sku_id,
            "sku_interno": f"PCH{sku_id}",
            "ean": ean,
            "name_search": name[:150],
            "preco": float(price),
            "disponivel": available,
            "quantidade_estoque": quantity,
            "link": product_url,
            "imagem_url": self._extrair_imagem(sku_data),
        }

    @staticmethod
    def _extrair_imagem(sku_data: dict[str, Any]) -> str | None:
        """Keep the first image supplied for this exact source product."""
        for image in sku_data.get("images") or []:
            url = image.get("imageUrl")
            if not url:
                continue
            url = str(url)
            if url.startswith("//"):
                url = f"https:{url}"
            if not image_delivery_rejection_reason(url):
                return url
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Coleta do catálogo público Pacheco")
    parser.add_argument(
        "--max-items",
        type=int,
        help="limite opcional para lotes controlados; omitido percorre a busca",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        help="intervalo entre páginas (padrão: PACHECO_REQUEST_DELAY_SECONDS)",
    )
    args = parser.parse_args()
    limit_label = str(args.max_items) if args.max_items is not None else "sem limite"
    print(f"Starting Pacheco catalog scan ({limit_label})")
    scraper = PachecoScraper(max_items=args.max_items, request_delay=args.request_delay)
    try:
        asyncio.run(scraper.run())
    except KeyboardInterrupt:
        print("Pacheco scan interrupted; checkpoint retained")
