"""
Celery task definitions — async invoice processing workers.

Architecture decision:
  The API returns 202 Accepted immediately after upload. Celery picks up the
  job from Redis and runs the full pipeline (OCR + AI) in the background.
  This keeps p99 upload latency under 200ms regardless of PDF complexity.

Worker scaling:
  - CPU-bound work (Tesseract OCR): use prefork pool (default)
  - I/O-bound work (OpenAI API): use gevent or eventlet pool
  - Deploy 2–4 workers per CPU core for the I/O-heavy profile
"""

from __future__ import annotations

import asyncio
import uuid

from celery import Celery
from celery.utils.log import get_task_logger

from app.core.settings import get_settings

settings = get_settings()
logger = get_task_logger(__name__)

celery_app = Celery(
    "invoice_ai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,                    # ack only after success (at-least-once)
    task_reject_on_worker_lost=True,        # requeue if worker dies mid-task
    task_max_retries=3,
    task_default_retry_delay=60,            # seconds
    worker_prefetch_multiplier=1,           # fair dispatch — don't pre-fetch
    result_expires=86400,                   # 24h
)


@celery_app.task(
    name="process_invoice",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=120,    # 2 min — signal SIGTERM
    time_limit=150,         # 2.5 min — SIGKILL
)
def process_invoice_task(self, invoice_id: str, user_id: str) -> dict:
    """
    Worker entry point. Runs the async service in a new event loop
    (Celery workers are synchronous by default).
    """
    async def _run() -> dict:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.infrastructure.ai import AIExtractionClient
        from app.infrastructure.ocr import OCRProviderFactory
        from app.infrastructure.persistence.repositories import SQLInvoiceRepository
        from app.services.invoice_service import InvoiceService

        # Create a fresh engine per task — avoids "Future attached to a different
        # loop" when Celery prefork inherits the parent process's asyncpg pool.
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        factory = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        try:
            async with factory() as session:
                repo = SQLInvoiceRepository(session)
                service = InvoiceService(
                    repository=repo,
                    ocr_provider=OCRProviderFactory.get_provider(),
                    ai_client=AIExtractionClient(),
                )
                invoice = await service.process_invoice(
                    uuid.UUID(invoice_id), uuid.UUID(user_id)
                )
                await session.commit()
                return {"invoice_id": str(invoice.id), "status": invoice.status.value}
        finally:
            await engine.dispose()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.error(f"process_invoice_task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
