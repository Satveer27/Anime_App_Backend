import hashlib
import html
from datetime import datetime
from decimal import Decimal, InvalidOperation


def clean_text(value) -> str | None:
    """A stripped string, or None for anything that is not text or is blank."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def clean_html_text(value) -> str | None:
    """`clean_text` for prose that may carry HTML entities (&rsquo; &quot; ...), decoded.

    Not for URLs: `html.unescape` also decodes entities written without a semicolon, so a
    query string like `?a=1&region=eu` would be corrupted.
    """
    if not isinstance(value, str):
        return None
    return clean_text(html.unescape(value))


def to_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_decimal(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def to_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
