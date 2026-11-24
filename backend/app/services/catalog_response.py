"""API DTO construction with external image references."""

from collections import defaultdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.catalogo import CatalogoBase
from app.models.medicine_image import MedicineImageLink
from app.models.oferta_farmacia import OfertaFarmacia
from app.schemas.catalogo import CatalogImageCandidate, CatalogoComOfertasOut
from app.services.image_registry import resolve_catalog_images
from app.services.imagens import (
    catalog_image_matches_record,
    embedded_eans,
    gtin_matches,
    is_generic_image_url,
)


def with_registry_images(
    medicine: CatalogoBase,
    generic_image_links: list[MedicineImageLink] | None = None,
) -> CatalogoComOfertasOut:
    offers = getattr(medicine, "ofertas", [])
    urls = resolve_catalog_images(
        medicine.id,
        offers,
        catalog_ean=medicine.codigo_barras,
        require_offer_match=True,
    )
    urls = [url for url in urls if not is_generic_image_url(url)]
    stored_url = medicine.imagem_url
    if (
        medicine.imagem_status == "verificada"
        and stored_url
        and catalog_image_matches_record(medicine)
        and not is_generic_image_url(stored_url)
    ):
        stored_eans = embedded_eans(stored_url)
        if not stored_eans or (
            medicine.codigo_barras
            and all(
                gtin_matches(medicine.codigo_barras, image_ean)
                for image_ean in stored_eans
            )
        ):
            urls = [stored_url, *(url for url in urls if url != stored_url)]
    result = CatalogoComOfertasOut.model_validate(medicine)
    stored = next(
        (
            link
            for link in medicine.image_links
            if link.is_primary
            and not (
                medicine.codigo_barras
                and link.ean
                and not gtin_matches(medicine.codigo_barras, link.ean)
            )
        ),
        None,
    )
    # Only specific links explicitly attached to this canonical catalog row are
    # cross-pharmacy candidates. A conflicting stored EAN disqualifies the link.
    links_by_pharmacy = {}
    for item in medicine.image_links:
        if item.is_generic or item.catalog_id != medicine.id:
            continue
        if (
            medicine.codigo_barras
            and item.ean
            and not gtin_matches(medicine.codigo_barras, item.ean)
        ):
            continue
        current = links_by_pharmacy.get(item.pharmacy_id)
        if current is None or (item.is_primary, item.updated_at, item.id) > (
            current.is_primary,
            current.updated_at,
            current.id,
        ):
            links_by_pharmacy[item.pharmacy_id] = item
    image_candidates = [
        CatalogImageCandidate(
            url=(
                f"/catalogo/medicamentos/{medicine.id}/imagem"
                f"?link_id={item.id}&v={item.blob.sha256[:12]}"
            ),
            pharmacy_id=item.pharmacy_id,
            generic=False,
        )
        for item in links_by_pharmacy.values()
    ]
    generic_by_pharmacy = {}
    for item in generic_image_links or []:
        if item.offer_id is None:
            continue
        current = generic_by_pharmacy.get(item.pharmacy_id)
        if current is None or (item.updated_at, item.id) > (
            current.updated_at,
            current.id,
        ):
            generic_by_pharmacy[item.pharmacy_id] = item
    image_candidates.extend(
        CatalogImageCandidate(
            url=(
                f"/catalogo/ofertas/{item.offer_id}/imagem"
                f"?v={item.blob.sha256[:12]}"
            ),
            pharmacy_id=item.pharmacy_id,
            generic=True,
        )
        for item in generic_by_pharmacy.values()
    )
    offers = []
    for offer in result.ofertas:
        source_offer = next(
            (item for item in medicine.ofertas if item.id == offer.id), None
        )
        link = next(
            (
                item
                for item in (source_offer.image_links if source_offer else [])
                if item.is_generic
            ),
            None,
        )
        image_ref = (
            f"/catalogo/ofertas/{offer.id}/imagem?v={link.blob.sha256[:12]}"
            if link is not None
            else None
        )
        offers.append(
            offer.model_copy(update={"imagem_url": None, "imagem_ref": image_ref})
        )
    return result.model_copy(
        update={
            "imagem_urls": urls,
            "imagem_candidatos": image_candidates,
            "imagem_url": urls[0] if urls else None,
            "imagem_fonte": None,
            "imagem_status": None,
            "imagem_motivo_rejeicao": None,
            "imagem_armazenada": stored is not None,
            "imagem_hash": stored.blob.sha256 if stored is not None else None,
            "ofertas": offers,
        }
    )


def with_registry_images_for_products(
    medicines: Iterable[CatalogoBase], db: Session
) -> list[CatalogoComOfertasOut]:
    """Resolve stored images for a page in bulk, including generic links whose
    source pharmacy offer was omitted by a pharmacy-scoped price query.
    """
    products = list(medicines)
    catalog_ids = [product.id for product in products]
    generic_by_catalog: dict[int, list[MedicineImageLink]] = defaultdict(list)
    if catalog_ids:
        rows = db.execute(
            select(MedicineImageLink, OfertaFarmacia.catalogo_id)
            .join(OfertaFarmacia, OfertaFarmacia.id == MedicineImageLink.offer_id)
            .where(
                OfertaFarmacia.catalogo_id.in_(catalog_ids),
                MedicineImageLink.is_generic.is_(True),
                MedicineImageLink.pharmacy_id == OfertaFarmacia.farmacia_id,
            )
            .options(joinedload(MedicineImageLink.blob))
        ).all()
        for link, catalog_id in rows:
            generic_by_catalog[catalog_id].append(link)
    return [
        with_registry_images(product, generic_by_catalog.get(product.id, []))
        for product in products
    ]
