"""
Pure domain entities — no SQLAlchemy, no Pydantic, no I/O.

These are plain Python dataclasses that encode business rules.
They are constructed by the service layer from ORM models and
are what the service layer operates on and returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.domain.enums import Currency, ExpenseCategory, InvoiceStatus


@dataclass(frozen=True)
class Money:
    """Value object: amount + currency as an indivisible pair."""
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if self.amount < Decimal("0"):
            raise ValueError("Money amount cannot be negative")


@dataclass
class ExtractedInvoiceData:
    """
    Raw extraction result from the AI pipeline.
    Fields are Optional because OCR / LLM may not always find every field.
    """
    vendor_name:    str | None = None
    invoice_number: str | None = None
    invoice_date:   date | None = None
    due_date:       date | None = None
    currency:       Currency | None = None
    subtotal:       Decimal | None = None
    tax_amount:     Decimal | None = None
    total_amount:   Decimal | None = None
    line_items:     list[dict] = field(default_factory=list)
    raw_text:       str = ""
    confidence:     float = 0.0   # 0–1 LLM self-reported confidence


@dataclass
class InvoiceEntity:
    """Core invoice domain object."""
    id:             uuid.UUID
    user_id:        uuid.UUID
    filename:       str
    file_path:      str
    mime_type:      str
    status:         InvoiceStatus
    created_at:     datetime
    updated_at:     datetime

    # Populated after extraction
    vendor_name:    str | None = None
    invoice_number: str | None = None
    invoice_date:   date | None = None
    due_date:       date | None = None
    currency:       Currency | None = None
    subtotal:       Decimal | None = None
    tax_amount:     Decimal | None = None
    total_amount:   Decimal | None = None
    category:       ExpenseCategory | None = None
    confidence:     float = 0.0
    raw_text:       str = ""
    line_items:     list[dict] = field(default_factory=list)
    is_anomaly:     bool = False
    anomaly_reason: str | None = None
    duplicate_of:   uuid.UUID | None = None

    @property
    def is_processed(self) -> bool:
        return self.status == InvoiceStatus.PROCESSED

    @property
    def total_money(self) -> Money | None:
        if self.total_amount is not None and self.currency is not None:
            return Money(amount=self.total_amount, currency=self.currency)
        return None


@dataclass(frozen=True)
class DuplicateMatch:
    """Result of a duplicate-detection check."""
    candidate_id:   uuid.UUID
    invoice_number_match: bool
    vendor_match:   bool
    amount_match:   bool
    similarity_score: float    # 0–1 composite score
    is_duplicate:   bool


@dataclass
class MonthlySummary:
    """Aggregated expense summary for a calendar month."""
    year:           int
    month:          int
    total_spending: Decimal
    currency:       Currency
    invoice_count:  int
    category_breakdown: dict[ExpenseCategory, Decimal]
    category_counts: dict[ExpenseCategory, int]
    top_vendors:    list[dict]   # [{"vendor": str, "total": str, "count": int}]
    ai_narrative:   str          # LLM-generated prose summary


@dataclass
class AnomalyReport:
    """Anomaly detection result for one invoice."""
    invoice_id:   uuid.UUID
    is_anomaly:   bool
    zscore:       float | None
    reason:       str | None
    similar_invoices: list[DuplicateMatch]
