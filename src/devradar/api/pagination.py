import base64
import json
from collections.abc import Callable
from datetime import datetime

from fastapi import HTTPException, status


def encode_cursor(*values: object) -> str:
    serializable = [v.isoformat() if isinstance(v, datetime) else v for v in values]
    return (
        base64.urlsafe_b64encode(json.dumps(serializable).encode()).decode().rstrip("=")
    )


def decode_cursor(cursor: str, *parsers: Callable[[object], object]) -> tuple:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != len(parsers):
            raise ValueError
        return tuple(parse(value) for parse, value in zip(parsers, values, strict=True))
    except (ValueError, TypeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from error


def aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def contains_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
