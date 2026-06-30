"""
AI extraction client — OpenAI GPT-4o integration.

Design decisions:
- System prompts are versioned constants (tracked in git, not a config table).
- Images use GPT-4o Vision directly — far more accurate than OCR→text→LLM.
- Text PDFs fall back to the text-only extraction path.
- JSON mode enforced — eliminates parse fragility.
- Temperature=0.0 for deterministic extraction.
- Tenacity handles transient API errors without burdening callers.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, APIError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.domain.entities import ExtractedInvoiceData
from app.domain.enums import Currency, ExpenseCategory
from app.domain.exceptions import AIProviderError, ExtractionError

logger = get_logger(__name__)

# MIME types that support GPT-4o Vision (sent as base64 image)
_IMAGE_MIME_TYPES = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/tif",
    "image/webp",
})

# ─────────────────────────────────────────────────────────────────────────────
# Prompt Engineering
# ─────────────────────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are a precise invoice data extraction engine. Read the OCR-extracted text
from an invoice and return a structured JSON object with every identifiable field.

CRITICAL RULES:
1. Return ONLY valid JSON — no markdown fences, no explanation text.
2. If a field is not found, use null — never guess or fabricate values.
3. DATES: Convert ALL date formats to ISO 8601 (YYYY-MM-DD).
   Examples:
     "September 21, 2028" → "2028-09-21"
     "21/09/2028"         → "2028-09-21"
     "Sep 21 2028"        → "2028-09-21"
     "09-21-2028"         → "2028-09-21"
4. AMOUNTS: Strip all currency symbols and commas — return plain numbers.
   "$28,600"   → 28600
   "$1,000.00" → 1000.00
   "26,000"    → 26000
5. CURRENCY: Infer from context even if no explicit code is written.
   "$" or "USD" present → "USD"
   "£" or "GBP"        → "GBP"
   "€" or "EUR"        → "EUR"
   "A$" or "AUD"       → "AUD"
   Default to "USD" when dollar signs appear but no explicit currency code.
6. INVOICE NUMBER: Look for labels "Invoice #", "Invoice No:", "Invoice Number:",
   "Ref:", "Bill No:", "Receipt #". Preserve the full value including any prefix
   (e.g. "#CON-2028-001", "INV-0042").
7. VENDOR NAME: The company SELLING the goods/services. Usually top-left or in
   the header/logo area. NOT the "Bill To" / buyer company.
8. LINE ITEMS: Extract EVERY row from the items/services table:
   - description: item name ONLY — strip quantity specs
     ("Foundation Work 10 Days" → "Foundation Work")
   - quantity: numeric value from Quantity column
     ("10 Days" → 10, "5 Weeks" → 5, "200 Cubics" → 200)
   - unit_price: price per unit as a plain number
   - total: row total as a plain number
9. confidence: 0.95 if all major fields found clearly; 0.80 if a few are missing;
   0.60 if OCR text is degraded/partial.

RESPONSE SCHEMA (return exactly this structure):
{
  "vendor_name": string | null,
  "invoice_number": string | null,
  "invoice_date": "YYYY-MM-DD" | null,
  "due_date": "YYYY-MM-DD" | null,
  "currency": "USD" | "EUR" | "GBP" | "AUD" | "PKR" | null,
  "subtotal": number | null,
  "tax_amount": number | null,
  "total_amount": number | null,
  "line_items": [
    {"description": string, "quantity": number, "unit_price": number, "total": number}
  ],
  "confidence": number
}
"""

EXTRACTION_SYSTEM_PROMPT_VISION = """\
You are a precise invoice data extraction engine with computer vision.
Examine the invoice image carefully — read EVERY visible text area including
headers, tables, footers, sidebars, and summary boxes.

CRITICAL RULES:
1. Return ONLY valid JSON — no markdown fences, no explanation text.
2. READ THE ENTIRE IMAGE — do not stop at the first field you find.
3. DATES: Convert ALL formats to ISO 8601 (YYYY-MM-DD).
   "September 21, 2028" → "2028-09-21"
   "21/09/28"           → "2028-09-21"
   "Sep 21, 2028"       → "2028-09-21"
4. AMOUNTS: Return plain numbers — no $ signs, no commas.
   "$28,600"  → 28600
   "$1,000"   → 1000
5. CURRENCY: Infer from symbols visible in the image.
   "$" → "USD",  "£" → "GBP",  "€" → "EUR",  "A$" → "AUD"
6. INVOICE NUMBER: Look for "Invoice #", "Invoice No:", "#INV-...", "#CON-...", "Ref:",
   "Receipt No:", "Bill #". Preserve the full value including prefix characters.
7. VENDOR NAME: Company at the TOP of the invoice (seller/from), NOT the "Bill To" company.
8. LINE ITEMS: Extract ALL rows from the items/services table. For each row:
   - description: the item name only (e.g. "Foundation Work", "Concrete Material")
     Do NOT include the quantity spec in the description.
   - quantity: numeric count from the Quantity column
     ("10 Days" → 10,  "5 Weeks" → 5,  "200 Cubics" → 200,  "10 Tons" → 10)
   - unit_price: numeric price from the Item Price / Unit Price column
   - total: numeric total from the Total / Amount column
9. SUBTOTAL / TAX / TOTAL: These appear in the summary box (usually bottom-right).
   Read them carefully — do not confuse subtotal with total.
10. confidence: 0.95+ if all major fields clearly visible; 0.80–0.94 if a few ambiguous;
    below 0.80 only if image quality is poor or fields are obscured.

RESPONSE SCHEMA (return exactly this structure):
{
  "vendor_name": string | null,
  "invoice_number": string | null,
  "invoice_date": "YYYY-MM-DD" | null,
  "due_date": "YYYY-MM-DD" | null,
  "currency": "USD" | "EUR" | "GBP" | "AUD" | "PKR" | null,
  "subtotal": number | null,
  "tax_amount": number | null,
  "total_amount": number | null,
  "line_items": [
    {"description": string, "quantity": number, "unit_price": number, "total": number}
  ],
  "confidence": number
}
"""

CATEGORIZATION_SYSTEM_PROMPT = """\
You are an expense categorization expert. Given invoice details, assign the
single most appropriate category. Return ONLY a JSON object — no explanation.

CATEGORY DEFINITIONS (use exact string values):
- "utilities"       — electricity, water, internet, gas, phone/telecom bills
- "marketing"       — ads, PR agencies, design agencies, promotions, events
- "travel"          — flights, hotels, car rental, taxi, accommodation, per diem
- "office_supplies" — paper, pens, printer consumables, cleaning supplies, stationery
- "equipment"       — hardware, machinery, tools, vehicles, furniture, construction
                      materials, structural work, building services
- "software"        — SaaS subscriptions, software licenses, cloud services (AWS, GCP, Azure)
- "other"           — professional services, legal, accounting, or anything above doesn't fit

CATEGORIZATION HINTS:
- Construction work, building materials, structural installations → "equipment"
- "Cloud", "SaaS", "license", "subscription" in vendor/text → "software"
- Hotel, airline, car rental vendor → "travel"
- Office consumables, cleaning → "office_supplies"

RESPONSE SCHEMA:
{
  "category": "utilities" | "marketing" | "travel" | "office_supplies" | "equipment" | "software" | "other",
  "reasoning": string,
  "confidence": number
}
"""

SUMMARY_SYSTEM_PROMPT = """\
You are a financial analyst assistant. Given aggregated expense data for a
calendar month, write a concise, insightful narrative summary (3–5 sentences).

Focus on:
- Total spending and whether it seems high, normal, or low
- The dominant expense category and what it suggests
- Any interesting vendor patterns
- One specific, actionable recommendation

Return ONLY a JSON object:
{
  "narrative": string
}
"""

ANOMALY_SYSTEM_PROMPT = """\
You are a financial anomaly detection expert. Given an invoice and historical
spending statistics, determine whether the invoice is anomalous.

Return ONLY a JSON object:
{
  "is_anomaly": boolean,
  "reason": string | null,
  "severity": "low" | "medium" | "high" | null
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

_RETRY = retry(
    retry=retry_if_exception_type((RateLimitError, APIError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


class AIExtractionClient:
    """
    Thin wrapper around the OpenAI async client.
    One instance per application (injected via DI).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._max_tokens = settings.openai_max_tokens
        self._temperature = settings.openai_temperature

    @_RETRY
    async def _chat(
        self,
        system_prompt: str,
        user_content: str,
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Text-only chat: call the API, return parsed JSON."""
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                temperature=self._temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except RateLimitError as exc:
            logger.warning("openai_rate_limited", error=str(exc))
            raise
        except APIError as exc:
            logger.error("openai_api_error", error=str(exc))
            raise AIProviderError(f"OpenAI API error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"LLM returned invalid JSON: {exc}") from exc

    @_RETRY
    async def _chat_vision(
        self,
        system_prompt: str,
        image_path: Path,
        supplemental_text: str | None = None,
    ) -> dict[str, Any]:
        """
        Vision chat: encode the invoice image as base64 and send to GPT-4o.
        Optionally includes OCR text as supplemental context.
        """
        import base64

        _MEDIA_TYPES: dict[str, str] = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
            ".webp": "image/webp",
        }
        media_type = _MEDIA_TYPES.get(image_path.suffix.lower(), "image/jpeg")

        with open(image_path, "rb") as fh:
            image_b64 = base64.b64encode(fh.read()).decode()

        user_content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_b64}",
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": "Extract all invoice fields from this invoice image. Return valid JSON only.",
            },
        ]

        if supplemental_text and supplemental_text.strip():
            user_content.append({
                "type": "text",
                "text": (
                    f"Additional OCR text to cross-reference (may be incomplete):\n"
                    f"{supplemental_text[:2000]}"
                ),
            })

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except RateLimitError as exc:
            logger.warning("openai_rate_limited_vision", error=str(exc))
            raise
        except APIError as exc:
            logger.error("openai_api_error_vision", error=str(exc))
            raise AIProviderError(f"OpenAI Vision API error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"LLM returned invalid JSON: {exc}") from exc

    # ── Public API ─────────────────────────────────────────────────────────

    async def extract_invoice_data(
        self,
        raw_text: str,
        *,
        file_path: Path | None = None,
        mime_type: str | None = None,
    ) -> ExtractedInvoiceData:
        """
        Core extraction: invoice → structured fields.

        For image files (JPEG, PNG, TIFF): uses GPT-4o Vision with the raw
        image — far more accurate than relying solely on OCR text.
        For PDFs and text-only inputs: uses the text extraction path.
        OCR text is always provided as supplemental context for cross-reference.
        """
        use_vision = (
            file_path is not None
            and file_path.exists()
            and mime_type is not None
            and mime_type.lower() in _IMAGE_MIME_TYPES
        )

        if use_vision:
            logger.info(
                "ai_extraction_start_vision",
                file=file_path.name,
                ocr_chars=len(raw_text),
            )
            data = await self._chat_vision(
                EXTRACTION_SYSTEM_PROMPT_VISION,
                file_path,  # type: ignore[arg-type]
                supplemental_text=raw_text,
            )
        else:
            truncated = raw_text[:6000]
            logger.info("ai_extraction_start", text_length=len(truncated))
            data = await self._chat(
                EXTRACTION_SYSTEM_PROMPT,
                f"Extract all invoice fields from the following text:\n\n{truncated}",
            )

        logger.info("ai_extraction_complete", confidence=data.get("confidence"))
        return self._parse_extraction_response(data, raw_text)

    async def categorize_expense(
        self,
        vendor_name: str | None,
        invoice_number: str | None,
        total_amount: Decimal | None,
        raw_text: str,
    ) -> tuple[ExpenseCategory, float]:
        """Returns (category, confidence)."""
        summary = (
            f"Vendor: {vendor_name or 'Unknown'}\n"
            f"Invoice #: {invoice_number or 'Unknown'}\n"
            f"Amount: {total_amount or 'Unknown'}\n"
            f"Invoice text excerpt: {raw_text[:1000]}"
        )
        data = await self._chat(CATEGORIZATION_SYSTEM_PROMPT, summary, max_tokens=256)
        category_str = data.get("category", "other").lower()
        try:
            category = ExpenseCategory(category_str)
        except ValueError:
            category = ExpenseCategory.OTHER
        confidence = float(data.get("confidence", 0.5))
        return category, confidence

    async def generate_monthly_narrative(self, stats: dict) -> str:
        """Returns AI-generated narrative string."""
        data = await self._chat(
            SUMMARY_SYSTEM_PROMPT,
            json.dumps(stats, default=str),
            max_tokens=512,
        )
        return data.get("narrative", "No summary available.")

    async def assess_anomaly(self, invoice_summary: dict, historical_stats: dict) -> dict:
        """Returns raw anomaly assessment dict."""
        user_msg = (
            f"Invoice: {json.dumps(invoice_summary, default=str)}\n"
            f"Historical stats for this vendor/category: {json.dumps(historical_stats, default=str)}"
        )
        return await self._chat(ANOMALY_SYSTEM_PROMPT, user_msg, max_tokens=256)

    # ── Parsing helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_extraction_response(data: dict, raw_text: str) -> ExtractedInvoiceData:
        from datetime import date

        def to_decimal(v: Any) -> Decimal | None:
            if v is None:
                return None
            try:
                return Decimal(str(v))
            except InvalidOperation:
                return None

        def to_date(v: Any) -> date | None:
            if not v:
                return None
            try:
                return date.fromisoformat(str(v))
            except ValueError:
                return None

        def to_currency(v: Any) -> Currency | None:
            if not v:
                return None
            try:
                return Currency(str(v).upper())
            except ValueError:
                return None

        def clean_line_items(items: Any) -> list[dict]:
            if not isinstance(items, list):
                return []
            cleaned = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                cleaned.append({
                    "description": str(item.get("description") or ""),
                    "quantity": float(item.get("quantity") or 1),
                    "unit_price": str(item.get("unit_price") or "0"),
                    "total": str(item.get("total") or "0"),
                })
            return cleaned

        return ExtractedInvoiceData(
            vendor_name=data.get("vendor_name"),
            invoice_number=data.get("invoice_number"),
            invoice_date=to_date(data.get("invoice_date")),
            due_date=to_date(data.get("due_date")),
            currency=to_currency(data.get("currency")),
            subtotal=to_decimal(data.get("subtotal")),
            tax_amount=to_decimal(data.get("tax_amount")),
            total_amount=to_decimal(data.get("total_amount")),
            line_items=clean_line_items(data.get("line_items")),
            raw_text=raw_text,
            confidence=float(data.get("confidence", 0.5)),
        )
