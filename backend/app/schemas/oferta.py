"""
Módulo responsável pela definição dos contratos de dados (Schemas) de Ofertas Locais.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.services.imagens import (
    image_delivery_rejection_reason,
    image_rejection_reason,
    offer_image_delivery_rejection_reason,
)


class ProdutoExtraidoSchema(BaseModel):
    """
    Schema estrito para validação dos dados brutos extraídos pelos scrapers
    antes de serem processados para inserção no banco de dados.
    """

    id: str | int = Field(..., description="ID interno do produto no scraper")
    sku_interno: str = Field(
        ..., description="SKU local único da farmácia para a oferta"
    )
    ean: str | None = Field(
        default=None,
        max_length=14,
        pattern=r"^\d+$",
        description="Código de barras EAN estritamente numérico",
    )
    name_search: str = Field(
        ..., max_length=150, description="Nome do produto padronizado para busca"
    )

    @field_validator("ean", mode="before")
    @classmethod
    def limpar_ean(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().replace("-", "").replace(" ", "")
            if not v:
                return None
        return v

    preco: float = Field(
        gt=0.0,
        allow_inf_nan=False,
        description="Preço positivo confirmado na coleta",
    )
    disponivel: bool = Field(default=True)
    quantidade_estoque: int = Field(default=1, ge=0)
    link: str = Field(..., max_length=500, description="URL de origem do produto")
    imagem_url: str | None = Field(
        default=None, max_length=500, description="URL da imagem do produto"
    )
    fonte_observado_em: datetime | None = Field(
        default=None,
        description="Data/hora em que a fonte parceira observou preço e estoque",
    )


class OfertaBaseSchema(BaseModel):
    """
    Schema base contendo os atributos voláteis de precificação e estoque.
    """

    preco: Annotated[Decimal, Field(max_digits=10, decimal_places=2)]
    quantidade_estoque: int = Field(default=0, ge=0)
    disponivel: bool = Field(default=True)
    url_origem: str
    imagem_url: str | None = None
    contexto_chave: str = "global"
    canal_venda: str | None = None
    contexto_local: str | None = None
    cep: str | None = None
    cidade: str | None = None
    uf: str | None = None
    loja_id: str | None = None
    loja_nome: str | None = None
    vendedor: str | None = None
    confiabilidade: str | None = None
    fonte_observado_em: datetime | None = None


class OfertaOut(OfertaBaseSchema):
    """
    Schema de saída (Response) para a entidade de Oferta.
    Mapeia as chaves estrangeiras cruciais para a interface gráfica.
    """

    id: int
    farmacia_id: int
    farmacia_nome: str | None = None
    catalogo_id: int
    criado_em: datetime
    atualizado_em: datetime | None = None
    observado_em: datetime | None = None

    @computed_field(return_type=bool)
    @property
    def imagem_rejeitada(self) -> bool:
        return bool(
            self.imagem_url
            and offer_image_delivery_rejection_reason(
                self.imagem_url, self.url_origem, self.farmacia_nome
            )
        )

    @computed_field(return_type=bool)
    @property
    def imagem_generica(self) -> bool:
        return bool(
            self.imagem_url
            and not image_delivery_rejection_reason(self.imagem_url)
            and image_rejection_reason(self.imagem_url)
        )

    model_config = ConfigDict(from_attributes=True)
