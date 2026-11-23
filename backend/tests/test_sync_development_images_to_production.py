from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session

import scripts.sync_development_images_to_production as image_sync
from app.models import Base, CatalogoBase, Farmacia, OfertaFarmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.services.medicine_images import normalize_medicine_image
from scripts.sync_development_images_to_production import (
    _candidate_key,
    _catalog_image_url,
    _check_candidates,
    _load_checkpoint,
    _load_descriptors,
    _load_scraped_image_candidates,
    _source_image_bytes,
    _status_counts_by_source,
    _write_checkpoint,
)


def _normalized_image() -> tuple[bytes, str]:
    stream = BytesIO()
    Image.new("RGB", (40, 50), (110, 80, 150)).save(stream, format="PNG")
    image = normalize_medicine_image(
        stream.getvalue(), declared_content_type="image/png"
    )
    return image.content, image.content_type


def test_migration_status_report_is_grouped_by_source():
    candidates = [
        SimpleNamespace(source="araujo"),
        SimpleNamespace(source="pacheco"),
        SimpleNamespace(source="pacheco"),
    ]
    results = [
        {"status": "ELIGIBLE"},
        {"status": "IMAGE_ALREADY_EXISTS"},
        {"status": "SOURCE_NOT_ENABLED"},
    ]

    assert _status_counts_by_source(candidates, results) == {
        "araujo": {"ELIGIBLE": 1},
        "pacheco": {"IMAGE_ALREADY_EXISTS": 1, "SOURCE_NOT_ENABLED": 1},
    }


def test_atomic_state_replace_retries_transient_permission_error(tmp_path, monkeypatch):
    temporary = tmp_path / "state.tmp"
    target = tmp_path / "state.json"
    temporary.write_text("complete", encoding="utf-8")
    real_replace = Path.replace
    attempts = 0

    def replace_with_one_transient_lock(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(13, "temporarily locked")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace_with_one_transient_lock)

    image_sync._replace_with_retry(temporary, target)

    assert attempts == 2
    assert target.read_text(encoding="utf-8") == "complete"
    assert not temporary.exists()


def test_error_diagnostics_omit_exception_text_and_paths():
    try:
        raise PermissionError("sensitive-value", "C:\\private\\secret.txt")
    except PermissionError as error:
        diagnostics = image_sync._safe_error_diagnostics(error)

    assert diagnostics["error_type"] == "PermissionError"
    assert diagnostics["frames"][-1]["file"] == Path(__file__).name
    rendered = repr(diagnostics)
    assert "sensitive-value" not in rendered
    assert "private" not in rendered
    assert "secret.txt" not in rendered


def test_catalog_image_delivery_url_removes_only_internal_api_prefix(monkeypatch):
    monkeypatch.setenv(
        "SCRAPER_IMAGE_INGEST_URL",
        "https://precobao.app/backend/api/v1/internal/medicine-images",
    )
    assert (
        _catalog_image_url(12345)
        == "https://precobao.app/backend/catalogo/medicamentos/12345/imagem"
    )


def test_execute_requires_a_small_explicit_limit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["image-sync", "--execute"])
    with pytest.raises(SystemExit):
        image_sync.main()

    monkeypatch.setattr("sys.argv", ["image-sync", "--execute", "--limit", "51"])
    with pytest.raises(SystemExit):
        image_sync.main()

    monkeypatch.setattr(
        "sys.argv", ["image-sync", "--execute", "--all-eligible", "--limit", "3"]
    )
    with pytest.raises(SystemExit):
        image_sync.main()


def test_migration_checkpoint_is_atomic_and_reusable(tmp_path, monkeypatch):
    report = tmp_path / "development-image-migration-report.json"
    monkeypatch.setattr(image_sync, "_report_path", lambda: report)
    candidate = SimpleNamespace(
        source="pacheco",
        sku="PCH155349",
        ean="7897705203095",
        content_sha256="a" * 64,
    )
    key = _candidate_key(candidate)
    _write_checkpoint({key: "STORED"})

    assert _load_checkpoint() == {key: "STORED"}
    assert not report.with_name("development-image-migration-checkpoint.tmp").exists()


@pytest.mark.asyncio
async def test_migration_check_retries_after_rate_limit(monkeypatch):
    monkeypatch.setenv(
        "SCRAPER_IMAGE_INGEST_URL",
        "https://precobao.app/backend/api/v1/internal/medicine-images",
    )
    candidate = image_sync.Candidate(
        dev_catalog_id=1,
        blob_id=None,
        source="pacheco",
        sku="PCH155349",
        ean="7897705203095",
        product_url="https://www.drogariaspacheco.com.br/produto/155349/p",
        source_image_url="https://drogariaspacheco.vteximg.com.br/7897705203095.jpg",
        content_type="image/webp",
        content_sha256="a" * 64,
        size_bytes=64,
        is_generic_fallback=False,
    )
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "source": candidate.source,
                        "sku": candidate.sku,
                        "ean": candidate.ean,
                        "content_sha256": candidate.content_sha256,
                        "status": "ELIGIBLE",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _check_candidates(client, [candidate], "test-secret-" * 4)

    assert calls == 2
    assert result[0]["status"] == "ELIGIBLE"


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


def test_scrape_archive_is_read_as_primary_candidate_source():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    archive_metadata = MetaData()
    archive = Table(
        "imagens_scrape",
        archive_metadata,
        Column("id", Integer, primary_key=True),
        Column("image_url", Text, nullable=False),
        Column("content_type", String(100), nullable=False),
        Column("sha256", String(64), nullable=False),
        Column("size_bytes", Integer, nullable=False),
        Column("content", LargeBinary, nullable=False),
    )
    archive_metadata.create_all(engine)
    raw_stream = BytesIO()
    Image.new("RGB", (64, 80), (95, 145, 190)).save(raw_stream, format="PNG")
    raw = raw_stream.getvalue()
    raw_digest = hashlib.sha256(raw).hexdigest()
    image_url = (
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/1138787/7897705203095.jpg"
    )
    with Session(engine) as db:
        pharmacy = Farmacia(
            cnpj="12345678000200",
            razao_social="Drogarias Pacheco S.A.",
            nome_fantasia="Drogarias Pacheco",
            endereco_completo="Rio de Janeiro - RJ",
        )
        catalog = CatalogoBase(
            codigo_barras="7897705203095",
            name_search="Medicamento de teste Pacheco",
            categorias=[],
        )
        db.add_all([pharmacy, catalog])
        db.flush()
        offer = OfertaFarmacia(
            sku_interno="PCH155349",
            preco=15.25,
            quantidade_estoque=1,
            disponivel=True,
            url_origem="https://www.drogariaspacheco.com.br/produto/155349/p",
            imagem_url=image_url,
            contexto_chave="global",
            farmacia_id=pharmacy.id,
            catalogo_id=catalog.id,
        )
        db.add(offer)
        db.execute(
            archive.insert().values(
                image_url=image_url,
                content_type="image/png",
                sha256=raw_digest,
                size_bytes=len(raw),
                content=raw,
            )
        )
        db.commit()

        candidates, counts = _load_scraped_image_candidates(db)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.source_store == "imagens_scrape"
        assert candidate.source == "pacheco"
        assert candidate.sku == "PCH155349"
        assert candidate.ean == "7897705203095"
        assert candidate.content_type in {"image/jpeg", "image/webp"}
        assert (
            hashlib.sha256(_source_image_bytes(db, candidate)).hexdigest()
            == candidate.content_sha256
        )
        assert counts["scraped_image_records"] == 1
        assert counts["scraped_size_mismatches"] == 0
        assert counts["scraped_pacheco_valid_images"] == 1

    engine.dispose()
