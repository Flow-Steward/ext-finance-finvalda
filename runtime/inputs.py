"""Checking workflow inputs: text, numbers, dates, flags, names. No Finvalda calls."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date as Date
from decimal import Decimal
from typing import Any

from .errors import invalid

_DIGITS = re.compile(r"-?[0-9]{1,10}")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DATE_TIME_RE = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?$")

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
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise invalid(f"{name} must be a whole number from {minimum} to {maximum}")
    return value


def flag_input(name: str, value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise invalid(f"{name} must be true or false")
    return value


def date_input(name: str, value: Any, *, required: bool = False) -> str:
    text = text_input(name, value, limit=10, required=required)
    if not text:
        return ""
    if not _DATE_RE.fullmatch(text):
        raise invalid(f"{name} must be a date, YYYY-MM-DD")
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
        value = format(value, "f")
    text = str(value).strip().replace(" ", "")
    if not _NUMBER_RE.fullmatch(text):
        raise invalid(f"{name} must be a number with '.' as the decimal point")
    number = Decimal(text)
    if minimum is not None and number < minimum:
        raise invalid(f"{name} must be at least {minimum}")
    if -number.as_tuple().exponent > places:  # type: ignore[operator]
        raise invalid(f"{name} may have at most {places} decimal places")
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
