"""
OCR abstraction layer — pluggable provider pattern.

Adding a new OCR backend (e.g. AWS Textract) requires only:
  1. A new class implementing AbstractOCRProvider
  2. A mapping entry in OCRProviderFactory

Services depend on AbstractOCRProvider — they never import Tesseract directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.domain.exceptions import OCRError

logger = get_logger(__name__)


class AbstractOCRProvider(ABC):
    """Contract for all OCR backends."""

    @abstractmethod
    async def extract_text(self, file_path: Path) -> str:
        """
        Extract raw text from a PDF or image file.
        Returns empty string if no text found (not an error).
        Raises OCRError on provider failure.
        """
        ...


class TesseractOCRProvider(AbstractOCRProvider):
    """
    Local Tesseract-based provider. Runs offline, good enough for clean scans.
    Images are preprocessed (upscale, contrast, binarize) before OCR to
    maximise character recognition accuracy.
    """

    # PSM modes to try in order; result with most characters wins.
    _PSM_MODES = (6, 4, 3)

    def __init__(self) -> None:
        settings = get_settings()
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    async def extract_text(self, file_path: Path) -> str:
        try:
            suffix = file_path.suffix.lower()
            if suffix == ".pdf":
                return await self._extract_from_pdf(file_path)
            else:
                return await self._extract_from_image(file_path)
        except OCRError:
            raise
        except Exception as exc:
            logger.error("ocr_failed", path=str(file_path), error=str(exc))
            raise OCRError(f"Tesseract OCR failed: {exc}") from exc

    async def _extract_from_pdf(self, path: Path) -> str:
        """
        Strategy: try pdfplumber for text-based PDFs first (fast, accurate).
        Fall back to pdf2image → Tesseract for scanned PDFs.
        """
        import pdfplumber

        try:
            with pdfplumber.open(str(path)) as pdf:
                pages_text = [p.extract_text() or "" for p in pdf.pages]
                text = "\n\n".join(pages_text).strip()
                if text:
                    logger.debug("pdf_text_extracted_native", chars=len(text))
                    return text
        except ImportError:
            logger.warning("pdfplumber_not_available_falling_back_to_ocr")
        except Exception as exc:
            logger.warning("pdfplumber_failed", error=str(exc))

        return await self._rasterize_and_ocr(path)

    async def _rasterize_and_ocr(self, path: Path) -> str:
        from pdf2image import convert_from_path  # type: ignore[import]

        images = convert_from_path(str(path), dpi=300)
        pages: list[str] = []
        for img in images:
            preprocessed = self._preprocess_image(img)
            text = self._ocr_best_of_psm_modes(preprocessed)
            pages.append(text)
        return "\n\n".join(pages).strip()

    async def _extract_from_image(self, path: Path) -> str:
        img = Image.open(path)
        preprocessed = self._preprocess_image(img)
        return self._ocr_best_of_psm_modes(preprocessed)

    # ── Image preprocessing ────────────────────────────────────────────────

    @staticmethod
    def _preprocess_image(img: Image.Image) -> Image.Image:
        """
        Pipeline: flatten alpha → upscale → grayscale → contrast/sharpen → binarize.
        Tesseract accuracy degrades sharply below ~150 DPI effective resolution and
        with low-contrast or colour images. This pipeline handles both.
        """
        # Flatten alpha channel onto white background
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Upscale: ensure longest side ≥ 2400 px (≈ 300 DPI for A4)
        w, h = img.size
        longest = max(w, h)
        if longest < 2400:
            scale = 2400 / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Convert to grayscale
        img = img.convert("L")

        # Boost contrast to separate text from background
        img = ImageEnhance.Contrast(img).enhance(2.0)

        # Sharpen twice — first pass recovers JPEG blur, second crisp edges
        img = img.filter(ImageFilter.SHARPEN)
        img = img.filter(ImageFilter.SHARPEN)

        # Otsu binarization: converts grayscale to pure black/white.
        # This eliminates background gradients and watermarks that confuse Tesseract.
        arr = np.array(img, dtype=np.uint8)
        threshold = TesseractOCRProvider._otsu_threshold(arr)
        arr = np.where(arr > threshold, 255, 0).astype(np.uint8)
        return Image.fromarray(arr)

    @staticmethod
    def _otsu_threshold(arr: np.ndarray) -> int:
        """
        Compute Otsu's optimal binarization threshold using inter-class variance.
        Pure numpy implementation — no scipy/scikit-image dependency needed.
        """
        hist, _ = np.histogram(arr.flatten(), bins=256, range=(0, 255))
        hist = hist.astype(float)
        total = int(arr.size)

        sum_all = float(np.dot(np.arange(256), hist))
        sum_bg = 0.0
        weight_bg = 0.0
        max_var = 0.0
        threshold = 128  # safe default

        for t in range(256):
            weight_bg += hist[t]
            if weight_bg == 0.0:
                continue
            weight_fg = total - weight_bg
            if weight_fg == 0.0:
                break
            sum_bg += t * hist[t]
            mean_bg = sum_bg / weight_bg
            mean_fg = (sum_all - sum_bg) / weight_fg
            var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
            if var > max_var:
                max_var = var
                threshold = t

        return threshold

    def _ocr_best_of_psm_modes(self, img: Image.Image) -> str:
        """
        Run Tesseract with several PSM (page segmentation mode) settings and
        return the result with the most extracted characters.
        PSM 6 = uniform block of text (default for most invoices)
        PSM 4 = single column of variable-size text
        PSM 3 = fully automatic page segmentation
        """
        best = ""
        for psm in self._PSM_MODES:
            cfg = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
            try:
                text = pytesseract.image_to_string(img, lang="eng", config=cfg).strip()
                if len(text) > len(best):
                    best = text
            except Exception as exc:
                logger.debug("tesseract_psm_failed", psm=psm, error=str(exc))
        return best


class OCRProviderFactory:
    """Returns the configured OCR provider singleton."""

    _providers: dict[str, AbstractOCRProvider] = {}

    @classmethod
    def get_provider(cls) -> AbstractOCRProvider:
        settings = get_settings()
        key = settings.ocr_provider
        if key not in cls._providers:
            match key:
                case "tesseract":
                    cls._providers[key] = TesseractOCRProvider()
                case _:
                    raise ValueError(f"Unsupported OCR provider: {key}")
        return cls._providers[key]
