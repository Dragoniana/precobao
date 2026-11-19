"""Email reset flow against an isolated SQLite database and fake SMTP task."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from fastapi_users.password import PasswordHelper
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all models with Base.metadata
from app.api.routes import usuarios
from app.core.database import get_db
from app.main import create_app
from app.models.base import Base
from app.models.catalogo import CatalogoBase
from app.models.password_recovery import PasswordRecoveryCode
from app.models.usuario import Usuario, usuario_medicamento_favorito

OLD_PASSWORD = "Old-account-password-123"
NEW_PASSWORD = "New-account-password-456"
GENERIC_REQUEST_MESSAGE = {
    "message": "Se o endereço estiver cadastrado, enviaremos um link."
}
INVALID_RESET_MESSAGE = {
    "detail": "O link é inválido ou expirou. Solicite um novo link e tente novamente."
}


@pytest.fixture
def recovery_context(monkeypatch):
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

    sent_emails = []
    monkeypatch.setattr(
        usuarios,
        "deliver_password_reset_email",
        lambda email, token: sent_emails.append((email, token)),
    )

    def override_db():
        with session_factory() as db:
            yield db

    application = create_app()
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        yield client, session_factory, user_id, medicine_id, sent_emails
    application.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _request_link(client: TestClient, email: str = "recovery@example.com"):
    return client.post("/auth/password-reset/request", json={"email": email})


def _confirm(
    client: TestClient,
    token: str,
    *,
    password: str = NEW_PASSWORD,
    confirmation: str = NEW_PASSWORD,
):
    return client.post(
        "/auth/password-reset/confirm",
        json={
            "token": token,
            "new_password": password,
            "confirm_new_password": confirmation,
        },
    )


def test_request_is_generic_and_only_sends_for_registered_accounts(recovery_context):
    client, _, _, _, sent = recovery_context
    registered = _request_link(client)
    unknown = _request_link(client, "not-registered@example.org")

    assert registered.status_code == unknown.status_code == 202
    assert registered.json() == unknown.json() == GENERIC_REQUEST_MESSAGE
    assert registered.headers["cache-control"] == "no-store"
    assert len(sent) == 1
    assert sent[0][0] == "recovery@example.com"
    assert len(sent[0][1]) >= 32


def test_request_rejects_invalid_email_without_sending(recovery_context):
    client, _, _, _, sent = recovery_context
    response = _request_link(client, "invalid-email")
    assert response.status_code == 422
    assert sent == []


def test_token_hash_is_stored_and_expires_in_twenty_minutes(recovery_context):
    client, session_factory, user_id, _, sent = recovery_context
    response = _request_link(client)
    assert response.status_code == 202
    token = sent[0][1]
    with session_factory() as db:
        record = db.execute(
            select(PasswordRecoveryCode).where(PasswordRecoveryCode.user_id == user_id)
        ).scalar_one()
        assert record.code_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in record.code_hash
        remaining = record.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
        assert timedelta(minutes=19) < remaining <= timedelta(minutes=20)


def test_reset_changes_password_once_and_preserves_favorites(recovery_context):
    client, session_factory, user_id, medicine_id, sent = recovery_context
    _request_link(client)
    token = sent[0][1]

    changed = _confirm(client, token)
    assert changed.status_code == 200, changed.text
    assert changed.headers["cache-control"] == "no-store"
    assert _confirm(client, token).status_code == 400
    assert _confirm(client, "x" * 43).json() == INVALID_RESET_MESSAGE

    old_login = client.post(
        "/auth/jwt/login",
        data={"username": "recovery@example.com", "password": OLD_PASSWORD},
    )
    assert old_login.status_code == 400
    new_login = client.post(
        "/auth/jwt/login",
        data={"username": "recovery@example.com", "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200
    favorites = client.get(
        "/users/me/favoritos/medicamentos",
        headers={"Authorization": f"Bearer {new_login.json()['access_token']}"},
    )
    assert favorites.status_code == 200
    assert [item["id"] for item in favorites.json()] == [medicine_id]

    with session_factory() as db:
        record = db.execute(
            select(PasswordRecoveryCode).where(PasswordRecoveryCode.user_id == user_id)
        ).scalar_one()
        assert record.used_at is not None


def test_wrong_and_expired_tokens_are_rejected(recovery_context):
    client, session_factory, user_id, _, sent = recovery_context
    _request_link(client)
    assert (
        _confirm(client, "wrong-token-value-01234567890123456789").json()
        == INVALID_RESET_MESSAGE
    )

    with session_factory() as db:
        record = db.execute(
            select(PasswordRecoveryCode).where(PasswordRecoveryCode.user_id == user_id)
        ).scalar_one()
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert _confirm(client, sent[0][1]).json() == INVALID_RESET_MESSAGE


def test_old_long_lived_manual_codes_cannot_be_used_as_email_links(recovery_context):
    client, session_factory, user_id, _, _ = recovery_context
    legacy_code = "legacy-manual-code-012345678901234567890"
    with session_factory() as db:
        db.add(
            PasswordRecoveryCode(
                user_id=user_id,
                code_hash=hashlib.sha256(legacy_code.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(days=365),
            )
        )
        db.commit()

    response = _confirm(client, legacy_code)
    assert response.status_code == 400
    assert response.json() == INVALID_RESET_MESSAGE


def test_mismatched_passwords_are_rejected(recovery_context):
    client, _, _, _, sent = recovery_context
    _request_link(client)
    response = _confirm(client, sent[0][1], confirmation="Different-password-789")
    assert response.status_code == 422
    assert "As senhas não coincidem" in response.text


def test_rate_limit_and_legacy_code_routes(recovery_context):
    client, _, _, _, _ = recovery_context
    responses = [_request_link(client) for _ in range(4)]
    assert [response.status_code for response in responses[:3]] == [202, 202, 202]
    assert responses[3].status_code == 429
    assert client.post("/auth/password-recovery/codes", json={}).status_code == 404
    assert client.post("/auth/password-recovery/reset", json={}).status_code == 404
