"""Initial schema: users, invoices, processing_jobs.

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",         UUID(as_uuid=True), primary_key=True),
        sa.Column("email",      sa.String(320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── invoices ───────────────────────────────────────────────────────────
    op.create_table(
        "invoices",
        sa.Column("id",             UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename",       sa.String(512), nullable=False),
        sa.Column("file_path",      sa.String(1024), nullable=False),
        sa.Column("mime_type",      sa.String(128), nullable=False),
        sa.Column("file_size",      sa.Integer, nullable=False),
        sa.Column("status",         sa.String(32), nullable=False, server_default="pending"),

        # Extracted fields
        sa.Column("vendor_name",    sa.String(512)),
        sa.Column("invoice_number", sa.String(256)),
        sa.Column("invoice_date",   sa.Date),
        sa.Column("due_date",       sa.Date),
        sa.Column("currency",       sa.String(8)),
        sa.Column("subtotal",       sa.Numeric(18, 4)),
        sa.Column("tax_amount",     sa.Numeric(18, 4)),
        sa.Column("total_amount",   sa.Numeric(18, 4)),
        sa.Column("category",       sa.String(64)),
        sa.Column("confidence",     sa.Numeric(5, 4), server_default="0"),
        sa.Column("raw_text",       sa.Text, server_default=""),
        sa.Column("line_items",     JSONB, server_default="[]"),

        # Flags
        sa.Column("is_anomaly",     sa.Boolean, server_default="false"),
        sa.Column("anomaly_reason", sa.Text),
        sa.Column("duplicate_of",   UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="SET NULL")),

        # Audit
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_invoices_user_id",       "invoices", ["user_id"])
    op.create_index("ix_invoices_status",         "invoices", ["status"])
    op.create_index("ix_invoices_invoice_date",   "invoices", ["invoice_date"])
    op.create_index("ix_invoices_vendor_name",    "invoices", ["vendor_name"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_index("ix_invoices_category",       "invoices", ["category"])

    # ── processing_jobs ────────────────────────────────────────────────────
    op.create_table(
        "processing_jobs",
        sa.Column("id",             UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id",     UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("celery_task_id", sa.String(256)),
        sa.Column("status",         sa.String(32), nullable=False, server_default="queued"),
        sa.Column("error_msg",      sa.Text),
        sa.Column("attempts",       sa.Integer, server_default="0"),
        sa.Column("started_at",     sa.DateTime(timezone=True)),
        sa.Column("finished_at",    sa.DateTime(timezone=True)),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_invoice_id", "processing_jobs", ["invoice_id"])
    op.create_index("ix_jobs_status",     "processing_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("processing_jobs")
    op.drop_table("invoices")
    op.drop_table("users")
