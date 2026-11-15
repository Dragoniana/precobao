"""Catalog price ranking keeps offers unique and missing prices last."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia


def test_price_ordering_aggregates_once_and_preserves_catalog_semantics(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "IMAGE_REFERENCE_STORE_PATH", str(tmp_path / "image-references.json")
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions() as db:
        indiana = Farmacia(
            cnpj="00000000000001",
            razao_social="Indiana Ltda",
            nome_fantasia="Indiana",
            endereco_completo="Rua A",
        )
        pacheco = Farmacia(
            cnpj="00000000000002",
            razao_social="Pacheco Ltda",
            nome_fantasia="Pacheco",
            endereco_completo="Rua B",
        )
        products = [
            CatalogoBase(
                id=101,
                name_search="Preço baixo",
                principio_ativo="Substância A",
                laboratorio="Lab A",
            ),
            CatalogoBase(
                id=102,
                name_search="Empate A",
                principio_ativo="Substância B",
                laboratorio="Lab B",
            ),
            CatalogoBase(
                id=103,
                name_search="Empate B",
                principio_ativo="Substância C",
                laboratorio="Lab C",
            ),
            CatalogoBase(
                id=104,
                name_search="Sem oferta",
                principio_ativo="Substância D",
                laboratorio="Lab D",
            ),
            CatalogoBase(
                id=105,
                name_search="Outra disponível",
                principio_ativo="Substância E",
                laboratorio="Lab E",
            ),
        ]
        db.add_all([indiana, pacheco, *products])
        db.flush()

        def offer(product_id, pharmacy, sku, price, available=True):
            return OfertaFarmacia(
                catalogo_id=product_id,
                farmacia_id=pharmacy.id,
                sku_interno=sku,
                preco=price,
                quantidade_estoque=1,
                disponivel=available,
                url_origem=f"https://example.test/{sku}",
            )

        db.add_all(
            [
                offer(101, indiana, "IND-101", 4.00),
                offer(101, pacheco, "PCH-101", 9.80),
                offer(101, pacheco, "PCH-101-OUT", 1.00, available=False),
                offer(102, pacheco, "PCH-102", 10.00),
                offer(103, pacheco, "PCH-103", 10.00),
                offer(105, indiana, "IND-105", 7.00),
            ]
        )
        pacheco_id = pacheco.id
        db.commit()

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            cheapest = client.get("/catalogo/mais-baratos?limit=6").json()
            assert [item["id"] for item in cheapest] == [101, 105, 102, 103]
            assert [len(item["ofertas"]) for item in cheapest] == [2, 1, 1, 1]

            lower = client.get("/catalogo?limit=10&ordenar=menor_preco").json()
            assert [item["id"] for item in lower["items"]] == [
                101,
                105,
                102,
                103,
                104,
            ]
            assert lower["total"] == 5

            higher = client.get("/catalogo?limit=10&ordenar=maior_preco").json()
            assert [item["id"] for item in higher["items"]] == [
                102,
                103,
                105,
                101,
                104,
            ]

            pacheco_prices = client.get(
                f"/catalogo?limit=10&farmacia_id={pacheco_id}&ordenar=menor_preco"
            ).json()
            assert [item["id"] for item in pacheco_prices["items"]] == [
                101,
                102,
                103,
            ]
            assert pacheco_prices["total"] == 3

            searched = client.get(
                "/catalogo?limit=1&offset=1&termo_busca=Empate&ordenar=menor_preco"
            ).json()
            assert searched["total"] == 2
            assert [item["id"] for item in searched["items"]] == [103]

            names = client.get("/catalogo?limit=5&ordenar=nome").json()
            assert [item["name_search"] for item in names["items"]] == [
                "Empate A",
                "Empate B",
                "Outra disponível",
                "Preço baixo",
                "Sem oferta",
            ]
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()
