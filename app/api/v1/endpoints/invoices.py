"""
Invoice API endpoints — v1.

All endpoints follow REST conventions:
  POST   /invoices/upload          → 202 Accepted (async processing)
  POST   /invoices/{id}/process    → 200 (sync, for dev/testing)
  GET    /invoices/{id}            → invoice detail
  GET    /invoices                 → paginated list
  GET    /invoices/{id}/duplicates → duplicate detection result
  GET    /invoices/{id}/anomaly    → anomaly report
  GET    /invoices/summary/monthly → monthly summary

Frontend integration notes:
  - All endpoints require X-User-Id header (forwarded from Next.js session)
  - File upload uses multipart/form-data — use FormData in the browser
  - Processing is async: upload returns 202, poll GET /invoices/{id} until
    status = "processed" or "failed"
  - All amounts are strings (Decimal) to avoid JS float precision issues
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.dependencies import CurrentUser, InvoiceSvc
from app.domain.enums import ExpenseCategory, InvoiceStatus
from app.services.celery_tasks import process_invoice_task
from app.schemas import (
    AnomalyReportResponse,
    DuplicateDetectionResponse,
    DuplicateMatchResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceUploadResponse,
    MonthlySummaryResponse,
    PaginationMeta,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])


# ── Upload ─────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InvoiceUploadResponse,
    summary="Upload an invoice (PDF or image)",
    description=(
        "Accepts a PDF, JPEG, PNG, or TIFF. Returns immediately with a "
        "PENDING invoice record. Processing (OCR + AI) runs asynchronously. "
        "Poll GET /invoices/{id} to check status."
    ),
)
async def upload_invoice(
    current_user: CurrentUser,
    service: InvoiceSvc,
    file: UploadFile = File(..., description="Invoice PDF or image"),
) -> InvoiceUploadResponse:
    content = await file.read()
    invoice = await service.upload_invoice(
        user_id=current_user,
        filename=file.filename or "invoice",
        content=content,
        mime_type=file.content_type or "application/octet-stream",
    )
    process_invoice_task.delay(str(invoice.id), str(current_user))
    return InvoiceUploadResponse(
        invoice_id=invoice.id,
        filename=invoice.filename,
        status=invoice.status.value,
    )


# ── Synchronous Processing (dev/webhook use) ───────────────────────────────

@router.post(
    "/{invoice_id}/process",
    response_model=InvoiceDetailResponse,
    summary="Trigger synchronous processing (dev / webhook)",
    description=(
        "Runs the full OCR + AI pipeline synchronously and returns the "
        "extracted invoice. In production, this is triggered by a Celery "
        "worker — use this endpoint for development or webhook-based flows."
    ),
)
async def process_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser,
    service: InvoiceSvc,
) -> InvoiceDetailResponse:
    invoice = await service.process_invoice(invoice_id, current_user)
    return _to_detail_response(invoice)


# ── Read ───────────────────────────────────────────────────────────────────

@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetailResponse,
    summary="Get invoice details",
)
async def get_invoice(
    invoice_id: uuid.UUID,
    current_user: CurrentUser,
    service: InvoiceSvc,
) -> InvoiceDetailResponse:
    invoice = await service.get_invoice(invoice_id, current_user)
    return _to_detail_response(invoice)


@router.get(
    "",
    response_model=InvoiceListResponse,
    summary="List invoices with optional filters",
)
async def list_invoices(
    current_user: CurrentUser,
    service: InvoiceSvc,
    status_filter: InvoiceStatus | None = Query(None, alias="status"),
    category: ExpenseCategory | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> InvoiceListResponse:
    invoices = await service.list_invoices(
        current_user,
        status=status_filter,
        category=category,
        limit=limit,
        offset=offset,
    )
    total = await service.count_invoices(current_user, status=status_filter, category=category)
    return InvoiceListResponse(
        invoices=[_to_detail_response(i) for i in invoices],
        meta=PaginationMeta(
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(invoices) < total,
        ),
    )


# ── Duplicate Detection ────────────────────────────────────────────────────

@router.get(
    "/{invoice_id}/duplicates",
    response_model=DuplicateDetectionResponse,
    summary="Detect duplicate invoices",
)
async def detect_duplicates(
    invoice_id: uuid.UUID,
    current_user: CurrentUser,
    service: InvoiceSvc,
) -> DuplicateDetectionResponse:
    matches = await service.detect_duplicates(invoice_id, current_user)
    match_responses = [
        DuplicateMatchResponse(
            candidate_id=m.candidate_id,
            invoice_number_match=m.invoice_number_match,
            vendor_match=m.vendor_match,
            amount_match=m.amount_match,
            similarity_score=m.similarity_score,
            is_duplicate=m.is_duplicate,
        )
        for m in matches
    ]
    return DuplicateDetectionResponse(
        invoice_id=invoice_id,
        matches=match_responses,
        has_duplicates=any(m.is_duplicate for m in matches),
    )


# ── Anomaly Detection ──────────────────────────────────────────────────────

@router.get(
    "/{invoice_id}/anomaly",
    response_model=AnomalyReportResponse,
    summary="Get anomaly report for an invoice",
)
async def get_anomaly_report(
    invoice_id: uuid.UUID,
    current_user: CurrentUser,
    service: InvoiceSvc,
) -> AnomalyReportResponse:
    report = await service.detect_anomaly(invoice_id, current_user)
    return AnomalyReportResponse(
        invoice_id=report.invoice_id,
        is_anomaly=report.is_anomaly,
        zscore=report.zscore,
        reason=report.reason,
    )


# ── Monthly Summary ────────────────────────────────────────────────────────

@router.get(
    "/summary/monthly",
    response_model=MonthlySummaryResponse,
    summary="AI-powered monthly expense summary",
    description=(
        "Returns aggregated spending data for the given month plus an "
        "AI-generated narrative with insights and recommendations."
    ),
)
async def get_monthly_summary(
    current_user: CurrentUser,
    service: InvoiceSvc,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
) -> MonthlySummaryResponse:
    summary = await service.get_monthly_summary(current_user, year, month)

    breakdown = [
        {
            "category": k.value,
            "amount": v,
            "percentage": float(v / summary.total_spending * 100) if summary.total_spending else 0.0,
            "count": summary.category_counts.get(k, 0),
        }
        for k, v in summary.category_breakdown.items()
    ]

    return MonthlySummaryResponse(
        year=summary.year,
        month=summary.month,
        total_spending=summary.total_spending,
        currency=summary.currency.value,
        invoice_count=summary.invoice_count,
        category_breakdown=breakdown,
        top_vendors=summary.top_vendors,
        ai_narrative=summary.ai_narrative,
    )


# ── Mapping helper ─────────────────────────────────────────────────────────

def _to_detail_response(invoice) -> InvoiceDetailResponse:
    return InvoiceDetailResponse(
        id=invoice.id,
        filename=invoice.filename,
        status=invoice.status.value,
        vendor_name=invoice.vendor_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency.value if invoice.currency else None,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        total_amount=invoice.total_amount,
        category=invoice.category.value if invoice.category else None,
        confidence=invoice.confidence,
        is_anomaly=invoice.is_anomaly,
        anomaly_reason=invoice.anomaly_reason,
        duplicate_of=invoice.duplicate_of,
        line_items=invoice.line_items or [],
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )
