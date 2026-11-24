import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi_users.exceptions import InvalidPasswordException
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.auth import (
    UserManager,
    auth_backend,
    current_active_user,
    fastapi_users,
    get_user_manager,
)
from app.core.database import get_db
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia
from app.models.password_recovery import PasswordRecoveryCode
from app.models.usuario import (
    Usuario,
    usuario_farmacia_favorita,
    usuario_medicamento_favorito,
)
from app.schemas.catalogo import CatalogoComOfertasOut
from app.schemas.farmacia import FarmaciaOut
from app.schemas.password_recovery import PasswordResetConfirm, PasswordResetRequest
from app.schemas.usuario import UsuarioCreate, UsuarioRead, UsuarioUpdate
from app.services.catalog_response import with_registry_images_for_products
from app.services.password_reset_email import deliver_password_reset_email

router = APIRouter(tags=["Usuários e Autenticação"])
logger = logging.getLogger("preco_bao.password_recovery")
PASSWORD_RESET_LIFETIME = timedelta(minutes=20)
MAX_RESET_ATTEMPTS = 10
INVALID_RESET_DETAIL = (
    "O link é inválido ou expirou. Solicite um novo link e tente novamente."
)

router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt")

router.include_router(
    fastapi_users.get_register_router(UsuarioRead, UsuarioCreate), prefix="/auth"
)

router.include_router(
    fastapi_users.get_users_router(UsuarioRead, UsuarioUpdate), prefix="/users"
)


@router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().casefold()
    now = datetime.now(UTC)
    user = db.execute(
        select(Usuario).where(func.lower(Usuario.email) == email).with_for_update()
    ).scalar_one_or_none()

    if user is not None and user.is_active:
        db.execute(
            update(PasswordRecoveryCode)
            .where(
                PasswordRecoveryCode.user_id == user.id,
                PasswordRecoveryCode.used_at.is_(None),
                PasswordRecoveryCode.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        token = secrets.token_urlsafe(32)
        expires_at = now + PASSWORD_RESET_LIFETIME
        db.add(
            PasswordRecoveryCode(
                user_id=user.id,
                code_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                expires_at=expires_at,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.info("password_reset_request outcome=not_queued")
        else:
            background_tasks.add_task(deliver_password_reset_email, user.email, token)

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    logger.info("password_reset_request outcome=accepted")
    return {"message": "Se o endereço estiver cadastrado, enviaremos um link."}


@router.post("/auth/password-reset/confirm")
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    response: Response,
    manager: UserManager = Depends(get_user_manager),
    db: Session = Depends(get_db),
):
    candidate = hashlib.sha256(
        payload.token.get_secret_value().strip().encode("utf-8")
    ).hexdigest()
    stored_code = db.execute(
        select(PasswordRecoveryCode)
        .where(PasswordRecoveryCode.code_hash == candidate)
        .with_for_update()
    ).scalar_one_or_none()
    stored_digest = stored_code.code_hash if stored_code else "0" * 64
    digest_matches = hmac.compare_digest(stored_digest, candidate)
    now = datetime.now(UTC)
    user = db.get(Usuario, stored_code.user_id) if stored_code is not None else None
    expiry = stored_code.expires_at if stored_code else None
    created_at = stored_code.created_at if stored_code else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    lifetime_is_email_token = bool(
        expiry is not None
        and created_at is not None
        # Allow small clock/precision differences between the app and DB while
        # distinguishing these from the legacy one-year manual recovery codes.
        and timedelta(0) < expiry - created_at <= timedelta(minutes=21)
    )

    valid = bool(
        user is not None
        and user.is_active
        and stored_code is not None
        and digest_matches
        and stored_code.used_at is None
        and stored_code.revoked_at is None
        and expiry is not None
        and expiry > now
        and lifetime_is_email_token
        and stored_code.attempt_count < MAX_RESET_ATTEMPTS
    )
    if not valid:
        if stored_code is not None:
            stored_code.attempt_count += 1
            if stored_code.attempt_count >= MAX_RESET_ATTEMPTS:
                stored_code.revoked_at = now
        db.commit()
        logger.info("password_reset_confirm outcome=rejected")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RESET_DETAIL,
        )

    assert user is not None and stored_code is not None
    new_password = payload.new_password.get_secret_value()
    try:
        await manager.validate_password(new_password, user)
    except InvalidPasswordException as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error.reason),
        ) from error

    user.hashed_password = manager.password_helper.hash(new_password)
    stored_code.used_at = now
    db.execute(
        update(PasswordRecoveryCode)
        .where(
            PasswordRecoveryCode.user_id == user.id,
            PasswordRecoveryCode.id != stored_code.id,
            PasswordRecoveryCode.used_at.is_(None),
            PasswordRecoveryCode.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    logger.info("password_reset_confirm outcome=completed")
    return {"message": "Senha alterada. Entre com sua nova senha."}


@router.post(
    "/users/me/favoritos/medicamentos/{medicamento_id}",
    status_code=status.HTTP_201_CREATED,
)
def add_medicamento_favorito(
    medicamento_id: int,
    user: Usuario = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    medicamento = db.get(CatalogoBase, medicamento_id)
    if not medicamento:
        raise HTTPException(status_code=404, detail="Medicamento não encontrado.")

    try:
        db.execute(
            insert(usuario_medicamento_favorito).values(
                usuario_id=user.id, medicamento_id=medicamento_id
            )
        )
        db.commit()
    except IntegrityError:
        # A chave composta protege contra requisições concorrentes.
        db.rollback()
        existing = db.execute(
            select(usuario_medicamento_favorito.c.medicamento_id).where(
                usuario_medicamento_favorito.c.usuario_id == user.id,
                usuario_medicamento_favorito.c.medicamento_id == medicamento_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=409, detail="Favorito não foi salvo.")
    return {"message": "Medicamento adicionado aos favoritos."}


@router.delete(
    "/users/me/favoritos/medicamentos/{medicamento_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_medicamento_favorito(
    medicamento_id: int,
    user: Usuario = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    db.execute(
        delete(usuario_medicamento_favorito).where(
            usuario_medicamento_favorito.c.usuario_id == user.id,
            usuario_medicamento_favorito.c.medicamento_id == medicamento_id,
        )
    )
    db.commit()


@router.get(
    "/users/me/favoritos/medicamentos", response_model=list[CatalogoComOfertasOut]
)
def list_medicamentos_favoritos(
    user: Usuario = Depends(current_active_user), db: Session = Depends(get_db)
):
    products = (
        db.execute(
            select(CatalogoBase)
            .join(
                usuario_medicamento_favorito,
                usuario_medicamento_favorito.c.medicamento_id == CatalogoBase.id,
            )
            .where(usuario_medicamento_favorito.c.usuario_id == user.id)
            .options(
                selectinload(
                    CatalogoBase.ofertas.and_(OfertaFarmacia.disponivel.is_(True))
                ).selectinload(OfertaFarmacia.farmacia),
                selectinload(CatalogoBase.image_links),
            )
            .order_by(CatalogoBase.id)
        )
        .scalars()
        .all()
    )
    return with_registry_images_for_products(products, db)


@router.post(
    "/users/me/favoritos/farmacias/{farmacia_id}", status_code=status.HTTP_201_CREATED
)
def add_farmacia_favorita(
    farmacia_id: int,
    user: Usuario = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    farmacia = db.get(Farmacia, farmacia_id)
    if not farmacia:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada.")

    try:
        db.execute(
            insert(usuario_farmacia_favorita).values(
                usuario_id=user.id, farmacia_id=farmacia_id
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(usuario_farmacia_favorita.c.farmacia_id).where(
                usuario_farmacia_favorita.c.usuario_id == user.id,
                usuario_farmacia_favorita.c.farmacia_id == farmacia_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=409, detail="Favorito não foi salvo.")
    return {"message": "Farmácia adicionada aos favoritos."}


@router.delete(
    "/users/me/favoritos/farmacias/{farmacia_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_farmacia_favorita(
    farmacia_id: int,
    user: Usuario = Depends(current_active_user),
    db: Session = Depends(get_db),
):
    db.execute(
        delete(usuario_farmacia_favorita).where(
            usuario_farmacia_favorita.c.usuario_id == user.id,
            usuario_farmacia_favorita.c.farmacia_id == farmacia_id,
        )
    )
    db.commit()


@router.get("/users/me/favoritos/farmacias", response_model=list[FarmaciaOut])
def list_farmacias_favoritas(
    user: Usuario = Depends(current_active_user), db: Session = Depends(get_db)
):
    return (
        db.execute(
            select(Farmacia)
            .join(
                usuario_farmacia_favorita,
                usuario_farmacia_favorita.c.farmacia_id == Farmacia.id,
            )
            .where(usuario_farmacia_favorita.c.usuario_id == user.id)
            .order_by(Farmacia.id)
        )
        .scalars()
        .all()
    )
