"""
Módulo responsável pela definição do modelo de Oferta e Estoque Local.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .base import Base

if TYPE_CHECKING:
    from .catalogo import CatalogoBase
    from .farmacia import Farmacia
    from .medicine_image import MedicineImageLink


class OfertaFarmacia(Base):
    """
    Representa preço e disponibilidade local de um item do catálogo.

    :ivar id: Identificador único e chave primária da oferta.
    :vartype id: int
    :ivar preco: Valor de venda local do medicamento (precisão monetária).
    :vartype preco: Decimal
    :ivar quantidade_estoque: Quantidade de unidades disponíveis fisicamente.
    :vartype quantidade_estoque: int
    :ivar disponivel: Flag indicando se a oferta está ativa para o público.
    :vartype disponivel: bool
    :ivar url_origem: Link direto para a compra ou visualização no site da farmácia.
    :vartype url_origem: str
    :ivar imagem_url: Link da fotografia ilustrativa retirada do scrape.
    :vartype imagem_url: str | None
    :ivar criado_em: Timestamp de criação do registro da oferta.
    :vartype criado_em: datetime
    :ivar atualizado_em: Timestamp de alteração (ex: mudança de preço/estoque).
    :vartype atualizado_em: datetime | None
    :ivar farmacia_id: Chave estrangeira que vincula a oferta à farmácia.
    :vartype farmacia_id: int
    :ivar catalogo_id: Chave estrangeira que vincula a oferta ao catálogo global.
    :vartype catalogo_id: int
    :ivar farmacia: Relacionamento ORM com a entidade Farmacia.
    :vartype farmacia: Farmacia
    :ivar produto_catalogo: Relacionamento ORM com a entidade CatalogoBase.
    :vartype produto_catalogo: CatalogoBase
    """

    __tablename__ = "ofertas_farmacia"
    __table_args__ = (
        UniqueConstraint(
            "url_origem", "contexto_chave", name="uq_ofertas_farmacia_url_contexto"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    sku_interno: Mapped[str] = mapped_column(String(50), index=True)
    preco: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantidade_estoque: Mapped[int] = mapped_column(Integer, default=0)
    disponivel: Mapped[bool] = mapped_column(Boolean, default=True)
    url_origem: Mapped[str] = mapped_column(String(500), index=True)
    imagem_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contexto_chave: Mapped[str] = mapped_column(
        String(120), nullable=False, default="global", server_default="global"
    )
    canal_venda: Mapped[str | None] = mapped_column(String(30), nullable=True)
    contexto_local: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cep: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cidade: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    loja_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    loja_nome: Mapped[str | None] = mapped_column(String(150), nullable=True)
    vendedor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confiabilidade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fonte_observado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    atualizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
    observado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    farmacia_id: Mapped[int] = mapped_column(ForeignKey("farmacias.id"))
    catalogo_id: Mapped[int] = mapped_column(ForeignKey("catalogo_base.id"))

    farmacia: Mapped["Farmacia"] = relationship("Farmacia", back_populates="ofertas")
    produto_catalogo: Mapped["CatalogoBase"] = relationship(
        "CatalogoBase", back_populates="ofertas"
    )
    image_links: Mapped[list["MedicineImageLink"]] = relationship(
        "MedicineImageLink", back_populates="offer", cascade="all, delete-orphan"
    )

    @property
    def farmacia_nome(self) -> str | None:
        return self.farmacia.nome_fantasia if self.farmacia else None
