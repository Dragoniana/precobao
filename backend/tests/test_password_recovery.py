import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all models with Base.metadata
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.catalogo import CatalogoBase
from app.models.password_recovery import PasswordRecoveryCode
from app.models.usuario import Usuario, usuario_medicamento_favorito

OLD_PASSWORD = "Old-account-password-123"
NEW_PASSWORD = "New-account-password-456"


@pytest.fixture
def recovery_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    password_helper = PasswordHelper()
    with session_factory() as db:
        user = Usuario(
            email="recovery@example.com",
            hashed_password=password_helper.hash(OLD_PASSWORD),
            is_active=True,
            is_superuser=False,
            is_verified=True,
            nome="Recovery Test",
        )
        medicine = CatalogoBase(
            name_search="Dipirona teste",
            principio_ativo="Dipirona",
            laboratorio="Teste",
            categorias=[],
        )
        db.add_all([user, medicine])
        db.commit()
        user_id = user.id
        medicine_id = medicine.id
        db.execute(
            insert(usuario_medicamento_favorito).values(
                usuario_id=user_id, medicamento_id=medicine_id
            )
        )
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    application = create_app()
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client, session_factory, user_id, medicine_id
    application.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _login(client: TestClient, password: str) -> dict:
    response = client.post(
        "/auth/jwt/login",
        data={"username": "recovery@example.com", "password": password},
    )
    return response


def _issue_code(client: TestClient, password: str = OLD_PASSWORD) -> str:
    login = _login(client, OLD_PASSWORD)
    assert login.status_code == 200, login.text
    response = client.post(
        "/auth/password-recovery/codes",
        json={"current_password": password},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["recovery_code"]


def _reset(
    client: TestClient,
    *,
    code: str,
    email: str = "recovery@example.com",
    password=NEW_PASSWORD,
    confirmation=NEW_PASSWORD,
):
    return client.post(
        "/auth/password-recovery/reset",
        json={
            "email": email,
            "code": code,
            "new_password": password,
            "confirm_new_password": confirmation,
        },
    )


def test_issue_requires_login_and_current_password(recovery_context):
    client, session_factory, _, _ = recovery_context
    anonymous = client.post(
        "/auth/password-recovery/codes", json={"current_password": OLD_PASSWORD}
    )
    assert anonymous.status_code == 401

    login = _login(client, OLD_PASSWORD)
    response = client.post(
        "/auth/password-recovery/codes",
        json={"current_password": "incorrect-current-password"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 400
    with session_factory() as db:
        assert db.execute(select(PasswordRecoveryCode)).scalars().all() == []


def test_code_is_shown_once_and_only_a_hash_is_stored(recovery_context):
    client, session_factory, user_id, _ = recovery_context
    login = _login(client, OLD_PASSWORD)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = client.post(
        "/auth/password-recovery/codes",
        json={"current_password": OLD_PASSWORD},
        headers=headers,
    )
    assert response.status_code == 201
    code = response.json()["recovery_code"]
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.get("/auth/password-recovery/codes", headers=headers).status_code == 405
    )
    with session_factory() as db:
        stored = db.execute(
            select(PasswordRecoveryCode).where(PasswordRecoveryCode.user_id == user_id)
        ).scalar_one()
        assert stored.code_hash == hashlib.sha256(code.encode()).hexdigest()
        assert code not in stored.code_hash
        assert stored.used_at is None


def test_bad_code_and_unknown_email_have_same_public_response(recovery_context):
    client, _, _, _ = recovery_context
    bad_code = _reset(client, code="x" * 43)
    missing_account = _reset(client, code="x" * 43, email="not-registered@example.org")
    assert bad_code.status_code == missing_account.status_code == 400
    assert bad_code.json() == missing_account.json()


def test_password_confirmation_must_match(recovery_context):
    client, _, _, _ = recovery_context
    code = _issue_code(client)
    response = _reset(client, code=code, confirmation="Different-password-789")
    assert response.status_code == 422
    assert "As senhas não coincidem" in response.text


def test_code_is_bound_to_its_account(recovery_context):
    client, session_factory, _, _ = recovery_context
    code = _issue_code(client)
    other_user_id = uuid.uuid4()
    with session_factory() as db:
        other = Usuario(
            id=other_user_id,
            email="other@example.org",
            hashed_password=PasswordHelper().hash(OLD_PASSWORD),
            is_active=True,
            is_superuser=False,
            is_verified=True,
            nome="Other Test",
        )
        db.add(other)
        db.commit()
    response = _reset(client, code=code, email="other@example.org")
    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Código inválido ou expirado. Confira os dados e tente novamente."
    )


def test_reissuing_a_code_revokes_the_previous_one(recovery_context):
    client, session_factory, user_id, _ = recovery_context
    first_code = _issue_code(client)
    second_code = _issue_code(client)
    assert first_code != second_code

    with session_factory() as db:
        codes = (
            db.execute(
                select(PasswordRecoveryCode)
                .where(PasswordRecoveryCode.user_id == user_id)
                .order_by(PasswordRecoveryCode.id)
            )
            .scalars()
            .all()
        )
        assert len(codes) == 2
        assert codes[0].revoked_at is not None
        assert codes[1].revoked_at is None

    assert _reset(client, code=first_code).status_code == 400
    assert _reset(client, code=second_code).status_code == 200


def test_success_changes_password_once_and_preserves_favorites(recovery_context):
    client, session_factory, user_id, medicine_id = recovery_context
    code = _issue_code(client)

    changed = _reset(client, code=code)
    assert changed.status_code == 200, changed.text
    assert "Senha alterada" in changed.json()["message"]
    assert _reset(client, code=code).status_code == 400
    assert _login(client, OLD_PASSWORD).status_code == 400
    new_login = _login(client, NEW_PASSWORD)
    assert new_login.status_code == 200, new_login.text
    favorites = client.get(
        "/users/me/favoritos/medicamentos",
        headers={"Authorization": f"Bearer {new_login.json()['access_token']}"},
    )
    assert favorites.status_code == 200
    assert [item["id"] for item in favorites.json()] == [medicine_id]

    with session_factory() as db:
        stored = db.execute(
            select(PasswordRecoveryCode).where(PasswordRecoveryCode.user_id == user_id)
        ).scalar_one()
        assert stored.used_at is not None
        assert stored.code_hash == hashlib.sha256(code.encode()).hexdigest()


def test_repeated_wrong_codes_are_rate_limited(recovery_context):
    client, _, _, _ = recovery_context
    body = {
        "email": "recovery@example.com",
        "code": "z" * 43,
        "new_password": NEW_PASSWORD,
        "confirm_new_password": NEW_PASSWORD,
    }
    responses = [
        client.post("/auth/password-recovery/reset", json=body) for _ in range(6)
    ]
    assert [response.status_code for response in responses[:5]] == [400] * 5
    assert responses[5].status_code == 429
    assert int(responses[5].headers["Retry-After"]) > 0
