from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.auth import auth_backend, current_active_user, fastapi_users
from app.core.database import get_db
from app.models.catalogo import CatalogoBase
from app.models.farmacia import Farmacia
from app.models.oferta_farmacia import OfertaFarmacia
from app.models.usuario import (
    Usuario,
    usuario_farmacia_favorita,
    usuario_medicamento_favorito,
)
from app.schemas.catalogo import CatalogoComOfertasOut
from app.schemas.farmacia import FarmaciaOut
from app.schemas.usuario import UsuarioCreate, UsuarioRead, UsuarioUpdate
from app.services.catalog_response import with_registry_images

router = APIRouter(tags=["Usuários e Autenticação"])

router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt")

router.include_router(
    fastapi_users.get_register_router(UsuarioRead, UsuarioCreate), prefix="/auth"
)

router.include_router(
    fastapi_users.get_users_router(UsuarioRead, UsuarioUpdate), prefix="/users"
)


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
