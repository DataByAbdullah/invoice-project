"""Health check endpoints — used by load balancers and k8s probes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.dependencies import DbSession

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health_check() -> HealthResponse:
    from app.core.settings import get_settings
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get("/health/ready", include_in_schema=False)
async def readiness_check(session: DbSession) -> dict:
    """Kubernetes readiness probe — verifies database connectivity."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        )
    return {"status": "ready"}
