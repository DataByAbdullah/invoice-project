"""
Repository layer — all database I/O goes through here.

Repositories are the only place that imports SQLAlchemy. Services work with
domain entities; repositories translate between entities and ORM models.

Design: Abstract base class defines the interface (contract). Concrete
SQLAlchemy implementation can be swapped for an in-memory implementation in
tests without touching service code.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    ExtractedInvoiceData,
    InvoiceEntity,
    MonthlySummary,
)
from app.domain.enums import ExpenseCategory, InvoiceStatus
from app.domain.exceptions import NotFoundError
from app.infrastructure.persistence.models import InvoiceModel, UserModel


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────

class AbstractInvoiceRepository(ABC):

    @abstractmethod
    async def create(self, entity: InvoiceEntity) -> InvoiceEntity: ...

    @abstractmethod
    async def get_by_id(self, invoice_id: uuid.UUID, user_id: uuid.UUID) -> InvoiceEntity: ...

    @abstractmethod
    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InvoiceEntity]: ...

    @abstractmethod
    async def update_status(self, invoice_id: uuid.UUID, status: InvoiceStatus) -> None: ...

    @abstractmethod
    async def update_extracted_data(
        self,
        invoice_id: uuid.UUID,
        data: ExtractedInvoiceData,
        category: ExpenseCategory,
    ) -> InvoiceEntity: ...

    @abstractmethod
    async def mark_anomaly(
        self, invoice_id: uuid.UUID, reason: str
    ) -> None: ...

    @abstractmethod
    async def mark_duplicate(
        self, invoice_id: uuid.UUID, original_id: uuid.UUID
    ) -> None: ...

    @abstractmethod
    async def find_potential_duplicates(
        self,
        user_id: uuid.UUID,
        invoice_number: str | None,
        vendor_name: str | None,
        total_amount: Decimal | None,
        exclude_id: uuid.UUID | None = None,
    ) -> list[InvoiceEntity]: ...

    @abstractmethod
    async def get_or_create_user(self, user_id: uuid.UUID) -> None: ...

    @abstractmethod
    async def count_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
    ) -> int: ...

    @abstractmethod
    async def get_monthly_aggregates(
        self, user_id: uuid.UUID, year: int, month: int
    ) -> dict: ...


# ─────────────────────────────────────────────────────────────────────────────
# SQLAlchemy implementation
# ─────────────────────────────────────────────────────────────────────────────

class SQLInvoiceRepository(AbstractInvoiceRepository):
    """Production repository backed by PostgreSQL via async SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Mapping helpers ────────────────────────────────────────────────────

    @staticmethod
    def _to_entity(model: InvoiceModel) -> InvoiceEntity:
        from app.domain.enums import Currency
        return InvoiceEntity(
            id=model.id,
            user_id=model.user_id,
            filename=model.filename,
            file_path=model.file_path,
            mime_type=model.mime_type,
            status=InvoiceStatus(model.status),
            vendor_name=model.vendor_name,
            invoice_number=model.invoice_number,
            invoice_date=model.invoice_date,
            due_date=model.due_date,
            currency=Currency(model.currency) if model.currency else None,
            subtotal=model.subtotal,
            tax_amount=model.tax_amount,
            total_amount=model.total_amount,
            category=ExpenseCategory(model.category) if model.category else None,
            confidence=float(model.confidence or 0),
            raw_text=model.raw_text or "",
            line_items=model.line_items or [],
            is_anomaly=model.is_anomaly,
            anomaly_reason=model.anomaly_reason,
            duplicate_of=model.duplicate_of,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    # ── User ───────────────────────────────────────────────────────────────

    async def get_or_create_user(self, user_id: uuid.UUID) -> None:
        """Ensure a user row exists. Auth is handled externally — we only need the ID."""
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            self._session.add(UserModel(
                id=user_id,
                email=f"user-{user_id}@placeholder.local",
            ))
            await self._session.flush()

    # ── CRUD ───────────────────────────────────────────────────────────────

    async def create(self, entity: InvoiceEntity) -> InvoiceEntity:
        model = InvoiceModel(
            id=entity.id,
            user_id=entity.user_id,
            filename=entity.filename,
            file_path=entity.file_path,
            mime_type=entity.mime_type,
            file_size=0,  # updated on upload
            status=entity.status.value,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, invoice_id: uuid.UUID, user_id: uuid.UUID) -> InvoiceEntity:
        stmt = select(InvoiceModel).where(
            and_(InvoiceModel.id == invoice_id, InvoiceModel.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Invoice {invoice_id} not found")
        return self._to_entity(model)

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InvoiceEntity]:
        stmt = select(InvoiceModel).where(InvoiceModel.user_id == user_id)
        if status:
            stmt = stmt.where(InvoiceModel.status == status.value)
        if category:
            stmt = stmt.where(InvoiceModel.category == category.value)
        stmt = stmt.order_by(InvoiceModel.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update_status(self, invoice_id: uuid.UUID, status: InvoiceStatus) -> None:
        await self._session.execute(
            update(InvoiceModel)
            .where(InvoiceModel.id == invoice_id)
            .values(status=status.value)
        )

    async def update_extracted_data(
        self,
        invoice_id: uuid.UUID,
        data: ExtractedInvoiceData,
        category: ExpenseCategory,
    ) -> InvoiceEntity:
        stmt = select(InvoiceModel).where(InvoiceModel.id == invoice_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Invoice {invoice_id} not found")

        model.vendor_name    = data.vendor_name
        model.invoice_number = data.invoice_number
        model.invoice_date   = data.invoice_date
        model.due_date       = data.due_date
        model.currency       = data.currency.value if data.currency else None
        model.subtotal       = data.subtotal
        model.tax_amount     = data.tax_amount
        model.total_amount   = data.total_amount
        model.category       = category.value
        model.confidence     = data.confidence
        model.raw_text       = data.raw_text
        model.line_items     = data.line_items
        model.status         = InvoiceStatus.PROCESSED.value

        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def mark_anomaly(self, invoice_id: uuid.UUID, reason: str) -> None:
        await self._session.execute(
            update(InvoiceModel)
            .where(InvoiceModel.id == invoice_id)
            .values(is_anomaly=True, anomaly_reason=reason)
        )

    async def mark_duplicate(self, invoice_id: uuid.UUID, original_id: uuid.UUID) -> None:
        await self._session.execute(
            update(InvoiceModel)
            .where(InvoiceModel.id == invoice_id)
            .values(status=InvoiceStatus.DUPLICATE.value, duplicate_of=original_id)
        )

    async def count_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: InvoiceStatus | None = None,
        category: ExpenseCategory | None = None,
    ) -> int:
        stmt = select(func.count(InvoiceModel.id)).where(InvoiceModel.user_id == user_id)
        if status:
            stmt = stmt.where(InvoiceModel.status == status.value)
        if category:
            stmt = stmt.where(InvoiceModel.category == category.value)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def find_potential_duplicates(
        self,
        user_id: uuid.UUID,
        invoice_number: str | None,
        vendor_name: str | None,
        total_amount: Decimal | None,
        exclude_id: uuid.UUID | None = None,
    ) -> list[InvoiceEntity]:
        """
        Broad-net query: return candidates that share any of the three key
        fields. Fine-grained similarity scoring happens in the service layer
        with full business logic.
        """
        filters = [InvoiceModel.user_id == user_id]
        if exclude_id:
            filters.append(InvoiceModel.id != exclude_id)

        or_conditions = []
        if invoice_number:
            or_conditions.append(InvoiceModel.invoice_number == invoice_number)
        if vendor_name:
            or_conditions.append(InvoiceModel.vendor_name.ilike(f"%{vendor_name}%"))
        if total_amount is not None:
            # ±1% tolerance for amount matching
            tolerance = total_amount * Decimal("0.01")
            or_conditions.append(
                and_(
                    InvoiceModel.total_amount >= total_amount - tolerance,
                    InvoiceModel.total_amount <= total_amount + tolerance,
                )
            )

        if not or_conditions:
            return []

        stmt = (
            select(InvoiceModel)
            .where(and_(*filters, or_(*or_conditions)))
            .limit(20)
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_monthly_aggregates(
        self, user_id: uuid.UUID, year: int, month: int
    ) -> dict:
        """
        Returns raw aggregate data; the service layer shapes it into
        MonthlySummary and adds AI narrative.
        """
        stmt = (
            select(
                func.sum(InvoiceModel.total_amount).label("total"),
                func.count(InvoiceModel.id).label("count"),
                InvoiceModel.category,
                InvoiceModel.vendor_name,
            )
            .where(
                and_(
                    InvoiceModel.user_id == user_id,
                    InvoiceModel.status == InvoiceStatus.PROCESSED.value,
                    func.extract("year", InvoiceModel.invoice_date) == year,
                    func.extract("month", InvoiceModel.invoice_date) == month,
                )
            )
            .group_by(InvoiceModel.category, InvoiceModel.vendor_name)
        )
        result = await self._session.execute(stmt)
        rows = result.fetchall()
        return {"rows": rows, "year": year, "month": month}
