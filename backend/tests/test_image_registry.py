import json
from types import SimpleNamespace

from app.scrapers.pacheco import PachecoScraper
from app.services import image_registry
from app.services.image_registry import (
    normalize_image_url,
    resolve_catalog_image_candidates,
    resolve_catalog_images,
    upsert_image_reference,
    upsert_image_references,
)
from app.services.imagens import is_generic_image_url


def test_registry_persists_generic_image_and_updates_without_erasing(
    monkeypatch, tmp_path
):
    path = tmp_path / "refs.json"
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(path))
    source = "https://new-pharmacy.example.test/product/pack.png"
    assert upsert_image_reference(
        image_url="/assets/generic-pack.png",
        pharmacy_id=99,
        pharmacy="Future Pharmacy",
        sku="SKU-9",
        ean=None,
        product_id="p-9",
        product_url=source,
        catalog_id=9,
        offer_id=90,
    )
    assert not upsert_image_reference(
        image_url=None,
        pharmacy_id=99,
        pharmacy="Future Pharmacy",
        sku="SKU-9",
        ean=None,
        product_id="p-9",
        product_url=source,
        catalog_id=9,
        offer_id=90,
    )
    assert resolve_catalog_images(9, [SimpleNamespace(id=90)]) == [
        "https://new-pharmacy.example.test/assets/generic-pack.png"
    ]
    assert len(json.loads(path.read_text(encoding="utf-8"))["records"]) == 1


def test_registry_updates_same_offer_and_keeps_catalogs_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "refs.json"))
    common = dict(
        pharmacy_id=1,
        pharmacy="Indiana",
        sku="SKU-1",
        ean="1234567890123",
        product_id="remote-1",
        product_url="https://www.farmaciaindiana.com.br/item/p",
        offer_id=10,
    )
    upsert_image_reference(
        image_url="https://cdn.example/a.jpg", catalog_id=4, **common
    )
    upsert_image_reference(
        image_url="https://cdn.example/b.jpg", catalog_id=4, **common
    )
    other_offer = {**common, "sku": "SKU-2", "offer_id": 11}
    upsert_image_reference(
        image_url="https://cdn.example/c.jpg", catalog_id=5, **other_offer
    )
    assert resolve_catalog_images(4, [SimpleNamespace(id=10)]) == [
        "https://cdn.example/b.jpg"
    ]
    assert resolve_catalog_images(5, [SimpleNamespace(id=11)]) == [
        "https://cdn.example/c.jpg"
    ]
    assert resolve_catalog_images(4, [SimpleNamespace(id=11)]) == []
    assert len(json.loads((tmp_path / "refs.json").read_text())["records"]) == 2


def test_catalog_images_follow_the_filtered_pharmacy(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "refs.json"))
    for pharmacy_id, pharmacy, sku, image in (
        (1, "Indiana", "IND-1", "indiana.jpg"),
        (4, "Pacheco", "PCH-1", "pacheco.jpg"),
    ):
        upsert_image_reference(
            image_url=f"https://cdn.example/{image}",
            pharmacy_id=pharmacy_id,
            pharmacy=pharmacy,
            sku=sku,
            ean="7891721201806",
            product_id=sku,
            product_url=f"https://{pharmacy.casefold()}.example/item",
            catalog_id=15,
            offer_id=None,
        )

    indiana = SimpleNamespace(id=10, farmacia_id=1, sku_interno="IND-1")
    pacheco = SimpleNamespace(id=20, farmacia_id=4, sku_interno="PCH-1")
    assert resolve_catalog_images(15, [indiana]) == ["https://cdn.example/indiana.jpg"]
    assert resolve_catalog_images(15, [pacheco]) == ["https://cdn.example/pacheco.jpg"]

    candidates = resolve_catalog_image_candidates(
        15,
        [indiana, pacheco],
        catalog_ean="7891721201806",
        require_offer_match=True,
    )
    assert candidates == [
        {
            "url": "https://cdn.example/indiana.jpg",
            "pharmacy_id": 1,
            "generic": False,
        },
        {
            "url": "https://cdn.example/pacheco.jpg",
            "pharmacy_id": 4,
            "generic": False,
        },
    ]


def test_exact_catalog_identity_can_deliver_allowed_generic_image(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "refs.json"))
    upsert_image_reference(
        image_url=(
            "https://i3-imagens-prd.araujo.com.br/webp/"
            "MEDICAMENTOS-CAIXA-AZUL---OTC.jpg"
        ),
        pharmacy_id=2,
        pharmacy="Araujo",
        sku="ARJ-1",
        ean="7896026307925",
        product_id="ARJ-1",
        product_url=None,
        catalog_id=22,
        offer_id=None,
        identity_confirmed=True,
        allow_generic=True,
    )

    pacheco_offer = SimpleNamespace(id=20, farmacia_id=3, sku_interno="PCH-1")
    assert resolve_catalog_images(
        22,
        [pacheco_offer],
        catalog_ean="7896026307925",
        require_offer_match=True,
    ) == ["https://i3-imagens-prd.araujo.com.br/webp/MEDICAMENTOS-CAIXA-AZUL---OTC.jpg"]


def test_registry_resolver_rejects_generic_art_and_ean_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "refs.json"))
    common = dict(
        pharmacy_id=1,
        pharmacy="Indiana",
        product_id=None,
        product_url="https://www.farmaciaindiana.com.br/product/p",
        catalog_id=22,
    )
    upsert_image_reference(
        image_url="https://farmaciaindiana.vteximg.com.br/arquivos/MEDICAMENTOS-CAIXA-AZUL---OTC.jpg",
        sku="IND-1",
        ean="7896026307925",
        offer_id=101,
        **common,
    )
    upsert_image_reference(
        image_url="https://farmaciaindiana.vteximg.com.br/arquivos/7896026306751.jpg",
        sku="IND-2",
        ean="7896026307925",
        offer_id=102,
        **common,
    )
    upsert_image_reference(
        image_url="https://farmaciaindiana.vteximg.com.br/arquivos/7896026307925.jpg",
        sku="IND-3",
        ean="7896026307925",
        offer_id=103,
        **common,
    )
    upsert_image_reference(
        image_url="https://farmaciaindiana.vteximg.com.br/arquivos/7896000000000.jpg",
        sku="IND-4",
        ean="7896026307925",
        offer_id=104,
        **common,
    )

    offers = [
        SimpleNamespace(id=101),
        SimpleNamespace(id=102),
        SimpleNamespace(id=103),
        SimpleNamespace(id=104),
    ]
    assert resolve_catalog_images(22, offers, catalog_ean="7896026307925") == [
        "https://farmaciaindiana.vteximg.com.br/arquivos/7896026307925.jpg"
    ]


def test_known_pharmacy_placeholders_are_recognized_as_generic_art():
    urls = [
        "https://farmaciaindiana.vteximg.com.br/arquivos/7896026306751.jpg",
        "https://farmaciaindiana.vteximg.com.br/arquivos/MEDICAMENTOS-CAIXA-AZUL---OTC.jpg",
        "https://i3-imagens-prd.araujo.com.br/webp/7290/128666_7896026306751_1.webp",
        "https://drogariaspacheco.vteximg.com.br/arquivos/de-referencia-tarja-vermelha-em-comprimido-pacheco.jpg",
        "https://i3-imagens-prd.araujo.com.br/webp/105933/239898_7896382709135_1.webp",
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/2611762/887455-2.jpg?v=639281055232600000",
        "https://drogariaspacheco.vteximg.com.br/arquivos/ids/2611791/887528-DPA.png?v=639281095739670000",
    ]
    assert all(is_generic_image_url(url) for url in urls)
    assert not is_generic_image_url(
        "https://i3-imagens-prd.araujo.com.br/webp/105933/pack-7891234567895.webp"
    )


def test_url_normalization_is_https_only_and_accepts_relative_urls():
    assert normalize_image_url("/image.webp", "https://shop.example.test/item") == (
        "https://shop.example.test/image.webp"
    )
    assert normalize_image_url("http://shop.example.test/image.webp") is None
    assert normalize_image_url("https://127.0.0.1/image.jpg") is None


def test_bulk_registry_write_is_atomic_and_keeps_existing_references(
    monkeypatch, tmp_path
):
    path = tmp_path / "refs.json"
    monkeypatch.setenv("IMAGE_REFERENCE_STORE_PATH", str(path))
    upsert_image_reference(
        image_url="https://cdn.example/old.jpg",
        pharmacy_id=4,
        pharmacy="Pacheco",
        sku="PCH-1",
        ean="7891721201806",
        product_id="1",
        product_url="https://shop.example/item/1",
        catalog_id=15,
        offer_id=101,
    )

    writes = 0
    original_write = image_registry._write

    def count_write(data):
        nonlocal writes
        writes += 1
        original_write(data)

    monkeypatch.setattr(image_registry, "_write", count_write)
    saved = upsert_image_references(
        [
            {
                "image_url": "https://cdn.example/new.jpg",
                "pharmacy_id": 4,
                "pharmacy": "Pacheco",
                "sku": "PCH-1",
                "ean": "7891721201806",
                "product_id": "1",
                "product_url": "https://shop.example/item/1",
                "catalog_id": 15,
                "offer_id": 101,
            },
            {
                "image_url": "https://cdn.example/item-2.webp",
                "pharmacy_id": 4,
                "pharmacy": "Pacheco",
                "sku": "PCH-2",
                "ean": None,
                "product_id": "2",
                "product_url": "https://shop.example/item/2",
                "catalog_id": 16,
                "offer_id": 102,
            },
            {
                "image_url": None,
                "pharmacy_id": 4,
                "pharmacy": "Pacheco",
                "sku": "PCH-3",
                "ean": None,
                "product_id": "3",
                "product_url": "https://shop.example/item/3",
                "catalog_id": 17,
                "offer_id": 103,
            },
        ]
    )

    assert saved == [True, True, False]
    assert writes == 1
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    assert len(records) == 2
    assert records[0]["image_url"] == "https://cdn.example/new.jpg"
    assert records[1]["image_url"] == "https://cdn.example/item-2.webp"


def test_pacheco_scraper_returns_generic_image_and_keeps_price_when_missing():
    scraper = PachecoScraper()
    assert scraper.farmacia_cnpj == "33438250000167"
    assert scraper.max_items is None
    product = {
        "productId": "155349",
        "productName": "Glifage XR 500mg",
        "linkText": "glifage-xr-500mg",
        "items": [
            {
                "itemId": "155349",
                "ean": "7891721201806",
                "name": "Glifage XR 500mg 30 comprimidos",
                "images": [
                    {
                        "imageUrl": (
                            "https://drogariaspacheco.vteximg.com.br/"
                            "arquivos/generic-red-box.jpg"
                        )
                    }
                ],
                "sellers": [{"commertialOffer": {"Price": 12.5}}],
            }
        ],
    }

    extracted = scraper._extrair_produto(product)
    assert extracted is not None
    assert extracted["sku_interno"] == "PCH155349"
    assert extracted["imagem_url"].endswith("generic-red-box.jpg")

    product["items"][0]["images"] = []
    extracted_without_image = scraper._extrair_produto(product)
    assert extracted_without_image is not None
    assert extracted_without_image["preco"] == 12.5
    assert extracted_without_image["imagem_url"] is None


def test_pacheco_scraper_accepts_a_bounded_collection_limit():
    scraper = PachecoScraper(max_items=1)

    assert scraper.max_items == 1
    assert scraper.items_enqueued == 0

    try:
        PachecoScraper(max_items=0)
    except ValueError as error:
        assert "maior que zero" in str(error)
    else:
        raise AssertionError("max_items=0 deveria ser rejeitado")
