"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.endpoints.invoices import router as invoices_router
from app.api.v1.endpoints.health import router as health_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(invoices_router)
api_v1_router.include_router(health_router)
