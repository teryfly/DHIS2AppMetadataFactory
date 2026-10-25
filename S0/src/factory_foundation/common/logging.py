"""Structured JSON logging with context fields and redaction (FD-COM-006/007).

Every record is emitted as a single JSON object carrying the operation-context
fields (correlationId, operator, projectId, ...) and any supplied structured
data.  Sensitive values are masked before emission (FD-COM-007).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from factory_foundation.common.context import get_operation_context
from factory_foundation.common.redaction import redact_value

_CONTEXT_FIELDS = (
    "agentRunId",
    "artifactId",
    "executionId",
)


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        }
        ctx = get_operation_context()
        if ctx is not None:
            payload.update(ctx.as_log_fields())
        for field in _CONTEXT_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                payload[field] = val
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        redacted, _ = redact_value(payload)
        assert isinstance(redacted, dict)
        return json.dumps(redacted, ensure_ascii=False, default=str)


_configured = False


def configure_logging(level: int | str = logging.INFO, *, stream: Any = None) -> logging.Logger:
    """Install a single JSON handler on the root logger (idempotent)."""
    global _configured
    root = logging.getLogger()
    if not _configured:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(RedactingJsonFormatter())
        root.handlers[:] = [handler]
        _configured = True
    root.setLevel(
        level if isinstance(level, int) else getattr(logging, str(level).upper(), logging.INFO)
    )
    # Third-party chatter we don't want in structured logs.
    for noisy in (
        "alembic",
        "alembic.runtime.plugins",
        "sqlalchemy.engine",
        "urllib3",
        "boto3",
        "botocore",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def log_with(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a log record with structured ``extra_fields`` (already redacted)."""
    redacted, _ = redact_value(fields)
    logger.log(
        level, message, extra={"extra_fields": redacted if isinstance(redacted, dict) else {}}
    )
