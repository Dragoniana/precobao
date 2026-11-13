"""Durable image URL registry, deliberately independent from PostgreSQL."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.services.imagens import embedded_eans, gtin_matches, is_generic_image_url

_lock = RLock()
_cached_stat: tuple[str, int, int] | None = None
_cached_records: list[dict[str, Any]] = []
_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "image_references"
    / "references.json"
)


@contextmanager
def _registry_guard():
    """Serialize read-modify-write across API and scraper processes."""
    with _lock:
        lock_path = _path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                if stream.read(1) == b"":
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _path() -> Path:
    return Path(os.getenv("IMAGE_REFERENCE_STORE_PATH", str(_DEFAULT_PATH)))


def _read() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"schema": "preco-bao.image-registry", "version": 1, "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "preco-bao.image-registry" or data.get("version") != 1:
        raise ValueError(f"Unsupported image registry format at {path}")
    return data


def _read_records() -> list[dict[str, Any]]:
    global _cached_stat, _cached_records
    path = _path()
    if not path.exists():
        return []
    stat = path.stat()
    signature = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if signature != _cached_stat:
            _cached_records = _read()["records"]
            _cached_stat = signature
        return _cached_records


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".images-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _record_key(record: dict[str, Any]) -> str:
    offer_id = record.get("offer_id")
    if offer_id is not None:
        return str(offer_id)
    source_identity = (
        record.get("sku")
        or record.get("product_id")
        or record.get("product_url")
        or record.get("ean")
        or ""
    )
    source_id = record.get("pharmacy_id")
    catalog_id = record.get("catalog_id") or ""
    return f"{source_id}:{catalog_id}:{source_identity}"


def normalize_image_url(
    image_url: str | None, product_url: str | None = None
) -> str | None:
    if not image_url:
        return None
    url = urljoin(product_url or "", image_url.strip())
    # New scrapers need no frontend allowlist entry. Keep transport and SSRF
    # protections here; identity is established by the persisted offer link.
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return None
    if not host or host.casefold() in {"localhost", "metadata.google.internal"}:
        return None
    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    ):
        return None
    if not any(
        parsed.path.casefold().endswith(ext)
        for ext in (".jpg", ".jpeg", ".png", ".webp")
    ):
        return None
    return url


def upsert_image_reference(
    *,
    image_url: str | None,
    pharmacy_id: int,
    pharmacy: str,
    sku: str | None,
    ean: str | None,
    product_id: str | None,
    product_url: str | None,
    catalog_id: int | None,
    offer_id: int | None,
    identity_confirmed: bool = False,
    allow_generic: bool = False,
) -> bool:
    """Add/update a source-offer reference. Missing images never erase old refs."""
    return upsert_image_references(
        [
            {
                "image_url": image_url,
                "pharmacy_id": pharmacy_id,
                "pharmacy": pharmacy,
                "sku": sku,
                "ean": ean,
                "product_id": product_id,
                "product_url": product_url,
                "catalog_id": catalog_id,
                "offer_id": offer_id,
                "identity_confirmed": identity_confirmed,
                "allow_generic": allow_generic,
            }
        ]
    )[0]


def upsert_image_references(references: list[dict[str, Any]]) -> list[bool]:
    """Atomically merge many offer images with one JSON read/write."""
    global _cached_stat

    prepared: list[dict[str, Any] | None] = []
    for reference in references:
        url = normalize_image_url(
            reference.get("image_url"), reference.get("product_url")
        )
        if not url:
            prepared.append(None)
            continue
        prepared.append(
            {
                **reference,
                "image_url": url,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    if not any(record is not None for record in prepared):
        return [False] * len(prepared)

    with _registry_guard():
        data = _read()
        records = data["records"]
        positions = {_record_key(record): index for index, record in enumerate(records)}
        for record in prepared:
            if record is None:
                continue
            key = _record_key(record)
            index = positions.get(key)
            if index is None:
                positions[key] = len(records)
                records.append(record)
            else:
                records[index] = {**records[index], **record}
        _write(data)
        _cached_stat = None
    return [record is not None for record in prepared]


def resolve_catalog_images(
    catalog_id: int,
    offers: list[Any] | None = None,
    catalog_ean: str | None = None,
    require_offer_match: bool = False,
) -> list[str]:
    """Resolve offer references with matching identity and non-generic artwork."""
    linked = {
        int(o.id): o for o in (offers or []) if getattr(o, "id", None) is not None
    }
    linked_offers = list(linked.values())
    linked_pharmacy_ids = {
        int(o.farmacia_id)
        for o in linked.values()
        if getattr(o, "farmacia_id", None) is not None
    }
    records = _read_records()
    found: list[str] = []
    for record in records:
        offer_id = record.get("offer_id")
        exact_offer_identity = False
        if record.get("catalog_id") != catalog_id:
            continue
        if offer_id is not None and linked and int(offer_id) not in linked:
            continue
        if require_offer_match:
            record_pharmacy = record.get("pharmacy_id")
            record_sku = record.get("sku")
            if offer_id is not None:
                offer = linked.get(int(offer_id))
                if offer is None:
                    continue
                if record_pharmacy is not None and int(record_pharmacy) != int(
                    getattr(offer, "farmacia_id", -1)
                ):
                    continue
                offer_sku = getattr(offer, "sku_interno", None)
                if record_sku and offer_sku and record_sku != offer_sku:
                    continue
                exact_offer_identity = bool(
                    record.get("identity_confirmed") is True
                    and record_sku
                    and offer_sku
                    and record_sku == offer_sku
                )
            elif linked_offers:
                same_offer = any(
                    (
                        record_pharmacy is None
                        or record_pharmacy == getattr(offer, "farmacia_id", None)
                    )
                    and (
                        not record_sku
                        or record_sku == getattr(offer, "sku_interno", None)
                    )
                    for offer in linked_offers
                )
                if not same_offer:
                    continue
            else:
                record_ean = record.get("ean")
                if (
                    not catalog_ean
                    or not record_ean
                    or not gtin_matches(catalog_ean, record_ean)
                ):
                    continue
        if (
            offer_id is None
            and linked_pharmacy_ids
            and record.get("pharmacy_id") is not None
            and int(record["pharmacy_id"]) not in linked_pharmacy_ids
        ):
            continue
        url = normalize_image_url(record.get("image_url"), record.get("product_url"))
        allow_generic = record.get("allow_generic") is True and exact_offer_identity
        if not url or url in found or (is_generic_image_url(url) and not allow_generic):
            continue

        record_ean = record.get("ean")
        if catalog_ean and record_ean and not gtin_matches(catalog_ean, record_ean):
            continue
        image_eans = embedded_eans(url)
        if image_eans and (
            not catalog_ean
            or not all(gtin_matches(catalog_ean, image_ean) for image_ean in image_eans)
        ) and not exact_offer_identity:
            continue
        found.append(url)
    return found


def has_offer_image_reference(*, offer_id: int, catalog_id: int, sku: str) -> bool:
    """Return whether this exact offer already has a displayable image reference."""
    for record in _read_records():
        if record.get("offer_id") != offer_id or record.get("catalog_id") != catalog_id:
            continue
        if record.get("sku") != sku:
            continue
        url = normalize_image_url(record.get("image_url"), record.get("product_url"))
        if not url:
            continue
        if is_generic_image_url(url) and not (
            record.get("allow_generic") is True
            and record.get("identity_confirmed") is True
        ):
            continue
        return True
    return False


def migrate_legacy_references(db: Any, backup_path: Path) -> dict[str, int]:
    """Copy legacy URL references once and snapshot source rows before API cutover."""
    from app.models import CatalogoBase, OfertaFarmacia
    from app.models.farmacia import Farmacia

    products = db.query(CatalogoBase).filter(CatalogoBase.imagem_url.is_not(None)).all()
    offers = (
        db.query(OfertaFarmacia).filter(OfertaFarmacia.imagem_url.is_not(None)).all()
    )
    pharmacy_by_id = {p.id: p for p in db.query(Farmacia).all()}
    if not backup_path.exists():
        snapshot = {
            "schema": "preco-bao.legacy-image-backup",
            "created_at": datetime.now(UTC).isoformat(),
            "catalog": [
                {
                    "catalog_id": p.id,
                    "ean": p.codigo_barras,
                    "image_url": p.imagem_url,
                    "source": p.imagem_fonte,
                    "sku": p.imagem_sku_origem,
                    "product_url": getattr(p, "imagem_url_produto_origem", None),
                }
                for p in products
            ],
            "offers": [
                {
                    "offer_id": o.id,
                    "catalog_id": o.catalogo_id,
                    "pharmacy_id": o.farmacia_id,
                    "sku": o.sku_interno,
                    "product_url": o.url_origem,
                    "image_url": o.imagem_url,
                    "ean": o.produto_catalogo.codigo_barras
                    if o.produto_catalogo
                    else None,
                }
                for o in offers
            ],
        }
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("x", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)

    with _registry_guard():
        data = _read()
        records = data["records"]
        positions = {}
        for index, record in enumerate(records):
            key = _record_key(record)
            positions[key] = index
        rejected = 0
        imported = 0

        def merge(record: dict[str, Any], identity: str) -> None:
            nonlocal rejected, imported
            url = normalize_image_url(
                record.get("image_url"), record.get("product_url")
            )
            if not url:
                rejected += 1
                return
            record["image_url"] = url
            record["updated_at"] = datetime.now(UTC).isoformat()
            index = positions.get(identity)
            if index is None:
                positions[identity] = len(records)
                records.append(record)
            else:
                records[index] = {**records[index], **record}
            imported += 1

        for offer in offers:
            pharmacy = pharmacy_by_id.get(offer.farmacia_id)
            merge(
                {
                    "pharmacy_id": offer.farmacia_id,
                    "pharmacy": pharmacy.nome_fantasia
                    if pharmacy
                    else str(offer.farmacia_id),
                    "sku": offer.sku_interno,
                    "ean": offer.produto_catalogo.codigo_barras
                    if offer.produto_catalogo
                    else None,
                    "product_id": None,
                    "product_url": offer.url_origem,
                    "catalog_id": offer.catalogo_id,
                    "offer_id": offer.id,
                    "image_url": offer.imagem_url,
                },
                str(offer.id),
            )
        for product in products:
            pharmacy_name = product.imagem_fonte or "Legacy catalog"
            pharmacy = next(
                (
                    item
                    for item in pharmacy_by_id.values()
                    if item.nome_fantasia.casefold() == pharmacy_name.casefold()
                ),
                None,
            )
            sku = product.imagem_sku_origem
            source_id = pharmacy.id if pharmacy else 0
            identity = (
                f"{source_id}:{product.id}:{sku or product.codigo_barras or product.id}"
            )
            merge(
                {
                    "pharmacy_id": source_id,
                    "pharmacy": pharmacy_name,
                    "sku": sku,
                    "ean": product.codigo_barras,
                    "product_id": None,
                    "product_url": None,
                    "catalog_id": product.id,
                    "offer_id": None,
                    "image_url": product.imagem_url,
                },
                identity,
            )
        _write(data)
    return {
        "catalog_rows": len(products),
        "offer_rows": len(offers),
        "imported_references": imported,
        "rejected_references": rejected,
        "registry_records": len(records),
    }
