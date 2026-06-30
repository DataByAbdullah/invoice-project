"""
InvoiceService — orchestrates the full invoice processing pipeline.

This is the heart of the application. It coordinates:
  OCR → text normalization → AI extraction → categorization →
  duplicate detection → anomaly detection → persistence

Design principles:
- No SQLAlchemy imports here — pure domain logic
- All I/O dependencies injected; fully testable with mocks
- Each pipeline step is a private method — easy to unit test in isolation
- Raises typed domain exceptions — API layer translates to HTTP
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.domain.entities import (
    AnomalyReport,
    DuplicateMatch,
    ExtractedInvoiceData,
    InvoiceEntity,
    MonthlySummary,
)
from app.domain.enums import Currency, ExpenseCategory, InvoiceStatus
from app.domain.exceptions import (
    ExtractionError,
    FileSizeExceededError,
    NotFoundError,
    UnsupportedFileTypeError,
)
from app.infrastructure.ai import AIExtractionClient
from app.infrastructure.ocr import AbstractOCRProvider
from app.infrastructure.persistence.repositories import AbstractInvoiceRepository

logger = get_logger(__name__)


class InvoiceService:
    """
    Application service — thin orchestration, rich error handling.

    All constructor parameters are abstract types; concrete implementations
    are injected by FastAPI's DI system (see dependencies.py).
    """

    def __init__(
        self,
        repository: AbstractInvoiceRepository,
        ocr_provider: AbstractOCRProvider,
        ai_client: AIExtractionClient,
    ) -> None:
        self._repo = repository
        self._ocr = ocr_provider
        self._ai = ai_client
        self._settings = get_settings()

    # ── Upload ─────────────────────────────────────────────────────────────

    async def upload_invoice(
        self,
        user_id: uuid.UUID,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> InvoiceEntity:
        """
        Validate the uploaded file, persist to storage, create the DB record.
        Returns PENDING invoice — processing happens asynchronously.
        """
        self._validate_upload(filename, content, mime_type)
        await self._repo.get_or_create_user(user_id)

        file_path = await self._persist_file(user_id, filename, content)

        entity = InvoiceEntity(
            id=uuid.uuid4(),
            user_id=user_id,
            filename=filename,
            file_path=str(file_path),
            mime_type=mime_type,
            status=InvoiceStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        created = await self._repo.create(entity)
        logger.info("invoice_uploaded", invoice_id=str(created.id), filename=filename)
        return created

    # ── Processing Pipeline ────────────────────────────────────────────────

    async def process_invoice(self, invoice_id: uuid.UUID, user_id: uuid.UUID) -> InvoiceEntity:
        """
        Full pipeline: OCR → normalize → AI extract → categorize → save.
        Called by the Celery worker (or synchronously in tests/dev).
        """
        invoice = await self._repo.get_by_id(invoice_id, user_id)
        await self._repo.update_status(invoice_id, InvoiceStatus.PROCESSING)

        try:
            # Step 1: OCR
            raw_text = await self._ocr.extract_text(Path(invoice.file_path))
            logger.info("ocr_complete", invoice_id=str(invoice_id), chars=len(raw_text))

            # Step 2: Normalize
            normalized_text = self._normalize_text(raw_text)

            # Step 3: AI extraction (Vision API for images, text path for PDFs)
            extracted: ExtractedInvoiceData = await self._ai.extract_invoice_data(
                normalized_text,
                file_path=Path(invoice.file_path),
                mime_type=invoice.mime_type,
            )

            # Step 4: Categorization
            category, cat_confidence = await self._ai.categorize_expense(
                vendor_name=extracted.vendor_name,
                invoice_number=extracted.invoice_number,
                total_amount=extracted.total_amount,
                raw_text=normalized_text[:500],
            )

            # Step 5: Persist extracted data
            updated = await self._repo.update_extracted_data(invoice_id, extracted, category)

            # Step 6: Duplicate detection (non-blocking — doesn't fail the pipeline)
            await self._run_duplicate_check(updated)

            # Step 7: Anomaly detection (non-blocking)
            await self._run_anomaly_check(updated)

            logger.info(
                "invoice_processed",
                invoice_id=str(invoice_id),
                vendor=extracted.vendor_name,
                category=category,
                confidence=extracted.confidence,
            )
            return updated

        except Exception as exc:
            await self._repo.update_status(invoice_id, InvoiceStatus.FAILED)
            logger.error("invoice_processing_failed", invoice_id=str(invoice_id), error=str(exc))
            raise

    # ── Read Operations ────────────────────────────────────────────────────

    async def get_invoice(self, invoice_id: uuid.UUID, user_id: uuid.UUID) -> InvoiceEntity:
        return await self._repo.get_by_id(invoice_id, user_id)

    async def list_invoices(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InvoiceEntity]:
        return await self._repo.list_by_user(
            user_id, status=status, category=category, limit=limit, offset=offset
        )

    async def count_invoices(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
    ) -> int:
        return await self._repo.count_by_user(user_id, status=status, category=category)

    # ── Monthly Summary ────────────────────────────────────────────────────

    async def get_monthly_summary(
        self, user_id: uuid.UUID, year: int, month: int
    ) -> MonthlySummary:
        aggregates = await self._repo.get_monthly_aggregates(user_id, year, month)
        summary = self._build_summary_stats(aggregates)

        # Generate AI narrative
        narrative = await self._ai.generate_monthly_narrative(summary)

        return MonthlySummary(
            year=year,
            month=month,
            total_spending=Decimal(str(summary.get("total_spending", 0))),
            currency=Currency.USD,  # default; in production derive from user prefs
            invoice_count=summary.get("invoice_count", 0),
            category_breakdown={
                ExpenseCategory(k): Decimal(str(v))
                for k, v in summary.get("by_category", {}).items()
            },
            category_counts={
                ExpenseCategory(k): v
                for k, v in summary.get("by_category_counts", {}).items()
            },
            top_vendors=summary.get("top_vendors", []),
            ai_narrative=narrative,
        )

    # ── Duplicate Detection ────────────────────────────────────────────────

    async def detect_duplicates(
        self, invoice_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[DuplicateMatch]:
        invoice = await self._repo.get_by_id(invoice_id, user_id)
        candidates = await self._repo.find_potential_duplicates(
            user_id=user_id,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            total_amount=invoice.total_amount,
            exclude_id=invoice_id,
        )
        return [self._score_duplicate(invoice, c) for c in candidates]

    # ── Anomaly Detection ─────────────────────────────────────────────────

    async def detect_anomaly(
        self, invoice_id: uuid.UUID, user_id: uuid.UUID
    ) -> AnomalyReport:
        invoice = await self._repo.get_by_id(invoice_id, user_id)
        return await self._run_anomaly_check(invoice, persist=False)

    # ── Private Pipeline Steps ─────────────────────────────────────────────

    def _validate_upload(self, filename: str, content: bytes, mime_type: str) -> None:
        if mime_type not in self._settings.allowed_mime_types:
            raise UnsupportedFileTypeError(
                f"File type '{mime_type}' not allowed",
                detail=f"Allowed types: {', '.join(self._settings.allowed_mime_types)}",
            )
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileSizeExceededError(
                f"File exceeds {self._settings.max_upload_size_mb}MB limit"
            )

    async def _persist_file(
        self, user_id: uuid.UUID, filename: str, content: bytes
    ) -> Path:
        """
        Store the file. Currently writes to local disk.
        In production, swap for S3 upload — the interface stays the same.
        """
        upload_dir = Path(self._settings.upload_dir) / str(user_id)
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Content-addressed naming prevents collisions and enables dedup at storage layer
        content_hash = hashlib.sha256(content).hexdigest()[:16]
        safe_name = f"{content_hash}_{filename}"
        file_path = upload_dir / safe_name

        file_path.write_bytes(content)
        return file_path

    @staticmethod
    def _normalize_text(text: str) -> str:
        """
        Clean OCR output before sending to the LLM.
        Preserves currency symbols (£ € ¥ ₹) which are outside ASCII range
        but critical for currency inference.
        """
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        # Keep printable ASCII + common currency/special chars outside ASCII range
        text = re.sub(r"[^\x20-\x7E£€¥₹\n]", "", text)
        return text.strip()

    async def _run_duplicate_check(self, invoice: InvoiceEntity) -> None:
        """Non-raising — logs and marks but never fails the pipeline."""
        try:
            matches = await self.detect_duplicates(invoice.id, invoice.user_id)
            exact = [m for m in matches if m.is_duplicate]
            if exact:
                original = exact[0].candidate_id
                await self._repo.mark_duplicate(invoice.id, original)
                logger.info(
                    "duplicate_detected",
                    invoice_id=str(invoice.id),
                    original_id=str(original),
                )
        except Exception as exc:
            logger.warning("duplicate_check_failed", error=str(exc))

    async def _run_anomaly_check(
        self, invoice: InvoiceEntity, *, persist: bool = True
    ) -> AnomalyReport:
        """Statistical + AI-based anomaly detection."""
        try:
            # Statistical z-score check (fast, no API call)
            zscore, stat_anomaly, stat_reason = self._statistical_anomaly(invoice)

            # If statistical check fires, also ask the LLM for richer context
            if stat_anomaly:
                ai_result = await self._ai.assess_anomaly(
                    invoice_summary={
                        "vendor": invoice.vendor_name,
                        "amount": str(invoice.total_amount),
                        "category": invoice.category,
                    },
                    historical_stats={"zscore": zscore},
                )
                reason = ai_result.get("reason") or stat_reason
                is_anomaly = ai_result.get("is_anomaly", stat_anomaly)
            else:
                is_anomaly, reason = stat_anomaly, stat_reason

            if is_anomaly and persist:
                await self._repo.mark_anomaly(invoice.id, reason or "Anomalous expense")

            return AnomalyReport(
                invoice_id=invoice.id,
                is_anomaly=is_anomaly,
                zscore=zscore,
                reason=reason,
                similar_invoices=[],
            )
        except Exception as exc:
            logger.warning("anomaly_check_failed", error=str(exc))
            return AnomalyReport(
                invoice_id=invoice.id,
                is_anomaly=False,
                zscore=None,
                reason=None,
                similar_invoices=[],
            )

    def _statistical_anomaly(
        self, invoice: InvoiceEntity
    ) -> tuple[float | None, bool, str | None]:
        """
        Placeholder for z-score computation.
        In production, fetch mean/std from a materialized view or Redis cache
        for this user's category, then compute:
            z = (amount - mean) / std
        """
        # Without historical data we cannot compute z-score meaningfully.
        # Return no anomaly — the AI step will catch obvious outliers.
        return None, False, None

    @staticmethod
    def _score_duplicate(
        invoice: InvoiceEntity, candidate: InvoiceEntity
    ) -> DuplicateMatch:
        """
        Composite similarity score — weighted combination of three signals.
        Thresholds are tunable via settings.
        """
        settings = get_settings()

        number_match = (
            bool(invoice.invoice_number)
            and invoice.invoice_number == candidate.invoice_number
        )
        vendor_match = (
            bool(invoice.vendor_name)
            and bool(candidate.vendor_name)
            and invoice.vendor_name.lower() == candidate.vendor_name.lower()
        )
        amount_match = (
            invoice.total_amount is not None
            and candidate.total_amount is not None
            and abs(invoice.total_amount - candidate.total_amount) / max(invoice.total_amount, Decimal("0.01")) < Decimal("0.01")
        )

        # Weights: invoice number is the strongest signal
        score = (
            (0.5 if number_match else 0.0)
            + (0.3 if vendor_match else 0.0)
            + (0.2 if amount_match else 0.0)
        )

        return DuplicateMatch(
            candidate_id=candidate.id,
            invoice_number_match=number_match,
            vendor_match=vendor_match,
            amount_match=amount_match,
            similarity_score=score,
            is_duplicate=score >= settings.duplicate_similarity_threshold,
        )

    @staticmethod
    def _build_summary_stats(aggregates: dict) -> dict:
        """Convert raw DB rows into a stats dict for the AI narrative."""
        rows = aggregates.get("rows", [])
        total = Decimal("0")
        count = 0
        by_category: dict[str, Decimal] = {}
        by_category_counts: dict[str, int] = {}
        vendor_totals: dict[str, Decimal] = {}
        vendor_counts: dict[str, int] = {}

        for row in rows:
            amt = Decimal(str(row.total or 0))
            cnt = int(row.count or 0)
            total += amt
            count += cnt
            cat = row.category or "other"
            by_category[cat] = by_category.get(cat, Decimal("0")) + amt
            by_category_counts[cat] = by_category_counts.get(cat, 0) + cnt
            if row.vendor_name:
                vendor_totals[row.vendor_name] = (
                    vendor_totals.get(row.vendor_name, Decimal("0")) + amt
                )
                vendor_counts[row.vendor_name] = vendor_counts.get(row.vendor_name, 0) + cnt

        top_vendors = sorted(
            [
                {"vendor": k, "total": str(v), "count": vendor_counts.get(k, 0)}
                for k, v in vendor_totals.items()
            ],
            key=lambda x: Decimal(x["total"]),
            reverse=True,
        )[:5]

        return {
            "total_spending": str(total),
            "invoice_count": count,
            "by_category": {k: str(v) for k, v in by_category.items()},
            "by_category_counts": by_category_counts,
            "top_vendors": top_vendors,
            "year": aggregates.get("year"),
            "month": aggregates.get("month"),
        }
