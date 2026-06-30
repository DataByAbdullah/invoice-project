"""
Global FastAPI exception handlers.

Maps domain exceptions → HTTP responses.
This is the ONLY place that knows about both domains and HTTP.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import (
    AlreadyExistsError,
    ExtractionError,
    FileSizeExceededError,
    InvoiceAIError,
    NotFoundError,
    OCRError,
    UnsupportedFileTypeError,
    ValidationError,
)
from app.schemas import ErrorDetail, ErrorResponse


def _error_response(code: str, message: str, detail: str | None, status_code: int) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, detail=detail)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return _error_response("NOT_FOUND", exc.message, exc.detail, 404)


async def already_exists_handler(request: Request, exc: AlreadyExistsError) -> JSONResponse:
    return _error_response("ALREADY_EXISTS", exc.message, exc.detail, 409)


async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return _error_response("VALIDATION_ERROR", exc.message, exc.detail, 422)


async def unsupported_file_handler(request: Request, exc: UnsupportedFileTypeError) -> JSONResponse:
    return _error_response("UNSUPPORTED_FILE_TYPE", exc.message, exc.detail, 415)


async def file_size_handler(request: Request, exc: FileSizeExceededError) -> JSONResponse:
    return _error_response("FILE_TOO_LARGE", exc.message, exc.detail, 413)


async def ocr_error_handler(request: Request, exc: OCRError) -> JSONResponse:
    return _error_response("OCR_FAILED", exc.message, exc.detail, 422)


async def extraction_error_handler(request: Request, exc: ExtractionError) -> JSONResponse:
    return _error_response("EXTRACTION_FAILED", exc.message, exc.detail, 422)


async def generic_invoice_ai_handler(request: Request, exc: InvoiceAIError) -> JSONResponse:
    return _error_response("INTERNAL_ERROR", exc.message, exc.detail, 500)


def register_exception_handlers(app) -> None:
    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(AlreadyExistsError, already_exists_handler)
    app.add_exception_handler(UnsupportedFileTypeError, unsupported_file_handler)
    app.add_exception_handler(FileSizeExceededError, file_size_handler)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.add_exception_handler(OCRError, ocr_error_handler)
    app.add_exception_handler(ExtractionError, extraction_error_handler)
    app.add_exception_handler(InvoiceAIError, generic_invoice_ai_handler)
