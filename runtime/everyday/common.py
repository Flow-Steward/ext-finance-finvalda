"""What the everyday steps share: input helpers, lookups, the progress of a composite write."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from .. import budget, errors
from ..advanced.reads import read_description_pages
from ..advanced.writes import item_write_body
from ..errors import ExtensionError, invalid
from ..fields import field
from ..inputs import decimal_input, text_input
from ..reference_data import DESCRIPTION_TYPES
from ..rows import rows_of
from ..transport import DeadlineReached
from ..shapes import warn
from ..values import decimal_of, lithuanian_today, safe_name

MAX_AMOUNT = Decimal("9999999999.99")

MAX_LINES = 200

#: Finvalda's manual (3.66): a record's code is made of capital letters and digits.
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]*$")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")

CLIENT_COLUMNS = [
    "code",
    "title",
    "company_code",
    "vat_payer_code",
    "email",
    "address",
    "city",
    "country",
    "phone",
    "type",
    "currency",
    "debt",
    "date_edited",
]

PRODUCT_COLUMNS = [
    "product",
    "title",
    "measure_unit",
    "barcode",
    "type",
    "sell_price1",
    "sell_currency",
    "purchase_price",
    "vat_percent",
    "date_edited",
]

SERVICE_COLUMNS = [
    "code",
    "title",
    "measure_unit",
    "sell_price",
    "purchase_price",
    "vat_percent",
    "date_edited",
]

OPERATION_COLUMNS = [
    "journal",
    "op_number",
    "series",
    "order_number",
    "client",
    "client_title",
    "op_date",
    "payment_date",
    "currency",
    "amount",
    "initial_debt",
    "date_created",
    "date_edited",
]


class Progress:
    """The writes a composite step has completed, for an honest failure message."""

    def __init__(self) -> None:
        self.done: list[str] = []

    def add(self, item: str) -> None:
        self.done.append(item)

    def fail(self, exc: ExtensionError, step: str) -> ExtensionError:
        if not self.done:
            return exc
        message = (
            f"{step} failed: {exc.message}. Already saved in Finvalda: {'; '.join(self.done)}. "
            "Check Finvalda before running this step again."
        )
        return ExtensionError(
            exc.code,
            message,
            failure_class="provider",
            retryable=False,
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
            http_status=exc.http_status,
            finvalda_code=exc.finvalda_code,
        )

    def guard(self, step: str, run: Callable[[], Any]) -> Any:
        try:
            return run()
        except ExtensionError as exc:
            raise self.fail(exc, step) from exc
        except DeadlineReached as exc:
            if not self.done:
                raise
            raise self.fail(
                ExtensionError(errors.TIMEOUT_UNKNOWN, "the step ran out of time"), step
            ) from exc


def text(name: str, inputs: Mapping[str, Any], limit: int, *, required: bool = False) -> str:
    return text_input(name, inputs.get(name), limit=limit, required=required)


def code_input(name: str, inputs: Mapping[str, Any], limit: int, *, required: bool = False) -> str:
    value = text(name, inputs, limit, required=required)
    if value and not _CODE_RE.fullmatch(value):
        raise invalid(f"{name} must be capital letters and digits (Finvalda's rule for codes)")
    return value


def currency_input(inputs: Mapping[str, Any]) -> str:
    value = text("currency", inputs, 3).upper() or "EUR"
    if not _CURRENCY_RE.fullmatch(value):
        raise invalid("currency must be a three-letter code such as EUR")
    return value


def money(name: str, value: Any, *, minimum: Decimal = Decimal(0), places: int = 4) -> Decimal:
    """An amount; ``places=2`` for a sum of money, which is never rounded silently."""
    number = decimal_input(name, value, minimum=minimum, places=places)
    if number > MAX_AMOUNT:
        raise invalid(f"{name} is too large")
    return number


def today() -> str:
    """Today in Lithuania (Finvalda's dates are Lithuanian), not in the process's zone."""
    return lithuanian_today().isoformat()


#: What identifies a row of a list: each entry is a set of columns of which one
#: must hold a value. A row without it cannot be told apart from "no such record".
_IDENTITY: dict[str, tuple[tuple[str, ...], ...]] = {
    "Clients": (("code",),),
    "Products": (("product", "code"),),
    "Services": (("code",),),
    "Prices": (("code",),),
}
#: Operations are told apart by journal and number.
_OPERATION_IDENTITY: tuple[tuple[str, ...], ...] = (("journal",), ("op_number",))


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def row_code(row: Mapping[str, Any], kind: str = "Clients") -> str:
    """The code of a row, from the same columns its identity is checked in."""
    for columns in _IDENTITY.get(kind, (("code",),)):
        for column in columns:
            value = field(row, column)
            if not _blank(value):
                return str(value).strip()
    return ""


def checked_rows(
    rows: Any, what: str, required: tuple[tuple[str, ...], ...] = (), *, strict: bool = True
) -> list[dict[str, Any]]:
    """Every row an object with its identifying values (``required``: one column of
    each group, not blank).

    ``strict`` — for a lookup that decides whether a step writes: a row of
    unknown form (null, {}, a row without its code) is invalid_response, never
    skipped as "not found". Otherwise (a read) a row without its code is kept, with
    a warning; a row that is not a record is still an error.
    """
    out: list[dict[str, Any]] = []
    without: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise errors.no_effect(
                errors.INVALID_RESPONSE,
                f"Finvalda's {what} answer holds a row that is not a record",
            )
        missing = [
            " or ".join(group) for group in required if all(_blank(field(row, c)) for c in group)
        ]
        if missing and strict:
            raise errors.no_effect(
                errors.INVALID_RESPONSE,
                f"Finvalda's {what} answer holds a row without its {', '.join(missing)}",
            )
        if missing:
            key = ", ".join(missing)
            without[key] = without.get(key, 0) + 1
        out.append(dict(row))
    # One warning per call, so the rows cannot crowd out the step's other warnings.
    for key, count in without.items():
        warn(f"Finvalda's {what} answer holds {count} row(s) without its {key}")
    return out


def _with_identity(
    columns: list[str] | None, required: tuple[tuple[str, ...], ...]
) -> list[str] | None:
    """The columns asked for, with the identifying ones always among them."""
    if not columns:
        return columns
    # The first column of a group is the documented one (Products: product).
    extra = [group[0] for group in required if not any(c in columns for c in group)]
    return [*columns, *extra]


def read_list(
    context: Any,
    kind: str,
    conditions: Mapping[str, Any],
    columns: list[str] | None,
    *,
    limit: int,
    max_pages: int,
    meter: budget.Meter | None = None,
    strict: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    """Rows of a GetDescriptions type and whether more may follow.

    ``conditions`` belong to the type's first filter group; ``strict`` for a
    lookup whose result decides a write (``checked_rows``).
    """
    required = _IDENTITY.get(kind, ())
    columns = _with_identity(columns, required)
    groups = DESCRIPTION_TYPES[kind]["filters"]
    group = next(iter(groups))
    inputs: dict[str, Any] = {
        "filter": {group: dict(conditions)} if len(groups) > 1 else dict(conditions)
    }
    if columns:
        inputs["columns"] = columns
    read = read_description_pages(
        context, kind, groups, inputs, first_page=1, limit=limit, max_pages=max_pages, meter=meter
    )
    if read["timed_out"]:
        # Rows cut short by the clock are no answer: report the timeout, not "more".
        raise DeadlineReached
    rows = checked_rows(read["records"], kind, required, strict=strict)
    return rows, bool(read["has_more"])


def describe(
    context: Any,
    kind: str,
    conditions: Mapping[str, Any],
    columns: list[str] | None,
    *,
    limit: int = 100,
    max_pages: int = 5,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Every row a lookup matches; a lookup that matches more is an error, never cut short.

    ``strict`` (the default) for lookups that decide whether a step writes; reads
    pass False.
    """
    # One page more than allowed tells "exactly that many" from "more".
    rows, more = read_list(
        context,
        kind,
        conditions,
        columns,
        limit=limit,
        max_pages=max_pages + 1,
        strict=strict,
    )
    if more or len(rows) > limit * max_pages:
        raise errors.no_effect(
            errors.CONFLICT,
            f"More than {len(rows)} Finvalda {kind} rows match {dict(conditions)}; give a code",
        )
    return rows


def operations_of(
    context: Any,
    op_class: str,
    conditions: Mapping[str, Any],
    columns: list[str] | None = None,
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Operations of a class; ``strict`` (the default) for a lookup whose result
    decides a write, False for a read."""
    columns = _with_identity(columns, _OPERATION_IDENTITY)
    params: dict[str, Any] = {"OpClass": op_class, "fullOp": False, "filter": dict(conditions)}
    if columns:
        params["columns"] = {"column": columns}
    reply = context.call(
        "GetOperations", verb="POST", body={"opReadParams": params}, mutating=False
    )
    return checked_rows(rows_of(reply.data), op_class, _OPERATION_IDENTITY, strict=strict)


def _same(a: Any, b: Any) -> bool:
    return str(a or "").strip().upper() == str(b or "").strip().upper()


def client_code_of(context: Any, inputs: Mapping[str, Any]) -> str:
    """The client's code: given, or found by company code (exactly one client)."""
    code = text("client_code", inputs, 15)
    if code:
        return code
    company = text("client_company_code", inputs, 30)
    if not company:
        raise invalid("Give client_code, or client_company_code to look the client up")
    rows = [
        r
        for r in describe(context, "Clients", {"CompanyCode": company}, ["code", "company_code"])
        if _same(field(r, "company_code"), company)
    ]
    if not rows:
        raise errors.no_effect(errors.NOT_FOUND, f"No Finvalda client has company code {company}")
    if len(rows) > 1:
        raise errors.no_effect(
            errors.CONFLICT,
            f"{len(rows)} Finvalda clients have company code {company}; give client_code",
        )
    return row_code(rows[0])


def insert_item(context: Any, cls: str, fields: dict[str, Any], inputs: Mapping[str, Any]) -> None:
    item_inputs = {"item_class": cls, "fields": fields}
    if inputs.get("item_parameter"):
        item_inputs["item_parameter"] = inputs["item_parameter"]
    spec = {"method": "InsertNewItem", "classes": [cls]}
    _cls, body, _code = item_write_body(spec, item_inputs, context)
    context.call("InsertNewItem", verb="POST", body=body, mutating=True)


def _best_effort(read: Callable[[], Any], warnings: list[str], what: str) -> Any:
    """A read after a saved write: its failure must not turn the write into an error.

    It is never silent: the step's ``warnings`` say that the record was saved and
    why it could not be read back.
    """
    try:
        return read()
    except ExtensionError as exc:
        warnings.append(f"{what} was saved, but reading it back failed: {exc.code}")
    except DeadlineReached:
        warnings.append(f"{what} was saved, but the step ran out of time reading it back")
    return None


def same_document(
    context: Any, op_class: str, document: str, series: str = "", client: str = ""
) -> list[dict[str, Any]]:
    """Operations of ``op_class`` with this document number (and series, and client)."""
    conditions: dict[str, Any] = {"OrderNumber": document}
    if series:
        conditions["Series"] = series
    if client:
        conditions["Client"] = client
    rows = operations_of(context, op_class, conditions, OPERATION_COLUMNS, strict=True)
    rows = [r for r in rows if _same(field(r, "order_number"), document)]
    if series:
        rows = [r for r in rows if _same(field(r, "series"), series)]
    if client:
        # The "client" column holds the client's code ("client_code" is its company code).
        rows = [r for r in rows if _same(field(r, "client"), client)]
    return rows


def find_operation(
    context: Any,
    op_class: str,
    client: str,
    document: str,
    series: str = "",
    *,
    per_client: bool = False,
) -> dict[str, Any] | None:
    """The operation of this client with this document number, if there is one.

    Sales numbers are the company's own, so the same number for another client is a
    conflict. Purchase numbers are the supplier's: two suppliers may use the same one
    (``per_client``).
    """
    rows = same_document(context, op_class, document, series, client if per_client else "")
    if not rows:
        return None
    mine = [r for r in rows if _same(field(r, "client"), client)]
    if not mine:
        other = field(rows[0], "client")
        raise errors.no_effect(
            errors.CONFLICT,
            f"Document {document} already exists in Finvalda for client {other}, not {client}",
        )
    if len(mine) > 1:
        raise errors.no_effect(
            errors.CONFLICT, f"{len(mine)} operations of client {client} have document {document}"
        )
    return dict(mine[0])


def own_file_name(kind: str, client: str, series: str, document: str) -> str:
    """The register name of an invoice step's file: readable, and one per document.

    The register's names are global and the step later trusts a stored file of this
    name as its own, so the name must belong to this document alone: the parts are
    kept apart by "-" and a hash of the exact values follows, so "A1"+"23" and
    "A"+"123", or "AB/12" and "AB 12", never meet.
    """
    digest = hashlib.sha256("\x1f".join((kind, client, series, document)).encode()).hexdigest()
    readable = "-".join(safe_name(part) for part in (kind, client, series, document) if part)
    name = f"{readable}-{digest[:12]}.pdf"
    if len(name) > 200:
        raise invalid("The client code, series and document are too long for a file name")
    return name


def operation_identity(record: Mapping[str, Any], document: str) -> dict[str, Any]:
    """Journal, number, series and document of an operation read from Finvalda."""
    return {
        "journal": errors.safe_text(field(record, "journal"), 20) or None,
        "op_number": _op_number(record),
        "series": errors.safe_text(field(record, "series"), 20) or None,
        "document": errors.safe_text(field(record, "order_number"), 40) or document,
    }


def _op_number(record: Mapping[str, Any]) -> int | None:
    """The operation's number when it is a whole number; never truncated (12.5 is None)."""
    value = field(record, "op_number")
    number = decimal_of(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def the_row(rows: list[dict[str, Any]] | None, code: str, kind: str) -> dict[str, Any] | None:
    """The one row of ``rows`` whose code is ``code``; None when there are no rows.

    Rows of other codes, or several of this code, are invalid_response: the step
    never returns another record under the code asked for.
    """
    if not rows:
        return None
    matches = [r for r in rows if _same(row_code(r, kind), code)]
    if len(matches) != 1:
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"Finvalda's {kind} answer for {code} holds {len(matches)} rows of that code "
            f"among {len(rows)}",
        )
    return matches[0]


def _codes(inputs: Mapping[str, Any], name: str, *, required: bool, most: int = 500) -> list[str]:
    raw = inputs.get(name)
    if raw is None:
        if required:
            raise invalid(f"Required input missing: {name}")
        return []
    if not isinstance(raw, list) or len(raw) > most or (required and not raw):
        raise invalid(f"{name} must be a list of 1 to {most} codes")
    return [text_input(name, v, limit=20, required=True) for v in raw]
