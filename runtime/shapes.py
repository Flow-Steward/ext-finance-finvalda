"""The everyday steps' records: English fields with fixed types, read from Finvalda's rows.

One table per kind of record: the output field, its kind, the Finvalda columns it
is read from (the first one present wins) and a short description. The runtime
normalises rows with it, and the contract generator builds the output schemas
from it, so the two cannot drift.

Kinds and how a value is written in a result:

* ``text`` — text, or null;
* ``decimal`` — an exact decimal as text ("12.50"), or null;
* ``integer`` — a whole number, or null;
* ``boolean`` — true or false (Finvalda's 1/0, "T"/"F", true/false), or null;
* ``date`` — YYYY-MM-DD, or null;
* ``datetime`` — ISO 8601 with the offset of Lithuanian time, or null.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .fields import field
from .values import decimal_of, finvalda_datetime, iso_date, iso_moment


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    columns: tuple[str, ...]
    description: str


def _f(name: str, kind: str, columns: str, description: str) -> Field:
    return Field(name, kind, tuple(columns.split()), description)


SHAPES: dict[str, tuple[Field, ...]] = {
    "client": (
        _f("code", "text", "code", "The client's code."),
        _f("name", "text", "title", "The client's name."),
        _f("company_code", "text", "company_code", "The company code."),
        _f("vat_code", "text", "vat_payer_code", "The VAT payer code."),
        _f("email", "text", "email", "The e-mail address."),
        _f("address", "text", "address", "The address."),
        _f("city", "text", "city", "The city."),
        _f("country", "text", "country", "The country code."),
        _f("phone", "text", "phone", "The phone number."),
        _f("type", "text", "type", "The client type code."),
        _f("currency", "text", "currency", "The currency offered in operations."),
        _f("debt", "decimal", "current_debt debt", "The client's current debt."),
        _f("edited_at", "datetime", "date_edited", "When the client was last edited."),
    ),
    "product": (
        _f("code", "text", "product code", "The product's code."),
        _f("name", "text", "title", "The product's name."),
        _f("unit", "text", "measure_unit", "The unit of measure."),
        _f("barcode", "text", "barcode", "The barcode."),
        _f("type", "text", "type", "The product type code."),
        _f("price", "decimal", "sell_price1", "The first sales price."),
        _f("currency", "text", "sell_currency", "The sales price currency."),
        _f("purchase_price", "decimal", "purchase_price", "The purchase price."),
        _f("vat_percent", "decimal", "vat_percent", "The VAT percent."),
        _f("edited_at", "datetime", "date_edited", "When the product was last edited."),
    ),
    "service": (
        _f("code", "text", "code", "The service's code."),
        _f("name", "text", "title", "The service's name."),
        _f("unit", "text", "measure_unit", "The unit of measure."),
        _f("price", "decimal", "sell_price", "The sales price."),
        _f("purchase_price", "decimal", "purchase_price", "The purchase price."),
        _f("vat_percent", "decimal", "vat_percent", "The VAT percent."),
        _f("edited_at", "datetime", "date_edited", "When the service was last edited."),
    ),
    "operation": (
        _f("journal", "text", "journal", "The operation's journal."),
        _f("op_number", "integer", "op_number", "Its number in the journal."),
        _f("series", "text", "series", "The document series."),
        _f("document", "text", "order_number", "The document number."),
        _f("client_code", "text", "client", "The client's code."),
        _f("client_name", "text", "client_title", "The client's name."),
        _f("date", "date", "op_date", "The operation date."),
        _f("payment_date", "date", "payment_date", "The payment date."),
        _f("currency", "text", "currency", "The currency."),
        _f("amount", "decimal", "amount", "The operation's amount as Finvalda shows it."),
        _f("created_at", "datetime", "date_created", "When the operation was created."),
        _f("edited_at", "datetime", "date_edited", "When the operation was last edited."),
    ),
    "stock": (
        _f("code", "text", "code", "The product's code."),
        _f("name", "text", "title", "The product's name."),
        _f("warehouse", "text", "warehouse", "The warehouse code."),
        _f("warehouse_name", "text", "warehouse_title", "The warehouse name."),
        _f("quantity", "decimal", "quantity", "The quantity in stock."),
        _f(
            "quantity_with_reserved",
            "decimal",
            "quantity_with_reserved",
            "The quantity less reservations.",
        ),
        _f("unit", "text", "measure_unit", "The unit of measure."),
        _f("cost", "decimal", "primary_cost", "The cost."),
    ),
    "product_price": (
        _f("code", "text", "product code", "The product's code."),
        _f("name", "text", "title", "The product's name."),
        _f("price1", "decimal", "sell_price1", "Sales price 1."),
        _f("price2", "decimal", "sell_price2", "Sales price 2."),
        _f("price3", "decimal", "sell_price3", "Sales price 3."),
        _f("price4", "decimal", "sell_price4", "Sales price 4."),
        _f("price5", "decimal", "sell_price5", "Sales price 5."),
        _f("price6", "decimal", "sell_price6", "Sales price 6."),
        _f("currency", "text", "sell_currency", "The sales price currency."),
        _f("purchase_price", "decimal", "purchase_price", "The purchase price."),
        _f("purchase_currency", "text", "purchase_currency", "The purchase price currency."),
        _f("vat_percent", "decimal", "vat_percent", "The VAT percent."),
    ),
    "client_price": (
        _f("client_code", "text", "client", "The client's code."),
        _f("code", "text", "code", "The product's or service's code."),
        _f("name", "text", "title", "Its name."),
        _f("price", "decimal", "price", "The client's price."),
        _f("currency", "text", "currency", "The price currency."),
        _f("discount_percent", "decimal", "discount_1", "The discount in percent."),
        _f("from_quantity", "decimal", "quantity_1", "The quantity the discount starts at."),
        _f("valid_from", "date", "term_from", "The first day of the price."),
        _f("valid_to", "date", "term_to", "The last day of the price."),
    ),
    "debt_document": (
        _f("document", "text", "dokumentas", "The document (series and number)."),
        _f("series", "text", "serija", "The document series."),
        _f("journal", "text", "zurnalas", "The operation's journal."),
        _f("op_number", "integer", "op_numeris", "Its number in the journal."),
        _f("date", "date", "dokumento_data", "The document date."),
        _f("due_date", "date", "apmokejimo_data", "The payment due date."),
        _f("amount", "decimal", "pradine_suma", "The document amount in EUR."),
        _f("remaining", "decimal", "likusi_skola", "What remains unpaid, in EUR."),
        _f("amount_currency", "decimal", "pradine_suma_val", "The amount in the currency."),
        _f(
            "remaining_currency",
            "decimal",
            "likusi_skola_val",
            "What remains unpaid, in the currency.",
        ),
        _f("currency", "text", "valiuta", "The currency."),
    ),
}

_TRUE = {"1", "t", "true", "taip", "y", "yes"}
_FALSE = {"0", "f", "false", "ne", "n", "no"}


def to_kind(kind: str, value: Any) -> Any:
    """A Finvalda value as the kind says, or None when it is missing or unreadable."""
    if value is None or value == "":
        return None
    if kind == "decimal":
        number = decimal_of(value)
        return None if number is None else format(number, "f")
    if kind == "integer":
        number = decimal_of(value)
        if number is None or number != number.to_integral_value():
            return None
        return int(number)
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return True if text in _TRUE else False if text in _FALSE else None
    if kind == "date":
        return iso_date(finvalda_datetime(value))
    if kind == "datetime":
        return iso_moment(finvalda_datetime(value))
    text = str(value).strip()
    return text or None


def shape(kind: str, row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``row`` as the record ``kind`` describes, every field present."""
    if row is None:
        return None
    out: dict[str, Any] = {}
    for item in SHAPES[kind]:
        value = None
        for column in item.columns:
            value = field(row, column)
            if value not in (None, ""):
                break
        out[item.name] = to_kind(item.kind, value)
    return out


_MOMENT_TEXT = re.compile(
    r"^(\d{4}[-.]\d{2}[-.]\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?|/Date\(-?\d+([+-]\d{4})?\)/)$"
)


def normalize_value(value: Any, kind: str | None = None) -> Any:
    """One value of a raw Finvalda row: by its documented kind, else by its form.

    Without a kind, only what is certain is changed: a date or time in one of
    Finvalda's forms becomes ISO 8601 (a date, or a time with Lithuania's offset).
    """
    if kind is not None:
        return to_kind(kind, value)
    if isinstance(value, str) and _MOMENT_TEXT.match(value.strip()):
        moment = finvalda_datetime(value)
        if moment is None:
            return value
        has_time = "T" in value or " " in value.strip() or value.startswith("/Date")
        return iso_moment(moment) if has_time else iso_date(moment)
    return value


def normalize_row(row: Any, kinds: Mapping[str, str] | None = None) -> Any:
    """A raw Finvalda row with its values normalised (nested rows too)."""
    if isinstance(row, Mapping):
        known = {k.lower(): v for k, v in (kinds or {}).items()}
        return {
            key: normalize_row(value)
            if isinstance(value, (Mapping, list))
            else normalize_value(value, known.get(str(key).lower()))
            for key, value in row.items()
        }
    if isinstance(row, list):
        return [normalize_row(item, kinds) for item in row]
    return normalize_value(row)


#: The manual's column types (Char (15), Double, Numeric, Integer, Date …) as kinds.
def manual_kind(manual_type: str) -> str:
    text = manual_type.strip().lower()
    if text.startswith(("char", "string", "varchar", "text", "memo")):
        return "text"
    if text.startswith(("double", "numeric", "decimal", "float", "money", "currency")):
        return "decimal"
    if text.startswith(("int", "long", "short", "smallint")):
        return "integer"
    if text.startswith("datetime"):
        return "datetime"
    if text.startswith("date"):
        return "date"
    if text.startswith(("bool", "bit")):
        return "boolean"
    return "text"


KIND_SCHEMAS: dict[str, dict[str, Any]] = {
    "text": {"type": ["string", "null"]},
    "decimal": {"type": ["string", "null"], "pattern": r"^-?\d+(\.\d+)?$"},
    "integer": {"type": ["integer", "null"]},
    "boolean": {"type": ["boolean", "null"]},
    "date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
    "datetime": {
        "type": ["string", "null"],
        "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$",
    },
}


def schema(kind: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON schema of a ``kind`` record (plus ``extra`` properties)."""
    properties = {
        item.name: {**KIND_SCHEMAS[item.kind], "description": item.description}
        for item in SHAPES[kind]
    }
    properties.update(extra or {})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


__all__ = [
    "KIND_SCHEMAS",
    "SHAPES",
    "Field",
    "manual_kind",
    "normalize_row",
    "normalize_value",
    "schema",
    "shape",
    "to_kind",
]
