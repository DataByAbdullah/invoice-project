"""SQLAlchemy ORM models — database representation layer."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base — all models inherit from here."""
    pass


class UserModel(Base):
    """
    Minimal user model — auth is expected to be handled by the Next.js
    layer or a dedicated auth service. We only store the user identifier
    so every invoice is scoped to an owner.
    """
    __tablename__ = "users"

    id:         Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email:      Mapped[str]        = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    invoices: Mapped[list["InvoiceModel"]] = relationship("InvoiceModel", back_populates="user", lazy="noload")


class InvoiceModel(Base):
    """
    Central invoice table. A single row holds both the file metadata
    (populated on upload) and extracted fields (populated after AI processing).

    Design note: storing raw_text and line_items (JSONB) on the invoice row
    rather than a separate table keeps queries simple and fast for the common
    case (read one invoice). If line items need to be queried individually
    (e.g. "find all invoices with a line item > $1000"), extract them to a
    child table — that's a straightforward schema migration.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_user_id",        "user_id"),
        Index("ix_invoices_status",          "status"),
        Index("ix_invoices_invoice_date",    "invoice_date"),
        Index("ix_invoices_vendor_name",     "vendor_name"),
        Index("ix_invoices_invoice_number",  "invoice_number"),
        Index("ix_invoices_category",        "category"),
    )

    id:             Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id:        Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename:       Mapped[str]            = mapped_column(String(512), nullable=False)
    file_path:      Mapped[str]            = mapped_column(String(1024), nullable=False)
    mime_type:      Mapped[str]            = mapped_column(String(128), nullable=False)
    file_size:      Mapped[int]            = mapped_column(Integer, nullable=False)  # bytes
    status:         Mapped[str]            = mapped_column(String(32), nullable=False, default="pending")

    # ── Extracted Fields ───────────────────────────────────────────────────
    vendor_name:    Mapped[str | None]     = mapped_column(String(512))
    invoice_number: Mapped[str | None]     = mapped_column(String(256))
    invoice_date:   Mapped[date | None]    = mapped_column(Date)
    due_date:       Mapped[date | None]    = mapped_column(Date)
    currency:       Mapped[str | None]     = mapped_column(String(8))
    subtotal:       Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    tax_amount:     Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total_amount:   Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    category:       Mapped[str | None]     = mapped_column(String(64))
    confidence:     Mapped[float]          = mapped_column(Numeric(5, 4), default=0.0)
    raw_text:       Mapped[str]            = mapped_column(Text, default="")
    line_items:     Mapped[list[Any]]      = mapped_column(JSONB, default=list)

    # ── Anomaly / Duplicate Flags ──────────────────────────────────────────
    is_anomaly:     Mapped[bool]           = mapped_column(Boolean, default=False)
    anomaly_reason: Mapped[str | None]     = mapped_column(Text)
    duplicate_of:   Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)

    # ── Audit ──────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ── Relationships ──────────────────────────────────────────────────────
    user:        Mapped["UserModel"]          = relationship("UserModel", back_populates="invoices")
    duplicate_source: Mapped["InvoiceModel | None"] = relationship("InvoiceModel", remote_side="InvoiceModel.id", foreign_keys=[duplicate_of])


class ProcessingJobModel(Base):
    """
    Tracks async processing jobs for invoices.
    Allows the API to return 202 Accepted immediately and let the client
    poll for completion — crucial for large PDFs that take 10–30 seconds.
    """
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_jobs_invoice_id", "invoice_id"),
        Index("ix_jobs_status",     "status"),
    )

    id:          Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id:  Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(256))
    status:      Mapped[str]       = mapped_column(String(32), nullable=False, default="queued")
    error_msg:   Mapped[str | None] = mapped_column(Text)
    attempts:    Mapped[int]       = mapped_column(Integer, default=0)
    started_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at:  Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())

    invoice: Mapped["InvoiceModel"] = relationship("InvoiceModel")
