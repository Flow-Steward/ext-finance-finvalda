"""Checking workflow inputs: text, numbers, dates, flags, names. No Finvalda calls."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date as Date
from decimal import Decimal
from typing import Any

from .errors import invalid
from .values import finvalda_datetime, plain, whole_of

_DIGITS = re.compile(r"-?[0-9]{1,10}")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A moment a workflow gives (since): Finvalda's forms, or ISO 8601 with an offset.
_DATE_TIME_RE = re.compile(
    r"^\d{4}[-.]\d{2}[-.]\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})?)?$"
)
#: A date or time sent to Finvalda (fields, filters): its own forms, without an offset.
_FINVALDA_DATE_TIME_RE = re.compile(
    r"^\d{4}[-.]\d{2}[-.]\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?$"
)

_COLUMN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")

_NUMBER_RE = re.compile(r"^-?\d{1,15}(\.\d{1,8})?$")

_QUERY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")

_FILE_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,200}$")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def text_input(name: str, value: Any, *, limit: int = 200, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise invalid(f"Required input missing: {name}")
        return ""
    if not isinstance(value, str):
        raise invalid(f"{name} must be text")
    text = value.strip()
    if len(text) > limit or not text.isprintable():
        raise invalid(f"{name} must be printable text of at most {limit} characters")
    if required and not text:
        raise invalid(f"Required input missing: {name}")
    return text


def whole_input(
    name: str, value: Any, *, default: int | None = None, minimum: int, maximum: int
) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if isinstance(value, Decimal):
        whole = whole_of(value)
        if whole is not None:
            value = whole
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise invalid(f"{name} must be a whole number from {minimum} to {maximum}")
    return value


def flag_input(name: str, value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise invalid(f"{name} must be true or false")
    return value


_ISO_TIME_TAIL = re.compile(r"^T\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})?$")


def date_input(name: str, value: Any, *, required: bool = False) -> str:
    """YYYY-MM-DD, also taken from an ISO 8601 time (its date in Lithuania)."""
    text = text_input(name, value, limit=35, required=required)
    if not text:
        return ""
    if len(text) > 10 and _ISO_TIME_TAIL.fullmatch(text[10:]):
        # A time with an offset is first moved to Lithuanian time: 2024-03-31T22:30Z
        # is already 1 April in Vilnius. A time without one is Lithuanian already.
        moment = finvalda_datetime(text)
        if moment is None:
            raise invalid(f"{name} is not a real date and time")
        text = moment.date().isoformat()
    if not _DATE_RE.fullmatch(text):
        raise invalid(f"{name} must be a date, YYYY-MM-DD (or an ISO 8601 time)")
    try:
        Date.fromisoformat(text)
    except ValueError as exc:
        raise invalid(f"{name} is not a real date") from exc
    return text


def decimal_input(
    name: str, value: Any, *, minimum: Decimal | None = None, places: int = 4
) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise invalid(f"{name} must be a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise invalid(f"{name} must be a finite number")
        value = repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise invalid(f"{name} must be a finite number")
        value = plain(value)
    text = str(value).strip().replace(" ", "")
    if not _NUMBER_RE.fullmatch(text):
        raise invalid(f"{name} must be a number with '.' as the decimal point")
    number = Decimal(text)
    if minimum is not None and number < minimum:
        raise invalid(f"{name} must be at least {minimum}")
    if -number.as_tuple().exponent > places:  # type: ignore[operator]
        # 10.000 is 10.00: trailing zeros are dropped without loss; a real
        # fraction below the last place (10.005) is refused, never rounded.
        shorter = number.quantize(Decimal(1).scaleb(-places))
        if shorter != number:
            raise invalid(f"{name} may have at most {places} decimal places")
        number = shorter
    return number


def allowed(inputs: Mapping[str, Any], names: set[str] | frozenset[str]) -> None:
    unknown = set(inputs) - set(names) - {"connection_ref"}
    if unknown:
        raise invalid(f"Unknown input: {', '.join(sorted(unknown))}")


def file_name_input(name: str, value: Any) -> str:
    text = text_input(name, value, limit=200, required=True)
    if not _FILE_NAME_RE.fullmatch(text) or text in {".", ".."}:
        raise invalid(f"{name} must be a plain file name with its extension, for example S-1.pdf")
    return text
