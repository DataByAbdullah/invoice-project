"""
Unit tests for InvoiceService.

All external dependencies (repository, OCR, AI) are mocked.
Tests verify business logic — not infrastructure behaviour.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.entities import ExtractedInvoiceData, InvoiceEntity
from app.domain.enums import Currency, ExpenseCategory, InvoiceStatus
from app.domain.exceptions import FileSizeExceededError, UnsupportedFileTypeError
from app.services.invoice_service import InvoiceService


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_repository() -> AsyncMock:
    repo = AsyncMock()
    return repo


@pytest.fixture
def mock_ocr() -> AsyncMock:
    ocr = AsyncMock()
    ocr.extract_text.return_value = "ACME Corp\nInvoice #INV-001\nTotal: $1,250.00"
    return ocr


@pytest.fixture
def mock_ai() -> AsyncMock:
    ai = AsyncMock()
    ai.extract_invoice_data.return_value = ExtractedInvoiceData(
        vendor_name="ACME Corp",
        invoice_number="INV-001",
        currency=Currency.USD,
        total_amount=Decimal("1250.00"),
        tax_amount=Decimal("125.00"),
        confidence=0.95,
    )
    ai.categorize_expense.return_value = (ExpenseCategory.SOFTWARE, 0.88)
    ai.generate_monthly_narrative.return_value = "Spending was normal this month."
    ai.assess_anomaly.return_value = {"is_anomaly": False, "reason": None}
    return ai


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_invoice(user_id: uuid.UUID) -> InvoiceEntity:
    return InvoiceEntity(
        id=uuid.uuid4(),
        user_id=user_id,
        filename="test_invoice.pdf",
        file_path="/tmp/test_invoice.pdf",
        mime_type="application/pdf",
        status=InvoiceStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def service(mock_repository, mock_ocr, mock_ai) -> InvoiceService:
    return InvoiceService(
        repository=mock_repository,
        ocr_provider=mock_ocr,
        ai_client=mock_ai,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Upload tests
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadInvoice:

    @pytest.mark.asyncio
    async def test_upload_valid_pdf(self, service, mock_repository, user_id, sample_invoice, tmp_path):
        mock_repository.create.return_value = sample_invoice

        with patch.object(service, "_persist_file", return_value=tmp_path / "invoice.pdf"):
            result = await service.upload_invoice(
                user_id=user_id,
                filename="invoice.pdf",
                content=b"%PDF-1.4 fake content",
                mime_type="application/pdf",
            )

        assert result.status == InvoiceStatus.PENDING
        mock_repository.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_rejects_unsupported_mime(self, service, user_id):
        with pytest.raises(UnsupportedFileTypeError):
            await service.upload_invoice(
                user_id=user_id,
                filename="doc.exe",
                content=b"fake",
                mime_type="application/octet-stream",
            )

    @pytest.mark.asyncio
    async def test_upload_rejects_oversized_file(self, service, user_id):
        # Generate content larger than limit (20MB)
        large_content = b"x" * (21 * 1024 * 1024)
        with pytest.raises(FileSizeExceededError):
            await service.upload_invoice(
                user_id=user_id,
                filename="huge.pdf",
                content=large_content,
                mime_type="application/pdf",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Processing pipeline tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessInvoice:

    @pytest.mark.asyncio
    async def test_full_pipeline_success(
        self, service, mock_repository, mock_ocr, mock_ai, user_id, sample_invoice
    ):
        processed_invoice = InvoiceEntity(
            **{
                **sample_invoice.__dict__,
                "status": InvoiceStatus.PROCESSED,
                "vendor_name": "ACME Corp",
                "total_amount": Decimal("1250.00"),
                "category": ExpenseCategory.SOFTWARE,
            }
        )
        mock_repository.get_by_id.return_value = sample_invoice
        mock_repository.update_extracted_data.return_value = processed_invoice
        mock_repository.find_potential_duplicates.return_value = []

        result = await service.process_invoice(sample_invoice.id, user_id)

        assert result.status == InvoiceStatus.PROCESSED
        mock_ocr.extract_text.assert_called_once()
        mock_ai.extract_invoice_data.assert_called_once()
        mock_ai.categorize_expense.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_marks_failed_on_error(
        self, service, mock_repository, mock_ocr, user_id, sample_invoice
    ):
        mock_repository.get_by_id.return_value = sample_invoice
        mock_ocr.extract_text.side_effect = Exception("Tesseract crashed")

        with pytest.raises(Exception, match="Tesseract crashed"):
            await service.process_invoice(sample_invoice.id, user_id)

        mock_repository.update_status.assert_called_with(
            sample_invoice.id, InvoiceStatus.FAILED
        )


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate detection tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateDetection:

    @pytest.mark.asyncio
    async def test_exact_duplicate_detected(
        self, service, mock_repository, user_id, sample_invoice
    ):
        invoice = InvoiceEntity(
            **{
                **sample_invoice.__dict__,
                "invoice_number": "INV-001",
                "vendor_name": "ACME Corp",
                "total_amount": Decimal("1000.00"),
                "currency": Currency.USD,
            }
        )
        candidate = InvoiceEntity(
            **{
                **sample_invoice.__dict__,
                "id": uuid.uuid4(),
                "invoice_number": "INV-001",    # same number
                "vendor_name": "ACME Corp",     # same vendor
                "total_amount": Decimal("1000.00"),
            }
        )

        mock_repository.get_by_id.return_value = invoice
        mock_repository.find_potential_duplicates.return_value = [candidate]

        matches = await service.detect_duplicates(invoice.id, user_id)

        assert len(matches) == 1
        assert matches[0].is_duplicate is True
        assert matches[0].invoice_number_match is True

    @pytest.mark.asyncio
    async def test_no_duplicates_returns_empty(
        self, service, mock_repository, user_id, sample_invoice
    ):
        mock_repository.get_by_id.return_value = sample_invoice
        mock_repository.find_potential_duplicates.return_value = []

        matches = await service.detect_duplicates(sample_invoice.id, user_id)

        assert matches == []


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTextNormalization:

    def test_collapses_multiple_blank_lines(self, service):
        text = "Line 1\n\n\n\n\nLine 2"
        result = service._normalize_text(text)
        assert "\n\n\n" not in result

    def test_removes_non_printable_chars(self, service):
        text = "Hello\x00World\x01\x02"
        result = service._normalize_text(text)
        assert "\x00" not in result
        assert "HelloWorld" in result
