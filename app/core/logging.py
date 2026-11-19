"""structlog setup: JSON lines in production, readable output in development.

One event per line, key-value fields, nothing formatted into the message (see CLAUDE.md, Logging).
`LOG_JSON=true` turns on JSON, which is what the Oracle VM runs, so `docker compose logs` output
can be read by tools. The processors add the level and a UTC timestamp; they never add request
data, so no secret or personal field appears unless a call site logs it.
"""

import logging

import structlog


def configure_logging(json_logs: bool) -> None:
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
