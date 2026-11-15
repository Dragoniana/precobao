"""Fill missing Pacheco offer image references without touching prices or stock."""

from __future__ import annotations

import argparse
import asyncio
from email.utils import parsedate_to_datetime
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.catalogo import CatalogoBase
from app.models.oferta_farmacia import OfertaFarmacia
from app.scrapers.pacheco import PachecoScraper
from app.services.image_registry import _path, _read, upsert_image_references
from app.services.imagens import image_delivery_rejection_reason, verify_image_url

PACHECO_ID = 3
PACHECO_API = PachecoScraper.API_SEARCH
USER_AGENT = "PrecoBao/1.0 (+https://precobao.app)"
SAVE_EVERY = 25


def _now() -> str:
    return datetime.now(UTC).isoformat()


def exact_product_images(payload: Any, product_id: str) -> list[str]:
    """Return safe image URLs only from the exact VTEX product ID response."""
    if not isinstance(payload, list):
        return []
    products = [
        product
        for product in payload
        if isinstance(product, dict)
        and str(product.get("productId") or "") == str(product_id)
    ]
    urls: list[str] = []
    for product in products:
        for sku in product.get("items") or []:
            if not isinstance(sku, dict):
                continue
            for image in sku.get("images") or []:
                if not isinstance(image, dict):
                    continue
                raw_url = image.get("imageUrl")
                if not raw_url:
                    continue
                url = urljoin("https://www.drogariaspacheco.com.br/", str(raw_url))
                if url.startswith("http://"):
                    continue
                if not image_delivery_rejection_reason(url) and url not in urls:
                    urls.append(url)
    return urls


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _locked(lock_path: Path):
    class Lock:
        def __enter__(self):
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = lock_path.open("a+b")
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                if self.stream.read(1) == b"":
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self

        def __exit__(self, *_exc):
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()

    return Lock()


def _work_items() -> list[dict[str, Any]]:
    registry = _read()
    existing_offer_ids = {
        int(record["offer_id"])
        for record in registry["records"]
        if record.get("pharmacy_id") == PACHECO_ID
        and record.get("offer_id") is not None
    }
    with SessionLocal() as db:
        rows = db.execute(
            select(
                OfertaFarmacia.id,
                OfertaFarmacia.catalogo_id,
                OfertaFarmacia.sku_interno,
                OfertaFarmacia.url_origem,
                CatalogoBase.codigo_barras,
            )
            .join(CatalogoBase, CatalogoBase.id == OfertaFarmacia.catalogo_id)
            .where(OfertaFarmacia.farmacia_id == PACHECO_ID)
            .order_by(OfertaFarmacia.id)
        ).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for offer_id, catalog_id, sku, product_url, ean in rows:
        if int(offer_id) in existing_offer_ids or not sku or not sku.startswith("PCH"):
            continue
        product_id = sku[3:]
        if not product_id.isdigit():
            continue
        grouped.setdefault(product_id, []).append(
            {
                "offer_id": int(offer_id),
                "catalog_id": int(catalog_id),
                "sku": sku,
                "ean": ean,
                "product_url": product_url,
            }
        )
    return [
        {"product_id": product_id, "offers": offers}
        for product_id, offers in sorted(grouped.items(), key=lambda item: int(item[0]))
    ]


def _load_state(path: Path) -> dict[str, Any] | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("pharmacy_id") != PACHECO_ID or state.get("status") == "complete":
        return None
    return state


def _retry_after(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return min(300.0, max(1.0, float(value)))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            return min(
                300.0,
                max(1.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()),
            )
        except (TypeError, ValueError, OverflowError):
            return min(60.0, 2.0**attempt)


async def _fetch_product(
    client: httpx.AsyncClient, product_id: str, max_retries: int
) -> list[str] | None:
    for attempt in range(max_retries + 1):
        response = await client.get(
            PACHECO_API,
            params={"fq": f"productId:{product_id}"},
        )
        if response.status_code in {429, 500, 502, 503, 504}:
            if attempt >= max_retries:
                return None
            await asyncio.sleep(_retry_after(response, attempt))
            continue
        if response.status_code in {400, 401, 403}:
            raise RuntimeError(
                f"Pacheco recusou a consulta por produto (HTTP {response.status_code}); "
                "coleta interrompida sem tentar outra rota."
            )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return exact_product_images(response.json(), product_id)
    return None


async def run(limit: int | None, delay_seconds: float, max_retries: int) -> None:
    registry_path = _path()
    checkpoint_path = registry_path.with_name("pacheco-image-backfill-progress.json")
    lock_path = registry_path.with_name("pacheco-image-backfill.lock")
    with _locked(lock_path):
        state = _load_state(checkpoint_path)
        if state is None:
            state = {
                "pharmacy_id": PACHECO_ID,
                "started_at": _now(),
                "updated_at": _now(),
                "status": "running",
                "next_index": 0,
                "work_items": _work_items(),
                "products_requested": 0,
                "products_matched": 0,
                "images_saved": 0,
                "no_image": 0,
                "invalid_images": 0,
                "errors": 0,
            }
            _atomic_json(checkpoint_path, state)
        items = state["work_items"]
        end_index = (
            len(items)
            if limit is None
            else min(len(items), int(state["next_index"]) + limit)
        )
        print(
            f"Pacheco image-only: pending={len(items) - int(state['next_index'])}; "
            f"batch={end_index - int(state['next_index'])}; delay={delay_seconds}s",
            flush=True,
        )
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
            headers=headers,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        ) as client:
            while int(state["next_index"]) < end_index:
                index = int(state["next_index"])
                item = items[index]
                started = time.monotonic()
                try:
                    urls = await _fetch_product(client, item["product_id"], max_retries)
                    state["products_requested"] += 1
                    if urls is None:
                        state["errors"] += 1
                    elif not urls:
                        state["no_image"] += 1
                    else:
                        state["products_matched"] += 1
                        selected_url = None
                        for candidate in urls:
                            check = await verify_image_url(candidate)
                            if check.ok:
                                selected_url = candidate
                                break
                            state["invalid_images"] += 1
                            await asyncio.sleep(delay_seconds)
                        if selected_url:
                            records = [
                                {
                                    "image_url": selected_url,
                                    "pharmacy_id": PACHECO_ID,
                                    "pharmacy": "Pacheco",
                                    "sku": offer["sku"],
                                    "ean": offer["ean"],
                                    "product_id": item["product_id"],
                                    "product_url": offer["product_url"],
                                    "catalog_id": offer["catalog_id"],
                                    "offer_id": offer["offer_id"],
                                    "identity_confirmed": True,
                                    "allow_generic": True,
                                }
                                for offer in item["offers"]
                            ]
                            saved = upsert_image_references(records)
                            state["images_saved"] += sum(saved)
                        else:
                            state["no_image"] += 1
                except RuntimeError:
                    state["status"] = "stopped_source_rejection"
                    state["errors"] += 1
                    state["updated_at"] = _now()
                    _atomic_json(checkpoint_path, state)
                    raise
                except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
                    state["errors"] += 1
                    print(
                        f"Pacheco product_id={item['product_id']} "
                        f"error={type(error).__name__}",
                        flush=True,
                    )
                state["next_index"] = index + 1
                state["updated_at"] = _now()
                if (
                    state["next_index"] % SAVE_EVERY == 0
                    or state["next_index"] == end_index
                ):
                    _atomic_json(checkpoint_path, state)
                    print(
                        f"progress={state['next_index']}/{len(items)} "
                        f"matched={state['products_matched']} saved={state['images_saved']} "
                        f"no_image={state['no_image']} errors={state['errors']}",
                        flush=True,
                    )
                await asyncio.sleep(
                    max(0.0, delay_seconds - (time.monotonic() - started))
                )
        if state["next_index"] >= len(items):
            state["status"] = "complete"
            state["finished_at"] = _now()
        else:
            state["status"] = "paused_after_batch"
        state["updated_at"] = _now()
        _atomic_json(checkpoint_path, state)
        print(
            f"finished status={state['status']} next={state['next_index']}/{len(items)} "
            f"saved={state['images_saved']} no_image={state['no_image']} "
            f"invalid={state['invalid_images']} errors={state['errors']}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preenche apenas referências de imagem Pacheco; não atualiza ofertas."
    )
    parser.add_argument("--limit", type=int, help="limita este lote controlado")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="intervalo mínimo entre consultas",
    )
    parser.add_argument("--max-retries", type=int, default=4)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit deve ser maior que zero")
    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds não pode ser menor que 1 segundo")
    asyncio.run(run(args.limit, args.delay_seconds, args.max_retries))


if __name__ == "__main__":
    main()
