import json
import logging
import os
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.routes import (
    catalogo,
    historico,
    medicine_image_ingest,
    scraper_ingest,
    usuarios,
)
from app.core.database import get_db
from app.core.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger("preco_bao.security")


_RATE_LIMIT_POLICIES = {
    ("POST", "/api/v1/internal/scraper-batches"): (
        "scraper-ingest",
        "SCRAPER_INGEST_RATE_LIMIT_PER_HOUR",
        120,
        3600,
    ),
    ("POST", "/api/v1/internal/medicine-images"): (
        "scraper-image-ingest",
        "SCRAPER_IMAGE_INGEST_RATE_LIMIT_PER_HOUR",
        600,
        3600,
    ),
    ("POST", "/api/v1/internal/medicine-images/migration-check"): (
        "scraper-image-migration-check",
        "SCRAPER_IMAGE_MIGRATION_CHECK_RATE_LIMIT_PER_HOUR",
        600,
        3600,
    ),
    ("POST", "/auth/jwt/login"): ("login", "RATE_LIMIT_LOGIN_PER_MINUTE", 10, 60),
    ("POST", "/auth/password-reset/request"): (
        "password_reset_request",
        "RATE_LIMIT_PASSWORD_RESET_REQUESTS_PER_HOUR",
        3,
        3600,
    ),
    ("POST", "/auth/password-reset/confirm"): (
        "password_reset_confirm",
        "RATE_LIMIT_PASSWORD_RESET_CONFIRMS_PER_HOUR",
        5,
        3600,
    ),
    ("POST", "/auth/register"): (
        "register",
        "RATE_LIMIT_REGISTER_PER_HOUR",
        5,
        3600,
    ),
    ("GET", "/catalogo"): ("catalog", "RATE_LIMIT_CATALOG_PER_MINUTE", 120, 60),
    ("GET", "/catalogo/farmacias"): (
        "catalog",
        "RATE_LIMIT_CATALOG_PER_MINUTE",
        120,
        60,
    ),
    ("GET", "/catalogo/mais-baratos"): (
        "catalog",
        "RATE_LIMIT_CATALOG_PER_MINUTE",
        120,
        60,
    ),
    ("GET", "/catalogo/filtros/opcoes"): (
        "catalog",
        "RATE_LIMIT_CATALOG_PER_MINUTE",
        120,
        60,
    ),
}


def _rate_limit_value(environment_name: str, default: int) -> int:
    raw_value = os.environ.get(environment_name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{environment_name} must be a positive integer.") from error
    if value < 1:
        raise RuntimeError(f"{environment_name} must be a positive integer.")
    return value


def _rate_limit_policy(method: str, path: str):
    policy = _RATE_LIMIT_POLICIES.get((method, path))
    if policy is not None:
        return policy
    if method == "GET" and (
        path.startswith("/catalogo/") or path.startswith("/api/v1/medicines/")
    ):
        return ("catalog", "RATE_LIMIT_CATALOG_PER_MINUTE", 120, 60)
    return None


def _get_cors_origins() -> list[str]:
    """Retrieves the CORS origins from environment variables.

    Reads the `BACKEND_CORS_ORIGINS` environment variable, attempting to parse
    it as a JSON list of strings. If the variable is not set, or if parsing
    fails, an empty list is returned.

    Returns:
        list[str]: A list of allowed CORS origins.
    """
    origins_env = os.environ.get("BACKEND_CORS_ORIGINS")
    if origins_env:
        try:
            parsed = json.loads(origins_env)
            if isinstance(parsed, list):
                return [str(origin) for origin in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "development").casefold() == "production"


def _validate_production_configuration(origins: list[str]) -> None:
    if "*" in origins or not origins:
        raise RuntimeError(
            "Em produção, BACKEND_CORS_ORIGINS deve listar origens HTTPS exatas."
        )
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "Em produção, BACKEND_CORS_ORIGINS aceita apenas origens HTTPS exatas."
            )

    secret = os.environ.get("JWT_SECRET", "")
    if (
        len(secret) < 32
        or len(set(secret)) < 12
        or secret.casefold().startswith(
            ("replace-", "change-me", "test-", "dev-", "local-", "example-")
        )
    ):
        raise RuntimeError("Em produção, JWT_SECRET deve ser um segredo forte e único.")
    if os.environ.get("DEBUG", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("Em produção, DEBUG deve ser false.")


def create_app() -> FastAPI:
    production = _is_production()
    debug = os.environ.get("DEBUG", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    origins = _get_cors_origins()
    if production:
        _validate_production_configuration(origins)
    rate_limit_values = {
        environment_name: _rate_limit_value(environment_name, default_limit)
        for environment_name, default_limit in {
            (policy[1], policy[2]) for policy in _RATE_LIMIT_POLICIES.values()
        }
    }

    application = FastAPI(
        title="Preço Bão API",
        debug=debug,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    rate_limiter = FixedWindowRateLimiter()

    @application.middleware("http")
    async def limit_public_requests(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        policy = _rate_limit_policy(request.method, request.url.path)
        if policy is not None:
            policy_name, environment_name, default_limit, window_seconds = policy
            limit = rate_limit_values[environment_name]
            client_ip = request.client.host if request.client else "unknown"
            allowed, retry_after = rate_limiter.check(
                f"{policy_name}:{client_ip}", limit, window_seconds
            )
            if not allowed:
                logger.warning("rate_limit_exceeded policy=%s", policy_name)
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Too many requests. Please try again later."},
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    # Register CORS after the request limiter so CORS remains present on early
    # 429 responses as well as on responses generated by the routers.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Expo may choose any free port when the usual Metro port is occupied.
        allow_origin_regex=(
            None if production else r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
        ),
        allow_credentials=True,
        allow_methods=(
            ["*"]
            if not production
            else ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        ),
        allow_headers=["*"] if not production else ["Authorization", "Content-Type"],
    )

    application.include_router(catalogo.router)
    application.include_router(usuarios.router)
    application.include_router(historico.router, prefix="/api/v1")
    application.include_router(scraper_ingest.router, prefix="/api/v1")
    application.include_router(medicine_image_ingest.router, prefix="/api/v1")

    @application.get("/health/live", include_in_schema=False)
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready", include_in_schema=False)
    def health_ready(db: Session = Depends(get_db)) -> dict[str, str]:
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable",
            ) from error
        return {"status": "ready"}

    @application.get("/")
    def read_root() -> dict[str, str]:
        return {"Hello": "Aurora"}

    return application


app: FastAPI = create_app()
