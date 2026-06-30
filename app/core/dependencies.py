"""
FastAPI dependency injection container.

Every dependency is defined here as a function that FastAPI resolves
per-request (or cached as a singleton where appropriate).

The Next.js developer does not need to understand this file — it's
internal wiring. From their perspective, every API endpoint just works.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db_session
from app.infrastructure.ai import AIExtractionClient
from app.infrastructure.ocr import OCRProviderFactory, AbstractOCRProvider
from app.infrastructure.persistence.repositories import (
    AbstractInvoiceRepository,
    SQLInvoiceRepository,
)
from app.services.invoice_service import InvoiceService


# ── Infrastructure singletons (created once, reused across requests) ──────

@lru_cache(maxsize=1)
def get_ai_client() -> AIExtractionClient:
    """Singleton AI client — expensive to construct (validates API key)."""
    return AIExtractionClient()


@lru_cache(maxsize=1)
def get_ocr_provider() -> AbstractOCRProvider:
    return OCRProviderFactory.get_provider()


# ── Per-request dependencies ──────────────────────────────────────────────

async def get_invoice_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AbstractInvoiceRepository:
    return SQLInvoiceRepository(session)


async def get_invoice_service(
    repository: Annotated[AbstractInvoiceRepository, Depends(get_invoice_repository)],
    ocr_provider: Annotated[AbstractOCRProvider, Depends(get_ocr_provider)],
    ai_client: Annotated[AIExtractionClient, Depends(get_ai_client)],
) -> InvoiceService:
    return InvoiceService(
        repository=repository,
        ocr_provider=ocr_provider,
        ai_client=ai_client,
    )


# ── Auth ──────────────────────────────────────────────────────────────────
# 
# The Next.js app handles auth (NextAuth / Clerk / Supabase Auth).
# It forwards the user's ID in a custom header after validating their JWT.
# In production, validate the JWT signature here instead of trusting the header.

async def get_current_user_id(
    x_user_id: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """
    Extract and validate user identity from request header.

    PRODUCTION NOTE: Replace this with JWT signature verification.
    The Next.js API routes should forward `x-user-id` after verifying
    the session token server-side. Never trust client-supplied headers
    directly in a public API.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id header",
        )
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-User-Id format",
        )


# ── Convenience type aliases (for cleaner endpoint signatures) ─────────────

DbSession    = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser  = Annotated[uuid.UUID, Depends(get_current_user_id)]
InvoiceSvc   = Annotated[InvoiceService, Depends(get_invoice_service)]
