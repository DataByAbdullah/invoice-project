"""
Structured logging via structlog.

Produces JSON logs in production (machine-parseable, ingested by Datadog /
CloudWatch / Loki), and human-readable coloured output in development.
Every log entry automatically carries: timestamp, level, service name,
request_id (when set via contextvars), and any extra key=value pairs passed
by the caller.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

from app.core.settings import get_settings

# Context variable injected per-request by middleware
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_request_id(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    rid = request_id_ctx.get()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def _add_service_info(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    settings = get_settings()
    event_dict["service"] = settings.app_name
    event_dict["version"] = settings.app_version
    event_dict["env"] = settings.app_env
    return event_dict


def configure_logging() -> None:
    settings = get_settings()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        _add_service_info,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_production:
        # JSON — structured for log aggregation
        renderer = structlog.processors.JSONRenderer()
    else:
        # Human-readable with colours
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge standard library logging into structlog so SQLAlchemy / uvicorn
    # logs also appear in the same structured format.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level),
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)
