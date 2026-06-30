"""
Domain exception hierarchy.

All application errors derive from InvoiceAIError so callers can catch broadly
or narrowly. HTTP translation happens in the exception handlers registered on
the FastAPI app — exceptions never carry HTTP status codes here (domain stays
pure).
"""

from __future__ import annotations


class InvoiceAIError(Exception):
    """Root exception for the entire application."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


# ── Resource Errors ────────────────────────────────────────────────────────────

class NotFoundError(InvoiceAIError):
    """Requested resource does not exist."""


class AlreadyExistsError(InvoiceAIError):
    """Resource with given identity already exists (e.g. duplicate invoice)."""


# ── Validation / Input Errors ─────────────────────────────────────────────────

class ValidationError(InvoiceAIError):
    """Incoming data failed business-rule validation."""


class UnsupportedFileTypeError(ValidationError):
    """Uploaded file MIME type is not in the allow-list."""


class FileSizeExceededError(ValidationError):
    """Uploaded file exceeds the configured size limit."""


# ── Processing Errors ─────────────────────────────────────────────────────────

class OCRError(InvoiceAIError):
    """OCR extraction failed or produced unusable output."""


class ExtractionError(InvoiceAIError):
    """LLM-based data extraction failed or returned malformed JSON."""


class CategorizationError(InvoiceAIError):
    """Expense categorization step failed."""


# ── Infrastructure Errors ─────────────────────────────────────────────────────

class AIProviderError(InvoiceAIError):
    """Upstream AI API returned an error or timed out."""


class StorageError(InvoiceAIError):
    """File storage operation failed."""


class DatabaseError(InvoiceAIError):
    """Unexpected database error that was not handled by the repository."""
