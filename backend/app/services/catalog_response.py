"""API DTO construction with external image references."""

from app.models.catalogo import CatalogoBase
from app.schemas.catalogo import CatalogoComOfertasOut
from app.services.image_registry import resolve_catalog_images
from app.services.imagens import (
    catalog_image_matches_record,
    embedded_eans,
    gtin_matches,
    is_generic_image_url,
)


def with_registry_images(medicine: CatalogoBase) -> CatalogoComOfertasOut:
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
    stored = next((link for link in medicine.image_links if link.is_primary), None)
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
            "imagem_url": urls[0] if urls else None,
            "imagem_fonte": None,
            "imagem_status": None,
            "imagem_motivo_rejeicao": None,
            "imagem_armazenada": stored is not None,
            "imagem_hash": stored.blob.sha256 if stored is not None else None,
            "ofertas": offers,
        }
    )
