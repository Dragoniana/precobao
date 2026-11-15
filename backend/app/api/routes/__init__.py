from .catalogo import router as catalogo_router
from .historico import router as historico_router
from .scraper_ingest import router as scraper_ingest_router
from .usuarios import router as usuarios_router

__all__ = [
    "usuarios_router",
    "catalogo_router",
    "historico_router",
    "scraper_ingest_router",
]
