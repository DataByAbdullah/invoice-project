"""
FastAPI application factory.

Follows the application factory pattern — no global app object at module level.
This makes testing (create a fresh app per test) and configuration clean.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware, RequestLoggingMiddleware
from app.core.settings import get_settings
from app.db.database import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown logic."""
    configure_logging()
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("application_starting", env=get_settings().app_env)
    yield
    await dispose_engine()
    logger.info("application_shutdown")


def create_application() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI-powered invoice data extraction, expense categorization, "
            "duplicate detection, and anomaly analysis API."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware (order matters — outermost wraps innermost) ─────────────
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    # ── Exception handlers ─────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routes ─────────────────────────────────────────────────────────────
    from app.api.v1 import api_v1_router
    app.include_router(api_v1_router)

    return app


app = create_application()
