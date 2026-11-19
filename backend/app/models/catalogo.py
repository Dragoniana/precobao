"""
Módulo responsável pela definição do modelo central de Catálogo de Medicamentos.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import (
    ARRAY,  # Importação específica para o Postgres
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base
from .historico import HistoricoPreco
from .oferta_farmacia import OfertaFarmacia

if TYPE_CHECKING:
    from .medicine_image import MedicineImageLink


class SQLiteArray(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = json.loads(value)
        return value


class CatalogoBase(Base):
    """
    Representa o catálogo unificado com dados farmacológicos invariáveis.
    """

    __tablename__ = "catalogo_base"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    codigo_barras: Mapped[str | None] = mapped_column(
        String(14), unique=True, index=True, nullable=True
    )
    name_search: Mapped[str] = mapped_column(String(150), index=True)

    principio_ativo: Mapped[str] = mapped_column(
        String(500), index=True, default="Não informado"
    )
    laboratorio: Mapped[str] = mapped_column(
        String(100), index=True, default="Não informado"
    )
    exige_receita: Mapped[bool] = mapped_column(Boolean, default=False)
    categoria_regulatoria: Mapped[str | None] = mapped_column(String(40), nullable=True)
    referencia_anvisa: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    exige_receita_confirmada: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    controle_especial: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    classificacao_fonte: Mapped[str | None] = mapped_column(String(80), nullable=True)

    categorias: Mapped[list[str]] = mapped_column(
        ARRAY(String).with_variant(SQLiteArray, "sqlite"), default=list
    )

    # Imagem de catálogo só é preenchida após validar fonte e identidade.
    imagem_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    imagem_fonte: Mapped[str | None] = mapped_column(String(40), nullable=True)
    imagem_sku_origem: Mapped[str | None] = mapped_column(String(80), nullable=True)
    imagem_ean: Mapped[str | None] = mapped_column(String(14), nullable=True)
    imagem_match: Mapped[str | None] = mapped_column(String(24), nullable=True)
    imagem_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    imagem_atualizada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    imagem_verificada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    imagem_ultima_verificacao_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    imagem_falhas_consecutivas: Mapped[int] = mapped_column(
        default=0, server_default="0"
    )
    imagem_ultima_falha: Mapped[str | None] = mapped_column(Text, nullable=True)
    imagem_motivo_rejeicao: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    ofertas: Mapped[list["OfertaFarmacia"]] = relationship(
        "OfertaFarmacia", back_populates="produto_catalogo"
    )
    image_links: Mapped[list["MedicineImageLink"]] = relationship(
        "MedicineImageLink", back_populates="catalog", cascade="all, delete-orphan"
    )
    historico_precos: Mapped[list["HistoricoPreco"]] = relationship(
        "HistoricoPreco", backref="medicamento", cascade="all, delete-orphan"
    )
