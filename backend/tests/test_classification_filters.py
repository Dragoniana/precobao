"""Regression tests for source-backed catalog filters."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.catalogo import CatalogoBase


def test_filters_use_official_exact_classification_and_keep_unknowns_unknown():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all(
            [
                CatalogoBase(
                    name_search="Similar confirmado",
                    principio_ativo="Substância A",
                    laboratorio="Lab A",
                    categoria_regulatoria="Similar",
                    referencia_anvisa=True,
                    exige_receita_confirmada=True,
                    controle_especial=None,
                    categorias=["Generico"],  # Legacy inference must be ignored.
                ),
                CatalogoBase(
                    name_search="Genérico",
                    principio_ativo="Substância B",
                    laboratorio="Lab B",
                    categoria_regulatoria="Genérico",
                    referencia_anvisa=False,
                    exige_receita_confirmada=False,
                    categorias=["Similar"],
                ),
                CatalogoBase(
                    name_search="Sem registro confirmado",
                    principio_ativo="Substância C",
                    laboratorio="Lab C",
                    categorias=["Generico", "Indeterminado"],
                ),
            ]
        )
        db.commit()

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            similar = client.get("/catalogo?filtros=categoria:Similar")
            assert similar.status_code == 200
            assert [row["name_search"] for row in similar.json()["items"]] == [
                "Similar confirmado"
            ]
            legacy_mismatch = client.get("/catalogo?filtros=categoria:Genérico")
            assert [row["name_search"] for row in legacy_mismatch.json()["items"]] == [
                "Genérico"
            ]
            reference_and_similar = client.get(
                "/catalogo?filtros=categoria:Similar&filtros=referencia:sim"
            )
            assert reference_and_similar.json()["total"] == 1
            no_false_otc = client.get("/catalogo?filtros=receita:venda_livre")
            assert no_false_otc.json()["total"] == 0
            unknown = client.get("/catalogo?filtros=classificacao:desconhecida")
            assert [row["name_search"] for row in unknown.json()["items"]] == [
                "Sem registro confirmado"
            ]
            options = client.get("/catalogo/filtros/opcoes").json()
            assert options["categorias"] == ["Genérico", "Similar"]
            assert options["contagens"]["desconhecida"] == 1
            invalid = client.get("/catalogo?filtros=foo:bar")
            assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_sale_type_filters_match_nullable_rules_counts_and_keep_database_nulls():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    cases = [
        ("Receita não exigida, fora de controle", False, False),
        ("Receita exigida, fora de controle", True, False),
        ("Receita exigida, controlado", True, True),
        ("Sem receita, controlado", False, True),
        ("Classificação desconhecida", None, None),
        ("Receita exigida, controle desconhecido", True, None),
        ("Receita não exigida, controle desconhecido", False, None),
        ("Receita desconhecida, fora de controle", None, False),
    ]
    with sessions() as db:
        db.add_all(
            [
                CatalogoBase(
                    name_search=f"Regra {name}",
                    principio_ativo="Substância de teste",
                    laboratorio="Laboratório de teste",
                    categoria_regulatoria="Analgesico",
                    exige_receita_confirmada=requires_prescription,
                    controle_especial=special_control,
                )
                for name, requires_prescription, special_control in cases
            ]
        )
        db.commit()

    def override_db():
        with sessions() as db:
            yield db

    expected = {
        "venda_livre": {
            "Regra Receita não exigida, fora de controle",
            "Regra Receita exigida, fora de controle",
            "Regra Receita desconhecida, fora de controle",
        },
        "com_receita": {
            "Regra Receita exigida, fora de controle",
            "Regra Receita exigida, controlado",
            "Regra Classificação desconhecida",
            "Regra Receita exigida, controle desconhecido",
            "Regra Receita não exigida, controle desconhecido",
        },
        "controle_especial": {
            "Regra Receita exigida, controlado",
            "Regra Sem receita, controlado",
        },
    }
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            for mode, expected_names in expected.items():
                response = client.get(f"/catalogo?filtros=tipo_venda:{mode}")
                assert response.status_code == 200
                body = response.json()
                assert body["total"] == len(expected_names)
                assert {item["name_search"] for item in body["items"]} == expected_names

            options = client.get("/catalogo/filtros/opcoes").json()
            assert options["contagens"]["venda_livre"] == len(expected["venda_livre"])
            assert options["contagens"]["com_receita"] == len(expected["com_receita"])
            assert options["contagens"]["controle_especial"] == len(
                expected["controle_especial"]
            )

            combined = client.get(
                "/catalogo?filtros=tipo_venda:com_receita"
                "&filtros=categoria:Analgesico&termo_busca=Regra"
                "&ordenar=nome&limit=2&offset=1"
            ).json()
            assert combined["total"] == len(expected["com_receita"])
            assert len(combined["items"]) == 2
            assert combined["offset"] == 1

            contradictory = client.get(
                "/catalogo?filtros=tipo_venda:venda_livre"
                "&filtros=tipo_venda:controle_especial"
            )
            assert contradictory.status_code == 422
            legacy_sale_free = client.get("/catalogo?filtros=receita:venda_livre")
            assert legacy_sale_free.json()["total"] == 3

        with sessions() as db:
            unknown = db.query(CatalogoBase).filter_by(
                name_search="Regra Classificação desconhecida"
            ).one()
            assert unknown.exige_receita_confirmada is None
            assert unknown.controle_especial is None
            unknown_control = db.query(CatalogoBase).filter_by(
                name_search="Regra Receita exigida, controle desconhecido"
            ).one()
            assert unknown_control.controle_especial is None
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()
