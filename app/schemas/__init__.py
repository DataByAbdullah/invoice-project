"""
Pydantic v2 request/response schemas — the API contract.

These schemas are the boundary between the domain layer and HTTP.
They define exactly what the Next.js frontend sends and receives.
Schemas are NOT the same as domain entities — they're shaped for API consumers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Shared
# ─────────────────────────────────────────────────────────────────────────────

class APIResponse(BaseModel):
    """Envelope for all non-error responses."""
    success: bool = True
    message: str | None = None


class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool


# ─────────────────────────────────────────────────────────────────────────────
# Invoice Schemas
# ─────────────────────────────────────────────────────────────────────────────

class InvoiceUploadResponse(BaseModel):
    """Returned immediately on upload. Processing is async."""
    model_config = ConfigDict(from_attributes=True)

    invoice_id: uuid.UUID
    filename:   str
    status:     str
    message:    str = "Invoice uploaded. Processing started."


class LineItemSchema(BaseModel):
    description: str | None = None
    quantity:    float | None = None
    unit_price:  Decimal | None = None
    total:       Decimal | None = None


class InvoiceDetailResponse(BaseModel):
    """Full invoice detail — returned by GET /invoices/{id}."""
    model_config = ConfigDict(from_attributes=True)

    id:             uuid.UUID
    filename:       str
    status:         str
    vendor_name:    str | None
    invoice_number: str | None
    invoice_date:   date | None
    due_date:       date | None
    currency:       str | None
    subtotal:       Decimal | None
    tax_amount:     Decimal | None
    total_amount:   Decimal | None
    category:       str | None
    confidence:     float
    is_anomaly:     bool
    anomaly_reason: str | None
    duplicate_of:   uuid.UUID | None
    line_items:     list[LineItemSchema] = Field(default_factory=list)
    created_at:     datetime
    updated_at:     datetime


class InvoiceListResponse(BaseModel):
    invoices: list[InvoiceDetailResponse]
    meta:     PaginationMeta


# ─────────────────────────────────────────────────────────────────────────────
# Summary Schemas
# ─────────────────────────────────────────────────────────────────────────────

class CategoryBreakdownItem(BaseModel):
    category:   str
    amount:     Decimal
    percentage: float
    count:      int


class TopVendorItem(BaseModel):
    vendor: str
    total:  Decimal
    count:  int


class MonthlySummaryResponse(BaseModel):
    year:              int
    month:             int
    total_spending:    Decimal
    currency:          str
    invoice_count:     int
    category_breakdown: list[CategoryBreakdownItem]
    top_vendors:       list[TopVendorItem]
    ai_narrative:      str


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate / Anomaly Schemas
# ─────────────────────────────────────────────────────────────────────────────

class DuplicateMatchResponse(BaseModel):
    candidate_id:          uuid.UUID
    invoice_number_match:  bool
    vendor_match:          bool
    amount_match:          bool
    similarity_score:      float
    is_duplicate:          bool


class DuplicateDetectionResponse(BaseModel):
    invoice_id: uuid.UUID
    matches:    list[DuplicateMatchResponse]
    has_duplicates: bool


class AnomalyReportResponse(BaseModel):
    invoice_id: uuid.UUID
    is_anomaly: bool
    zscore:     float | None
    reason:     str | None


# ─────────────────────────────────────────────────────────────────────────────
# Processing Job Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ProcessingJobResponse(BaseModel):
    job_id:     uuid.UUID
    invoice_id: uuid.UUID
    status:     str
    message:    str


# ─────────────────────────────────────────────────────────────────────────────
# Error Schemas (used by exception handlers)
# ─────────────────────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code:    str
    message: str
    detail:  str | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    error:   ErrorDetail
