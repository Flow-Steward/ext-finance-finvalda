"""Fields of records and operations, checked against the manual's tables."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .errors import invalid
from .inputs import _COLUMN_RE, _DATE_TIME_RE, decimal_input, text_input, whole_input
from .limits import MAX_LIST_ITEMS, MAX_TEXT
from .reference_data import CLASSES

#: "Numeric(14,2)": at most 14 digits, 2 of them after the point.
_NUMERIC_SIZE = re.compile(r"^numeric\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")


def _field_value(cls: str, spec: Mapping[str, Any], value: Any) -> Any:
    name = spec["name"]
    kind = str(spec.get("type") or "").lower()
    where = f"{cls}.{name}"
    if isinstance(value, (dict, list)):
        raise invalid(f"{where} must be a single value")
    if kind.startswith("string") or kind == "char":
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise invalid(f"{where} must be text")
        text = str(value)
        length = spec.get("length")
        if (isinstance(length, int) and len(text) > length) or len(text) > MAX_TEXT:
            raise invalid(
                f"{where} may be at most {length or MAX_TEXT} characters (Finvalda's limit)"
            )
        if any(ch in text for ch in "\x00\r") or not text.replace("\n", " ").isprintable():
            raise invalid(f"{where} contains characters that are not allowed")
        return text
    if kind in {"long", "int", "integer"}:
        return whole_input(where, value, minimum=-2_147_483_648, maximum=2_147_483_647)
    if kind.startswith(("double", "numeric", "decimal")):
        size = _NUMERIC_SIZE.match(kind)
        if size is None:
            return str(decimal_input(where, value, places=8))
        precision, scale = int(size.group(1)), int(size.group(2))
        number = decimal_input(where, value, places=scale)
        if abs(number) >= Decimal(10) ** (precision - scale):
            raise invalid(
                f"{where} must be below {Decimal(10) ** (precision - scale)} "
                f"(Finvalda's {spec.get('type')})"
            )
        return str(number)
    if kind.startswith("datetime") or kind == "date":
        text = text_input(where, value, limit=27)
        if not _DATE_TIME_RE.fullmatch(text):
            raise invalid(f"{where} must be a date, YYYY-MM-DD")
        return text
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(decimal_input(where, value, places=8))
    if isinstance(value, (int, float, str)):
        return value
    raise invalid(f"{where} has a value of the wrong kind")


def check_fields(
    cls: str,
    fields: Any,
    *,
    lines: Mapping[str, Any] | None = None,
    enforce_required: bool = False,
    free_form: bool = False,
) -> dict[str, Any]:
    """``fields`` of ``cls`` by the manual's names, with nested line classes in ``lines``."""
    if not isinstance(fields, Mapping):
        raise invalid(f"{cls}: the fields must be an object")
    table = {f["name"]: f for f in CLASSES.get(cls, [])}
    lowered = {name.lower(): name for name in table}
    checked: dict[str, Any] = {}
    for raw_name, value in fields.items():
        name = str(raw_name)
        if lines is not None and name in lines:
            items = value if isinstance(value, list) else [value]
            if not items or len(items) > MAX_LIST_ITEMS:
                raise invalid(f"{cls}.{name}: give 1 to {MAX_LIST_ITEMS} lines")
            checked[name] = [check_fields(name, item, enforce_required=True) for item in items]
            continue
        if free_form:
            if not _COLUMN_RE.fullmatch(name):
                raise invalid(f"{cls}: {name} is not a field name")
            checked[name] = _field_value(
                cls, {"name": name, "type": "string", "length": 255}, value
            )
            continue
        canonical = name if name in table else lowered.get(name.lower())
        if canonical is None:
            known = list(table) + list(lines or {})
            raise invalid(
                f"{cls} has no field {name} in Finvalda's manual; its fields are: "
                + ", ".join(known[:80])
            )
        if value is None:
            continue
        checked[canonical] = _field_value(cls, table[canonical], value)
    if enforce_required:
        missing = [
            n
            for n, f in table.items()
            if f.get("required") and not f.get("from_parameter") and n not in checked
        ]
        if missing:
            raise invalid(f"{cls}: required field missing: {', '.join(missing)}")
    return checked
