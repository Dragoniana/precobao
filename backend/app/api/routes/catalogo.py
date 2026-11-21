from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload, undefer
from sqlalchemy.sql.elements import ColumnElement

from app import schemas
from app.core.database import get_db
from app.models import CatalogoBase, HistoricoPreco, OfertaFarmacia
from app.models.farmacia import Farmacia
from app.models.medicine_image import MedicineImageBlob, MedicineImageLink
from app.schemas import CatalogoFiltrosOut, CatalogoPageOut
from app.schemas.farmacia import FarmaciaOut
from app.services.catalog_response import with_registry_images

router = APIRouter(prefix="/catalogo", tags=["Catálogo de Produtos"])


def _available_offer_options(farmacia_id: int | None = None):
    criteria = [OfertaFarmacia.disponivel.is_(True)]
    if farmacia_id is not None:
        criteria.append(OfertaFarmacia.farmacia_id == farmacia_id)
    offers = CatalogoBase.ofertas.and_(*criteria)
    return selectinload(offers).selectinload(OfertaFarmacia.farmacia)


def _available_min_prices(farmacia_id: int | None = None):
    """Aggregate the available minimum once per medicine instead of per row."""
    query = select(
        OfertaFarmacia.catalogo_id.label("catalogo_id"),
        func.min(OfertaFarmacia.preco).label("menor_preco"),
    ).where(OfertaFarmacia.disponivel.is_(True))
    if farmacia_id is not None:
        query = query.where(OfertaFarmacia.farmacia_id == farmacia_id)
    return query.group_by(OfertaFarmacia.catalogo_id).subquery("preco_por_medicamento")


def _sale_type_condition(mode: str) -> ColumnElement[bool]:
    """Return the shared SQL predicate for one Tipo de venda filter."""
    if mode == "venda_livre":
        # This label means only that the medicine is not specially controlled.
        return CatalogoBase.controle_especial.is_(False)
    if mode == "com_receita":
        # Unknown special-control classification is included provisionally;
        # the stored NULL remains unchanged and is not a confirmed prescription rule.
        return or_(
            CatalogoBase.exige_receita_confirmada.is_(True),
            CatalogoBase.controle_especial.is_(None),
        )
    if mode == "controle_especial":
        return CatalogoBase.controle_especial.is_(True)
    raise ValueError(f"Tipo de venda inválido: {mode}")


def _sale_type_mode(kind: str, value: str) -> str | None:
    """Accept the current contract and legacy filter names during rollout."""
    if kind == "tipo_venda" and value in {
        "venda_livre",
        "com_receita",
        "controle_especial",
    }:
        return value
    if kind == "receita" and value in {"com_receita", "exige_receita"}:
        return "com_receita"
    if kind == "receita" and value == "venda_livre":
        return "venda_livre"
    if kind == "controle_especial" and value in {"sim", "true"}:
        return "controle_especial"
    return None


@router.get("/farmacias", response_model=list[FarmaciaOut])
def listar_farmacias(db: Session = Depends(get_db)):
    return (
        db.execute(select(Farmacia).order_by(Farmacia.nome_fantasia, Farmacia.id))
        .scalars()
        .all()
    )


@router.get("/mais-baratos", response_model=list[schemas.CatalogoComOfertasOut])
def listar_mais_baratos(
    limit: int = Query(6, ge=1, le=30),
    db: Session = Depends(get_db),
):
    precos = _available_min_prices()
    products = (
        db.execute(
            select(CatalogoBase)
            .join(precos, precos.c.catalogo_id == CatalogoBase.id)
            .options(
                _available_offer_options(),
                selectinload(CatalogoBase.image_links),
            )
            .order_by(precos.c.menor_preco.asc(), CatalogoBase.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [with_registry_images(product) for product in products]


@router.get(
    "/medicamentos/{medicamento_id}", response_model=schemas.CatalogoComOfertasOut
)
def obter_medicamento(medicamento_id: int, db: Session = Depends(get_db)):
    medicamento = db.execute(
        select(CatalogoBase)
        .where(CatalogoBase.id == medicamento_id)
        .options(
            _available_offer_options(),
            selectinload(CatalogoBase.image_links),
        )
    ).scalar_one_or_none()
    if medicamento is None:
        raise HTTPException(status_code=404, detail="Medicamento não encontrado.")
    return with_registry_images(medicamento)


@router.get("/medicamentos/{medicamento_id}/imagem", include_in_schema=False)
def servir_imagem_catalogo(
    medicamento_id: int, request: Request, db: Session = Depends(get_db)
):
    return _servir_imagem_catalogo(medicamento_id, db, request)


def redirecionar_imagem_catalogo(medicamento_id: int, db: Session):
    """Compatibility entry point for direct callers and legacy tests."""
    return _servir_imagem_catalogo(medicamento_id, db, None)


def _servir_imagem_catalogo(medicamento_id: int, db: Session, request: Request | None):
    medicamento = db.execute(
        select(CatalogoBase)
        .where(CatalogoBase.id == medicamento_id)
        .options(
            selectinload(CatalogoBase.ofertas).selectinload(OfertaFarmacia.farmacia)
        )
    ).scalar_one_or_none()
    if medicamento is None:
        raise HTTPException(status_code=404, detail="Imagem nao encontrada.")

    stored = db.execute(
        select(MedicineImageBlob)
        .join(MedicineImageLink, MedicineImageLink.blob_id == MedicineImageBlob.id)
        .where(
            MedicineImageLink.catalog_id == medicamento_id,
            MedicineImageLink.is_primary.is_(True),
        )
        .order_by(MedicineImageLink.updated_at.desc(), MedicineImageLink.id.desc())
        .options(undefer(MedicineImageBlob.image_data))
        .limit(1)
    ).scalar_one_or_none()
    if stored is not None:
        etag = f'"{stored.sha256}"'
        cache_headers = {
            "ETag": etag,
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
            "X-Content-Type-Options": "nosniff",
        }
        if_none_match = request.headers.get("if-none-match", "") if request else ""
        if any(
            candidate.strip() == "*" or candidate.strip().removeprefix("W/") == etag
            for candidate in if_none_match.split(",")
        ):
            return Response(status_code=304, headers=cache_headers)
        return Response(
            content=stored.image_data,
            media_type=stored.content_type,
            headers=cache_headers,
        )

    image_url = with_registry_images(medicamento).imagem_url
    if not image_url:
        raise HTTPException(status_code=404, detail="Imagem nao encontrada.")
    return RedirectResponse(
        image_url,
        status_code=302,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get(path="", response_model=CatalogoPageOut)
def listar_catalogo_completo(
    limit: int = Query(20, ge=1, le=100, description="Quantidade de itens por página"),
    offset: int = Query(0, ge=0, description="Número de itens a pular"),
    exige_receita: bool | None = Query(
        None, description="Filtra por necessidade de receita"
    ),
    categoria: str | None = Query(
        None, description="Filtra por categoria do medicamento"
    ),
    farmacia_id: int | None = Query(
        None,
        description="Filtra medicamentos com ofertas ativas na farmácia especificada",
    ),
    termo_busca: str | None = Query(
        None, description="Busca por nome ou princípio ativo"
    ),
    filtros: list[str] = Query(default=[]),
    ordenar: Literal["relevancia", "menor_preco", "maior_preco", "nome"] = Query(
        "relevancia", description="Ordenação dos resultados do catálogo"
    ),
    db: Session = Depends(get_db),
):
    """
    Retorna o catálogo de medicamentos paginado e filtrado,
    incluindo as ofertas locais ativas em cada farmácia.
    """
    stmt = select(CatalogoBase)

    if exige_receita is not None:
        stmt = stmt.where(CatalogoBase.exige_receita_confirmada.is_(exige_receita))

    if categoria:
        stmt = stmt.where(
            func.lower(CatalogoBase.categoria_regulatoria) == categoria.strip().lower()
        )

    if farmacia_id is not None:
        stmt = stmt.where(
            CatalogoBase.ofertas.any(
                (OfertaFarmacia.farmacia_id == farmacia_id)
                & OfertaFarmacia.disponivel.is_(True)
            )
        )

    if termo_busca:
        search_text = termo_busca.replace("%", "\\%").replace("_", "\\_")
        search_pattern = f"%{search_text}%"
        stmt = stmt.where(
            or_(
                CatalogoBase.name_search.ilike(search_pattern, escape="\\"),
                CatalogoBase.principio_ativo.ilike(search_pattern, escape="\\"),
                CatalogoBase.laboratorio.ilike(search_pattern, escape="\\"),
            )
        )

    option_conditions: dict[str, list[ColumnElement[bool]]] = {}
    sale_type_modes: set[str] = set()
    for option in filtros:
        kind, separator, value = option.partition(":")
        if not separator or not value.strip():
            raise HTTPException(status_code=422, detail=f"Filtro inválido: {option}")
        normalized = value.strip().lower()
        condition = None
        sale_type_mode = _sale_type_mode(kind, normalized)
        if sale_type_mode is not None:
            sale_type_modes.add(sale_type_mode)
            continue
        if kind == "categoria":
            condition = func.lower(CatalogoBase.categoria_regulatoria) == normalized
        elif kind == "referencia" and normalized in {"sim", "true", "referencia"}:
            condition = CatalogoBase.referencia_anvisa.is_(True)
        elif kind == "laboratorio":
            condition = func.lower(CatalogoBase.laboratorio) == normalized
        elif kind == "principio_ativo":
            condition = func.lower(CatalogoBase.principio_ativo) == normalized
        elif kind == "classificacao" and normalized in {"desconhecida", "unknown"}:
            condition = CatalogoBase.categoria_regulatoria.is_(None)
        if condition is None:
            raise HTTPException(
                status_code=422, detail=f"Filtro desconhecido: {option}"
            )
        option_conditions.setdefault(kind, []).append(condition)
    if len(sale_type_modes) > 1:
        raise HTTPException(
            status_code=422,
            detail="Selecione apenas um filtro de tipo de venda por vez.",
        )
    if sale_type_modes:
        stmt = stmt.where(_sale_type_condition(next(iter(sale_type_modes))))
    if option_conditions:
        stmt = stmt.where(
            *(or_(*conditions) for conditions in option_conditions.values())
        )

    # Conta o total antes de paginar
    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(total_stmt).scalar() or 0

    if ordenar == "menor_preco":
        precos = _available_min_prices(farmacia_id)
        stmt = stmt.outerjoin(precos, precos.c.catalogo_id == CatalogoBase.id)
        order_by = (precos.c.menor_preco.asc().nullslast(), CatalogoBase.id.asc())
    elif ordenar == "maior_preco":
        precos = _available_min_prices(farmacia_id)
        stmt = stmt.outerjoin(precos, precos.c.catalogo_id == CatalogoBase.id)
        order_by = (precos.c.menor_preco.desc().nullslast(), CatalogoBase.id.asc())
    elif ordenar == "nome":
        order_by = (func.lower(CatalogoBase.name_search).asc(), CatalogoBase.id.asc())
    else:
        order_by = (CatalogoBase.id.asc(),)

    # Adiciona paginação e Eager Loading das ofertas
    stmt = (
        stmt.options(
            _available_offer_options(farmacia_id),
            selectinload(CatalogoBase.image_links),
        )
        .order_by(*order_by)
        .limit(limit)
        .offset(offset)
    )

    resultados = db.execute(stmt).scalars().all()

    return CatalogoPageOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[with_registry_images(product) for product in resultados],
    )


@router.get(path="/filtros/opcoes", response_model=CatalogoFiltrosOut)
def obter_opcoes_filtros(db: Session = Depends(get_db)):
    """
    Retorna as opções únicas disponíveis no banco para categorias, laboratórios
    e princípios ativos, ignorando valores nulos, em branco ou "Não informado".
    """
    categorias = sorted(
        {
            value
            for value in db.execute(
                select(CatalogoBase.categoria_regulatoria)
                .where(CatalogoBase.categoria_regulatoria.is_not(None))
                .distinct()
            )
            .scalars()
            .all()
            if value and value.strip()
        },
        key=str.casefold,
    )

    # Laboratórios
    stmt_labs = select(CatalogoBase.laboratorio).distinct()
    laboratorios = [
        lab
        for lab in db.execute(stmt_labs).scalars().all()
        if lab and lab.strip() and lab != "Não informado"
    ]

    # Princípios ativos
    stmt_principios = select(CatalogoBase.principio_ativo).distinct()
    principios = [
        p
        for p in db.execute(stmt_principios).scalars().all()
        if p and p.strip() and p != "Não informado"
    ]

    return CatalogoFiltrosOut(
        categorias=categorias,
        laboratorios=sorted(laboratorios, key=str.casefold),
        principios_ativos=sorted(principios, key=str.casefold),
        contagens={
            "referencia": db.query(CatalogoBase)
            .filter(CatalogoBase.referencia_anvisa.is_(True))
            .count(),
            "com_receita": db.query(CatalogoBase)
            .filter(_sale_type_condition("com_receita"))
            .count(),
            "venda_livre": db.query(CatalogoBase)
            .filter(_sale_type_condition("venda_livre"))
            .count(),
            "controle_especial": db.query(CatalogoBase)
            .filter(_sale_type_condition("controle_especial"))
            .count(),
            "desconhecida": db.query(CatalogoBase)
            .filter(CatalogoBase.categoria_regulatoria.is_(None))
            .count(),
        },
    )


@router.get(
    path="/medicamentos/{id}/historico", response_model=list[schemas.HistoricoOut]
)
def ler_historico(id: int, db: Session = Depends(get_db)):
    # Retorna os preços ordenados por data para o gráfico
    return (
        db.query(HistoricoPreco)
        .filter(HistoricoPreco.medicamento_id == id)
        .order_by(HistoricoPreco.data_registro.asc())
        .all()
    )
