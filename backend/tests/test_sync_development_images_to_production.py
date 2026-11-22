from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import scripts.sync_development_images_to_production as image_sync
from app.models import Base, CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.medicine_images import normalize_medicine_image
from scripts.sync_development_images_to_production import _load_descriptors


def _normalized_image() -> tuple[bytes, str]:
    stream = BytesIO()
    Image.new("RGB", (40, 50), (110, 80, 150)).save(stream, format="PNG")
    image = normalize_medicine_image(
        stream.getvalue(), declared_content_type="image/png"
    )
    return image.content, image.content_type


def test_candidate_uses_development_ean_and_real_image_priority(monkeypatch):
    # Emulate a development schema before c4a8f32d1e9b added is_generic.
    real_inspect = image_sync.inspect

    class LegacySchemaInspector:
        def __init__(self, bind):
            self.delegate = real_inspect(bind)

        def get_columns(self, table_name, *args, **kwargs):
            return [
                column
                for column in self.delegate.get_columns(table_name, *args, **kwargs)
                if column["name"] != "is_generic"
            ]

    monkeypatch.setattr(image_sync, "inspect", LegacySchemaInspector)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    content, content_type = _normalized_image()
    digest = hashlib.sha256(content).hexdigest()
    generic_stream = BytesIO()
    Image.new("RGB", (40, 50), (220, 180, 70)).save(generic_stream, format="PNG")
    generic_image = normalize_medicine_image(
        generic_stream.getvalue(), declared_content_type="image/png"
    )
    generic_content = generic_image.content
    with Session(engine) as db:
        pharmacy = Farmacia(
            cnpj="12345678000195",
            razao_social="Drogaria Araujo S.A.",
            nome_fantasia="Drogaria Araújo",
            endereco_completo="Belo Horizonte - MG",
        )
        catalog = CatalogoBase(
            codigo_barras="7897705203095",
            name_search="Medicamento de teste",
            categorias=[],
        )
        generic_blob = MedicineImageBlob(
            sha256=hashlib.sha256(generic_content).hexdigest(),
            content_type=generic_image.content_type,
            image_data=generic_content,
            size_bytes=len(generic_content),
            width=40,
            height=50,
        )
        blob = MedicineImageBlob(
            sha256=digest,
            content_type=content_type,
            image_data=content,
            size_bytes=len(content),
            width=40,
            height=50,
        )
        db.add_all([pharmacy, catalog, generic_blob, blob])
        db.flush()
        offer = OfertaFarmacia(
            sku_interno="ARJ101392",
            preco=12.5,
            quantidade_estoque=1,
            disponivel=True,
            url_origem="https://www.araujo.com.br/produto/101392.html",
            contexto_chave="global",
            farmacia_id=pharmacy.id,
            catalogo_id=catalog.id,
        )
        db.add(offer)
        db.flush()
        db.add(
            MedicineImageLink(
                blob_id=generic_blob.id,
                catalog_id=None,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id,
                sku="ARJ101392",
                ean=catalog.codigo_barras,
                source="araujo",
                source_image_url="https://www.araujo.com.br/on/demandware.static/Sites-Araujo-Site/-/default/dw4f56ef98/images/generico.webp",
                is_primary=False,
                is_generic=True,
            )
        )
        db.flush()
        db.add(
            MedicineImageLink(
                blob_id=blob.id,
                catalog_id=catalog.id,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id,
                sku="ARJ101392",
                ean=catalog.codigo_barras,
                source="araujo",
                source_image_url="https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
                is_primary=True,
                is_generic=False,
            )
        )
        db.commit()

        candidates, counts, distinct_medicines = _load_descriptors(db)

    engine.dispose()
    assert distinct_medicines == 1
    assert len(candidates) == 1
    assert candidates[0].ean == "7897705203095"
    assert candidates[0].source == "araujo"
    assert candidates[0].content_sha256 == digest
    assert candidates[0].is_generic_fallback is False
    assert candidates[0].size_bytes == len(content)
    assert counts["eligible_source_candidates"] == 1
    assert counts["invalid_generic_classification"] == 1


def test_invalid_blob_hash_is_not_a_candidate():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    content, content_type = _normalized_image()
    with Session(engine) as db:
        pharmacy = Farmacia(
            cnpj="12345678000195",
            razao_social="Drogaria Araujo S.A.",
            nome_fantasia="Drogaria Araújo",
            endereco_completo="Belo Horizonte - MG",
        )
        catalog = CatalogoBase(
            codigo_barras="7897705203095",
            name_search="Medicamento de teste",
            categorias=[],
        )
        blob = MedicineImageBlob(
            sha256="f" * 64,
            content_type=content_type,
            image_data=content,
            size_bytes=len(content),
            width=40,
            height=50,
        )
        db.add_all([pharmacy, catalog, blob])
        db.flush()
        offer = OfertaFarmacia(
            sku_interno="ARJ101392",
            preco=12.5,
            quantidade_estoque=1,
            disponivel=True,
            url_origem="https://www.araujo.com.br/produto/101392.html",
            contexto_chave="global",
            farmacia_id=pharmacy.id,
            catalogo_id=catalog.id,
        )
        db.add(offer)
        db.flush()
        db.add(
            MedicineImageLink(
                blob_id=blob.id,
                catalog_id=catalog.id,
                pharmacy_id=pharmacy.id,
                offer_id=offer.id,
                sku="ARJ101392",
                ean=catalog.codigo_barras,
                source="araujo",
                source_image_url="https://i3-imagens-prd.araujo.com.br/101392/7897705203095.jpg",
                is_primary=True,
                is_generic=False,
            )
        )
        db.commit()
        candidates, counts, _ = _load_descriptors(db)

    engine.dispose()
    assert candidates == []
    assert counts["invalid_hash_or_size"] == 1
