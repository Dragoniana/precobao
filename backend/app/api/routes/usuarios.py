import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
from app.schemas.password_recovery import (
    PasswordRecoveryCodeIssue,
    PasswordRecoveryReset,
)
from app.schemas.usuario import UsuarioCreate, UsuarioRead, UsuarioUpdate
from app.services.catalog_response import with_registry_images

router = APIRouter(tags=["Usuários e Autenticação"])
logger = logging.getLogger("preco_bao.password_recovery")
RECOVERY_CODE_LIFETIME = timedelta(days=365)
MAX_CODE_ATTEMPTS = 10
INVALID_RECOVERY_DETAIL = (
    "Código inválido ou expirado. Confira os dados e tente novamente."
)

router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt")

router.include_router(
    fastapi_users.get_register_router(UsuarioRead, UsuarioCreate), prefix="/auth"
)

router.include_router(
    fastapi_users.get_users_router(UsuarioRead, UsuarioUpdate), prefix="/users"
)


@router.post("/auth/password-recovery/codes", status_code=status.HTTP_201_CREATED)
async def issue_password_recovery_code(
    payload: PasswordRecoveryCodeIssue,
    response: Response,
    user: Usuario = Depends(current_active_user),
    manager: UserManager = Depends(get_user_manager),
    db: Session = Depends(get_db),
):
    password = payload.current_password.get_secret_value()
    verified, upgraded_hash = manager.password_helper.verify_and_update(
        password, user.hashed_password
    )
    if not verified:
        logger.info("password_recovery_code_issue outcome=rejected")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não foi possível confirmar sua identidade.",
        )

    now = datetime.now(UTC)
    db.execute(
        update(PasswordRecoveryCode)
        .where(
            PasswordRecoveryCode.user_id == user.id,
            PasswordRecoveryCode.used_at.is_(None),
            PasswordRecoveryCode.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    if upgraded_hash:
        user.hashed_password = upgraded_hash
    recovery_code = secrets.token_urlsafe(32)
    db.add(
        PasswordRecoveryCode(
            user_id=user.id,
            code_hash=hashlib.sha256(recovery_code.encode("utf-8")).hexdigest(),
            expires_at=now + RECOVERY_CODE_LIFETIME,
        )
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    logger.info("password_recovery_code_issue outcome=issued")
    return {
        "recovery_code": recovery_code,
        "expires_at": (now + RECOVERY_CODE_LIFETIME).isoformat(),
        "message": "Guarde este código agora. Ele será exibido somente uma vez.",
    }


@router.post("/auth/password-recovery/reset")
async def reset_password_with_recovery_code(
    payload: PasswordRecoveryReset,
    manager: UserManager = Depends(get_user_manager),
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().casefold()
    user = db.execute(
        select(Usuario).where(func.lower(Usuario.email) == email)
    ).scalar_one_or_none()

    # Always do the code lookup and constant-time digest comparison, including
    # for unknown accounts, and return exactly the same public error.
    lookup_user_id = user.id if user is not None else uuid.UUID(int=0)
    stored_code = db.execute(
        select(PasswordRecoveryCode)
        .where(
            PasswordRecoveryCode.user_id == lookup_user_id,
            PasswordRecoveryCode.used_at.is_(None),
            PasswordRecoveryCode.revoked_at.is_(None),
        )
        .order_by(PasswordRecoveryCode.created_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    candidate = hashlib.sha256(
        payload.code.get_secret_value().strip().encode("utf-8")
    ).hexdigest()
    stored_digest = stored_code.code_hash if stored_code else "0" * 64
    digest_matches = hmac.compare_digest(stored_digest, candidate)
    now = datetime.now(UTC)
    expiry = stored_code.expires_at if stored_code else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)

    valid = bool(
        user is not None
        and user.is_active
        and stored_code is not None
        and digest_matches
        and expiry is not None
        and expiry > now
        and stored_code.attempt_count < MAX_CODE_ATTEMPTS
    )
    if not valid:
        if stored_code is not None:
            stored_code.attempt_count += 1
            if stored_code.attempt_count >= MAX_CODE_ATTEMPTS:
                stored_code.revoked_at = now
        db.commit()
        logger.info("password_recovery_reset outcome=rejected")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_RECOVERY_DETAIL,
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
    logger.info("password_recovery_reset outcome=completed")
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
                ).selectinload(OfertaFarmacia.farmacia)
            )
            .order_by(CatalogoBase.id)
        )
        .scalars()
        .all()
    )
    return [with_registry_images(product) for product in products]


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
