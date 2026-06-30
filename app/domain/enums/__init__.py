"""Domain enumerations — shared vocabulary across all layers."""

from __future__ import annotations

from enum import StrEnum


class InvoiceStatus(StrEnum):
    PENDING    = "pending"      # Uploaded, not yet processed
    PROCESSING = "processing"   # OCR + AI extraction in progress
    PROCESSED  = "processed"    # Extraction complete, data available
    FAILED     = "failed"       # Processing failed after retries
    DUPLICATE  = "duplicate"    # Marked as duplicate of another invoice


class ExpenseCategory(StrEnum):
    UTILITIES      = "utilities"
    MARKETING      = "marketing"
    TRAVEL         = "travel"
    OFFICE_SUPPLIES = "office_supplies"
    EQUIPMENT      = "equipment"
    SOFTWARE       = "software"
    OTHER          = "other"


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    PKR = "PKR"
    AED = "AED"
    SAR = "SAR"
    # Extend as needed — kept small for clarity


class OCRProvider(StrEnum):
    TESSERACT     = "tesseract"
    AWS_TEXTRACT  = "aws_textract"
    GOOGLE_VISION = "google_vision"


class FileType(StrEnum):
    PDF  = "pdf"
    JPEG = "jpeg"
    PNG  = "png"
    TIFF = "tiff"
