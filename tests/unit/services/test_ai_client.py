"""Unit tests for AI extraction client response parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.infrastructure.ai import AIExtractionClient
from app.domain.enums import Currency


class TestParseExtractionResponse:

    def test_parses_complete_response(self):
        data = {
            "vendor_name": "Acme Corp",
            "invoice_number": "INV-2024-001",
            "invoice_date": "2024-03-15",
            "due_date": "2024-04-15",
            "currency": "USD",
            "subtotal": 1000.00,
            "tax_amount": 100.00,
            "total_amount": 1100.00,
            "line_items": [{"description": "Software license", "total": 1000}],
            "confidence": 0.95,
        }
        result = AIExtractionClient._parse_extraction_response(data, "raw text")

        assert result.vendor_name == "Acme Corp"
        assert result.invoice_number == "INV-2024-001"
        assert result.currency == Currency.USD
        assert result.total_amount == Decimal("1100.00")
        assert result.confidence == 0.95

    def test_handles_null_fields_gracefully(self):
        data = {
            "vendor_name": None,
            "invoice_number": None,
            "invoice_date": None,
            "currency": None,
            "total_amount": None,
            "confidence": 0.3,
        }
        result = AIExtractionClient._parse_extraction_response(data, "")

        assert result.vendor_name is None
        assert result.total_amount is None
        assert result.currency is None

    def test_handles_invalid_date_format(self):
        data = {"invoice_date": "not-a-date", "confidence": 0.5}
        result = AIExtractionClient._parse_extraction_response(data, "")
        assert result.invoice_date is None

    def test_handles_unknown_currency(self):
        data = {"currency": "XYZ", "confidence": 0.5}
        result = AIExtractionClient._parse_extraction_response(data, "")
        assert result.currency is None

    def test_handles_string_amounts(self):
        data = {"total_amount": "1,234.56", "confidence": 0.8}
        # Commas in amounts: Decimal("1,234.56") will fail — test resilience
        result = AIExtractionClient._parse_extraction_response(data, "")
        # Should not raise; amount may be None if invalid
        assert result is not None
