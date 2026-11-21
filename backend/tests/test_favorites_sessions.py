"""HTTP integration checks with a fresh database and new JWT sessions."""

import os

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


def test_favorites_survive_logout_login_and_stay_private():
    database_url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    engine_options = {}
    if database_url.startswith("sqlite"):
        engine_options = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    engine = create_engine(database_url, **engine_options)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        medicine = CatalogoBase(
            name_search="Dipirona",
            principio_ativo="Dipirona",
            laboratorio="Teste",
            categorias=[],
            exige_receita_confirmada=False,
            controle_especial=False,
        )
        pharmacy = Farmacia(
            cnpj="12345678901234",
            razao_social="Farmácia Teste",
            nome_fantasia="Teste",
            endereco_completo="Rua Teste",
        )
        db.add_all([medicine, pharmacy])
        db.commit()
        medicine_id, pharmacy_id = medicine.id, pharmacy.id
        db.add(
            OfertaFarmacia(
                catalogo_id=medicine_id,
                farmacia_id=pharmacy_id,
                sku_interno="dipirona-1",
                preco=12.50,
                quantidade_estoque=3,
                disponivel=True,
                url_origem="https://example.com/dipirona",
            )
        )
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            pharmacies = client.get("/catalogo/farmacias")
            assert pharmacies.status_code == 200
            assert pharmacies.json()[0]["id"] == pharmacy_id

            page = client.get("/catalogo?limit=1&offset=0&termo_busca=teste")
            assert page.status_code == 200, page.text
            assert page.json()["total"] == 1
            assert page.json()["items"][0]["id"] == medicine_id
            filtered = client.get("/catalogo?filtros=laboratorio:Teste")
            assert [item["id"] for item in filtered.json()["items"]] == [medicine_id]
            combined = client.get(
                "/catalogo?filtros=laboratorio:Teste&filtros=principio_ativo:Dipirona"
            )
            assert [item["id"] for item in combined.json()["items"]] == [medicine_id]
            controlled = client.get("/catalogo?filtros=categoria:controlados")
            assert controlled.json()["items"] == []
            prescription = client.get("/catalogo?filtros=receita:com_receita")
            assert prescription.json()["items"] == []
            over_the_counter = client.get("/catalogo?filtros=receita:venda_livre")
            assert [item["id"] for item in over_the_counter.json()["items"]] == [
                medicine_id
            ]
            cheapest = client.get("/catalogo/mais-baratos?limit=1")
            assert cheapest.status_code == 200, cheapest.text
            assert cheapest.json()[0]["ofertas"][0]["farmacia_nome"] == "Teste"
            detail = client.get(f"/catalogo/medicamentos/{medicine_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == medicine_id
            assert client.get("/catalogo/medicamentos/999999").status_code == 404

            def register_and_login(email):
                registered = client.post(
                    "/auth/register",
                    json={
                        "nome": "Pessoa Teste",
                        "email": email,
                        "password": "strong-password-123",
                    },
                )
                assert registered.status_code == 201, registered.text
                login = client.post(
                    "/auth/jwt/login",
                    data={"username": email, "password": "strong-password-123"},
                )
                assert login.status_code == 200, login.text
                return {"Authorization": f"Bearer {login.json()['access_token']}"}

            alice = register_and_login("alice@example.com")
            medicine_url = f"/users/me/favoritos/medicamentos/{medicine_id}"
            pharmacy_url = f"/users/me/favoritos/farmacias/{pharmacy_id}"
            assert client.post(medicine_url, headers=alice).status_code == 201
            assert client.post(medicine_url, headers=alice).status_code == 201
            assert client.post(pharmacy_url, headers=alice).status_code == 201
            assert client.post(pharmacy_url, headers=alice).status_code == 201
            assert client.post("/auth/jwt/logout", headers=alice).status_code == 204

            # A fresh login must read persisted associations, including identifiers.
            alice_again = client.post(
                "/auth/jwt/login",
                data={
                    "username": "alice@example.com",
                    "password": "strong-password-123",
                },
            )
            assert alice_again.status_code == 200
            alice = {"Authorization": f"Bearer {alice_again.json()['access_token']}"}
            saved = client.get("/users/me/favoritos/medicamentos", headers=alice)
            assert saved.status_code == 200, saved.text
            assert [item["id"] for item in saved.json()] == [medicine_id]
            assert saved.json()[0]["ofertas"][0]["preco"] == "12.50"
            assert [
                item["id"]
                for item in client.get(
                    "/users/me/favoritos/farmacias", headers=alice
                ).json()
            ] == [pharmacy_id]

            bob = register_and_login("bob@example.com")
            conflict = client.patch(
                "/users/me", headers=alice, json={"email": "bob@example.com"}
            )
            assert conflict.status_code == 400
            invalid = client.get(
                "/users/me", headers={"Authorization": "Bearer invalid"}
            )
            assert invalid.status_code == 401
            assert (
                client.get("/users/me/favoritos/medicamentos", headers=bob).json() == []
            )
            assert client.get("/users/me/favoritos/farmacias", headers=bob).json() == []
            assert client.delete(medicine_url, headers=bob).status_code == 204
            assert [
                item["id"]
                for item in client.get(
                    "/users/me/favoritos/medicamentos", headers=alice
                ).json()
            ] == [medicine_id]
            profile = client.patch(
                "/users/me",
                headers=alice,
                json={"nome": "Nome Persistido", "email": "alice.new@example.com"},
            )
            assert profile.status_code == 200, profile.text
            assert profile.json()["nome"] == "Nome Persistido"
            assert client.delete(medicine_url, headers=alice).status_code == 204
            assert client.delete(pharmacy_url, headers=alice).status_code == 204
            assert (
                client.get("/users/me/favoritos/medicamentos", headers=alice).json()
                == []
            )
            assert (
                client.get("/users/me/favoritos/farmacias", headers=alice).json() == []
            )
            again = client.post(
                "/auth/jwt/login",
                data={
                    "username": "alice.new@example.com",
                    "password": "strong-password-123",
                },
            )
            assert again.status_code == 200
            assert (
                client.get(
                    "/users/me",
                    headers={"Authorization": f"Bearer {again.json()['access_token']}"},
                ).json()["nome"]
                == "Nome Persistido"
            )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
