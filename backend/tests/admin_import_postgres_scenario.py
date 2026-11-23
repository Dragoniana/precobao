"""Real PostgreSQL scenario; run only in a disposable isolated database."""

import base64
import io
import os

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.medicine_images import normalize_medicine_image
from scripts.admin_image_import import import_item, initialize, rollback


def image(color):
    output = io.BytesIO()
    Image.new("RGB", (40, 50), color).save(output, format="PNG")
    return normalize_medicine_image(
        output.getvalue(), declared_content_type="image/png"
    )


def main():
    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        initialize(connection)
    first, second = image("red"), image("blue")
    with Session(engine) as db:
        catalog = CatalogoBase(
            codigo_barras="7897705203095", name_search="test", categorias=[]
        )
        db.add(catalog)
        for n, (source, prefix) in enumerate(
            (("indiana", "IND"), ("pacheco", "PCH"), ("araujo", "ARJ")), 1
        ):
            pharmacy = Farmacia(
                cnpj=str(n).zfill(14),
                razao_social=source,
                nome_fantasia=source,
                endereco_completo="test",
            )
            db.add(pharmacy)
            db.flush()
            db.add(
                OfertaFarmacia(
                    sku_interno=f"{prefix}123",
                    preco=12.5,
                    quantidade_estoque=1,
                    disponivel=True,
                    contexto_chave="global",
                    url_origem=f"https://{source}.example/product",
                    farmacia_id=pharmacy.id,
                    catalogo_id=catalog.id,
                )
            )
        db.commit()
        items = []
        for source, prefix in (
            ("indiana", "IND"),
            ("pacheco", "PCH"),
            ("araujo", "ARJ"),
        ):
            items.append(
                dict(
                    source=source,
                    sku=f"{prefix}123",
                    ean="7897705203095",
                    product_url=f"https://{source}.example/product",
                    source_image_url=f"https://{source}.example/photo.jpg",
                    is_generic_fallback=False,
                    content_type=first.content_type,
                    content_sha256=first.sha256,
                    content_base64=base64.b64encode(first.content).decode(),
                )
            )
        for item in items:
            with db.begin():
                assert import_item(db, "insert", item).startswith("INSERTED")
        with db.begin():
            assert len(db.scalars(select(MedicineImageBlob)).all()) == 1
            assert len(db.scalars(select(MedicineImageLink)).all()) == 3
            assert import_item(db, "insert", items[0]) == "CHECKPOINT"
        replacement = items[0] | dict(
            content_type=second.content_type,
            content_sha256=second.sha256,
            content_base64=base64.b64encode(second.content).decode(),
        )
        with db.begin():
            assert import_item(db, "replace", replacement).startswith("REPLACED")
        with db.begin():
            assert rollback(db, "replace") == 1
        with db.begin():
            assert all(
                b.sha256 == first.sha256
                for b in db.scalars(
                    select(MedicineImageBlob).join(MedicineImageLink)
                ).all()
            )
        with db.begin():
            assert rollback(db, "insert") == 3
            assert not db.scalars(select(MedicineImageLink)).all()
            assert all(
                o.preco == 12.5 for o in db.scalars(select(OfertaFarmacia)).all()
            )
    print(
        "POSTGRES_SCENARIO_PASSED: three sources, insertion, replacement, "
        "deduplication, checkpoint, rollback, prices"
    )


if __name__ == "__main__":
    main()
