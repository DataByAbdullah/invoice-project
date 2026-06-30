"""
Integration tests for the Invoice API endpoints.

Uses httpx.AsyncClient against the real FastAPI app.
Database and external services are mocked at the dependency level —
we're testing HTTP routing, serialization, and error handling, not infrastructure.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.domain.entities import InvoiceEntity
from app.domain.enums import InvoiceStatus
from app.main import app


USER_ID = str(uuid.uuid4())
HEADERS = {"X-User-Id": USER_ID}


@pytest.fixture
def sample_invoice() -> InvoiceEntity:
    return InvoiceEntity(
        id=uuid.uuid4(),
        user_id=uuid.UUID(USER_ID),
        filename="test.pdf",
        file_path="/tmp/test.pdf",
        mime_type="application/pdf",
        status=InvoiceStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestUploadEndpoint:

    @pytest.mark.asyncio
    async def test_upload_returns_202(self, client, sample_invoice):
        mock_service = AsyncMock()
        mock_service.upload_invoice.return_value = sample_invoice

        with patch("app.api.v1.endpoints.invoices.InvoiceSvc", mock_service):
            # Use dependency override instead
            pass

        # Simpler: test the route directly with overridden dependencies
        from app.core.dependencies import get_invoice_service
        app.dependency_overrides[get_invoice_service] = lambda: mock_service

        try:
            files = {"file": ("invoice.pdf", io.BytesIO(b"%PDF test"), "application/pdf")}
            response = await client.post("/api/v1/invoices/upload", files=files, headers=HEADERS)
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "pending"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_upload_requires_user_header(self, client):
        files = {"file": ("invoice.pdf", io.BytesIO(b"%PDF test"), "application/pdf")}
        response = await client.post("/api/v1/invoices/upload", files=files)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestGetInvoice:

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, client):
        from app.core.dependencies import get_invoice_service
        from app.domain.exceptions import NotFoundError

        mock_service = AsyncMock()
        mock_service.get_invoice.side_effect = NotFoundError("Invoice not found")
        app.dependency_overrides[get_invoice_service] = lambda: mock_service

        try:
            invoice_id = uuid.uuid4()
            response = await client.get(f"/api/v1/invoices/{invoice_id}", headers=HEADERS)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "NOT_FOUND"
        finally:
            app.dependency_overrides.clear()
