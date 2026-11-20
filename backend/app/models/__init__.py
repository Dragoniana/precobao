"""
Pacote de Modelos de Domínio (ORM) da Aplicação.

Este pacote concentra todas as entidades de banco de dados mapeadas
através do SQLAlchemy. A importação centralizada neste módulo garante
que os metadados de todas as tabelas sejam devidamente registrados na
classe base antes da execução de migrações ou criação do esquema de banco.

Módulos e Classes Exportadas:
    * :class:`~models.base.Base`: Base declarativa do SQLAlchemy.
    * :class:`~models.farmacia.Farmacia`: Dados das farmácias.
    * :class:`~models.catalogo.CatalogoBase`: Catálogo farmacológico unificado.
    * :class:`~models.oferta_farmacia.OfertaFarmacia`: Preço e disponibilidade local.
    * :class:`~models.historico.HistoricoPreco`: Histórico de preços por farmácia.
    * :class:`~models.anvisa.AnvisaMedicamento`: Lookup de medicamentos da ANVISA.

.. note::
    Para o correto funcionamento das migrações, certifique-se de importar
    o objeto ``Base`` diretamente deste módulo dentro do arquivo ``env.py``
    do Alembic (ex: ``from models import Base``).
"""

from .anvisa import AnvisaMedicamento
from .base import Base
from .catalogo import CatalogoBase
from .farmacia import Farmacia
from .historico import HistoricoPreco
from .identidade_catalogo_fonte import IdentidadeCatalogoFonte
from .oferta_farmacia import OfertaFarmacia
from .password_recovery import PasswordRecoveryCode
from .usuario import Usuario, usuario_farmacia_favorita, usuario_medicamento_favorito

__all__ = [
    "Base",
    "Farmacia",
    "CatalogoBase",
    "OfertaFarmacia",
    "PasswordRecoveryCode",
    "HistoricoPreco",
    "IdentidadeCatalogoFonte",
    "AnvisaMedicamento",
    "Usuario",
    "usuario_medicamento_favorito",
    "usuario_farmacia_favorita",
]
