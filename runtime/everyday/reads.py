"""Test the connection, Stock levels, Product prices, Client debt, Changes since."""

from __future__ import annotations

import json
from collections import Counter
from itertools import groupby
from collections.abc import Callable, Mapping
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..shapes import shape, warn
from .. import errors
from ..advanced.reads import read_description_pages
from ..errors import invalid
from ..fields import field
from ..inputs import date_input, whole_input
from ..reference_data import CHANGE_ENTITIES, DESCRIPTION_TYPES
from ..rows import rows_of
from ..values import as_decimal, cents, finvalda_date, finvalda_datetime, iso_moment
from .common import (
    row_code,
    CLIENT_COLUMNS,
    OPERATION_COLUMNS,
    PRODUCT_COLUMNS,
    SERVICE_COLUMNS,
    _codes,
    _op_number,
    _same,
    describe,
    operations_of,
    read_list,
    text,
    today,
)


def test_connection_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    started = context.clock()
    rows, _more = read_list(context, "Clients", {}, ["code"], limit=1, max_pages=1, strict=False)
    warnings = []
    if not context.connection.fin_user:
        warnings.append(
            "No Finvalda employee is set: file steps (documents, attachments, an invoice with "
            "a file) need one; the web service user is not used in its place"
        )
    if not context.connection.operation_parameter:
        warnings.append(
            "No operation import parameter is set: invoices and payments need one, on the "
            "connection or in the step"
        )
    return {
        "connected": True,
        "company_id": context.connection.company_id or None,
        "clients_visible": bool(rows),
        "duration_ms": int((context.clock() - started) * 1000),
        "warnings": warnings,
    }


def stock_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    codes = _codes(inputs, "product_codes", required=False)
    conditions: dict[str, Any] = {}
    if codes:
        conditions["Codes"] = codes
    for key, wire in (("warehouse", "Warehouse"), ("warehouse_group", "WarehouseGroup")):
        value = text(key, inputs, 10)
        if value:
            conditions[wire] = value
    # One Finvalda page per step: 500 rows of these 8 columns stay well inside the
    # step output's limits (about 9 000 of its 12 000 strings).
    limit = whole_input("limit", inputs.get("limit"), default=200, minimum=1, maximum=500) or 200
    page = whole_input("page", inputs.get("page"), default=1, minimum=1, maximum=1_000_000) or 1
    columns = [
        "warehouse",
        "warehouse_title",
        "code",
        "title",
        "quantity",
        "quantity_with_reserved",
        "measure_unit",
        "primary_cost",
    ]
    groups = DESCRIPTION_TYPES["CurrentStock"]["filters"]
    read = read_description_pages(
        context,
        "CurrentStock",
        groups,
        {"filter": conditions, "columns": columns},
        first_page=page,
        limit=limit,
        max_pages=1,
        meter=None,
    )
    items = [shape("stock", r) for r in read["records"] if isinstance(r, Mapping)]
    return {
        "items": items,
        "count": len(items),
        "has_more": read["has_more"],
        "next_page": read["next_page"],
    }


CLIENT_PRICE_COLUMNS = [
    "client",
    "code",
    "title",
    "type",
    "price",
    "currency",
    "discount_1",
    "quantity_1",
    "sum_1",
    "term_from",
    "term_to",
]


def prices_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    codes = _codes(inputs, "product_codes", required=True, most=200)
    columns = [
        "product",
        "title",
        *(f"sell_price{n}" for n in range(1, 7)),
        "sell_currency",
        "purchase_price",
        "purchase_currency",
        "vat_percent",
    ]
    wanted = {c.upper() for c in codes}
    found = [
        (row_code(r, "Products").upper(), r)
        for r in describe(context, "Products", {"Codes": codes}, columns, limit=500, strict=False)
    ]
    # A row without its code (warned by checked_rows) or of a product not asked for
    # is no price of these products: left out, with one warning. Two rows for one
    # code asked for cannot be told apart: an error.
    rows: list[dict[str, Any]] = []
    per_code: Counter[str] = Counter()
    other = 0
    for code, row in found:
        if code in wanted:
            rows.append(row)
            per_code[code] += 1
        elif code:
            other += 1
    if other:
        warn(f"Finvalda returned {other} row(s) of products not asked for; they are left out")
    doubled = sorted(c for c, n in per_code.items() if n > 1)
    if doubled:
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"Finvalda returned more than one row for product {', '.join(doubled[:5])}",
        )
    items = [shape("product_price", r) for r in rows]
    client_prices: list[Any] = []
    client = text("client_code", inputs, 15)
    if client:
        prices = [
            (row_code(r, "Prices").upper(), r)
            for r in describe(
                context,
                "Prices",
                {"Type": 1, "Client": client, "Codes": codes},
                CLIENT_PRICE_COLUMNS,
                limit=200,
                max_pages=5,
                strict=False,
            )
        ]
        # Only the prices of the codes asked for and of this client, by the row's own
        # client column: a row without it, or of another client, is no special price
        # of this client — left out, with one warning.
        dropped = unasked = 0
        for code, row in prices:
            if code and code not in wanted:
                unasked += 1
            elif code in wanted and _same(field(row, "client"), client):
                client_prices.append(row)
            elif code in wanted:
                dropped += 1
        if unasked:
            warn(
                f"Finvalda returned {unasked} price row(s) of products not asked for; "
                "they are left out"
            )
        if dropped:
            warn(
                f"Finvalda returned {dropped} price row(s) without client {client}; "
                "they are left out"
            )
    return {
        "items": items,
        "client_prices": [shape("client_price", r) for r in client_prices],
        "count": len(items),
    }


def debt_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    client = text("client_code", inputs, 15, required=True)
    as_of = date_input("as_of", inputs.get("as_of")) or today()
    rows = [
        r
        for r in describe(
            context,
            "Clients",
            {"Codes": [client]},
            ["code", "title", "debt", "current_debt"],
        )
        # Strict: a row without its code is invalid_response, never "no such client".
        if _same(row_code(r), client)
    ]
    if not rows:
        raise errors.no_effect(errors.NOT_FOUND, f"Finvalda has no client {client}")
    reply = context.call(
        "GetNeapmoketiKliDok",
        verb="GET",
        query=[("sKlientoKodas", client)],
        mutating=False,
        language="lt",
    )
    documents = []
    unpaid = overdue = Decimal(0)
    for row in rows_of(reply.data, strict=False):
        if not isinstance(row, Mapping):
            continue
        remaining = as_decimal(field(row, "likusi_skola"))
        due_date = finvalda_date(field(row, "apmokejimo_data"))
        late: bool | None = None
        if due_date is not None and remaining is not None:
            late = due_date < Date.fromisoformat(as_of) and Decimal(remaining) > 0
        if remaining:
            unpaid += Decimal(remaining)
            if late:
                overdue += Decimal(remaining)
        document = shape("debt_document", row)
        document["overdue"] = late
        documents.append(document)
    record = rows[0]
    return {
        "client_code": client,
        "client_name": field(record, "title"),
        "debt": as_decimal(
            field(record, "current_debt")
            if field(record, "current_debt") is not None
            else field(record, "debt")
        ),
        "unpaid_total": str(cents(unpaid)),
        "overdue_total": str(cents(overdue)),
        "documents": documents,
        "count": len(documents),
    }


CHANGES_MAX_ROWS = 20_000
#: The record each entity of Changes since is returned as.
CHANGE_SHAPES = {
    "Clients": "client",
    "Products": "product",
    "Services": "service",
    "Sales": "operation",
    "Purchases": "operation",
    "Inflows": "operation",
    "Disbursement": "operation",
}


def _edited(row: Mapping[str, Any]) -> datetime | None:
    return finvalda_datetime(field(row, "date_edited"))


#: Rows per page: 300 rows of up to 13 columns stay inside the step output's limits.
CHANGES_MAX_LIMIT = 300


def _change_key(name: str) -> Callable[[Mapping[str, Any]], tuple[Any, ...]]:
    """The order of the pages: by the entity's own code, or by journal and number
    (the number as sent when it is not whole). Rows equal in it are ordered by
    their canonical JSON (``_fixed_order``)."""

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        number = _op_number(row)
        raw = field(row, "op_number")
        whole = (0, number, "") if number is not None else (1, 0, "" if raw is None else str(raw))
        return (row_code(row, name), str(field(row, "journal") or ""), whole)

    return key


def _canonical(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


def _fixed_order(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Every row in one place whatever order Finvalda answers in: by the key, and
    rows equal in it (no code, the same number) by their canonical JSON, which is
    computed only for them."""
    key = _change_key(name)
    keyed = sorted(((key(row), row) for row in rows), key=lambda pair: pair[0])
    out: list[dict[str, Any]] = []
    for _key, pairs in groupby(keyed, key=lambda pair: pair[0]):
        group = [row for _k, row in pairs]
        out += sorted(group, key=_canonical) if len(group) > 1 else group
    return out


def changes_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Records created or edited on or after the day ``since``, page by page.

    Finvalda filters by edit date only (EditedFrom, DateEditedFrom; manual: a
    date), and its edit times are coarse, so nothing is filtered by time here:
    every record Finvalda returns for the day and after is in the pages, in a fixed
    order. Asking again with the same date returns that day's records again —
    skip them by code; nothing is lost.
    """
    entity = inputs.get("entity")
    if entity not in CHANGE_ENTITIES:
        raise invalid(f"entity must be one of: {', '.join(CHANGE_ENTITIES)}")
    day = date_input("since", inputs.get("since"), required=True)
    limit = (
        whole_input("limit", inputs.get("limit"), default=200, minimum=1, maximum=CHANGES_MAX_LIMIT)
        or 200
    )
    page = whole_input("page", inputs.get("page"), default=1, minimum=1, maximum=1_000_000) or 1
    kind, name, _label = CHANGE_ENTITIES[entity]
    if kind == "descriptions":
        columns = {
            "Clients": CLIENT_COLUMNS,
            "Products": PRODUCT_COLUMNS,
            "Services": SERVICE_COLUMNS,
        }[name]
        rows, more = read_list(
            context,
            name,
            {"EditedFrom": day},
            columns,
            limit=1000,
            max_pages=CHANGES_MAX_ROWS // 1000,
            strict=False,
        )
        if more:
            raise errors.no_effect(
                errors.RESPONSE_TOO_LARGE,
                f"More than {CHANGES_MAX_ROWS} records changed since {day}; use a later "
                "since, or Export a list to a file with the EditedFrom filter",
            )
    else:
        rows = operations_of(
            context, name, {"DateEditedFrom": day}, OPERATION_COLUMNS, strict=False
        )
    ordered = _fixed_order(rows, name)
    start = (page - 1) * limit
    chosen = ordered[start : start + limit]
    has_more = start + limit < len(ordered)
    times = [t for t in (_edited(r) for r in chosen) if t is not None]
    kind_of = CHANGE_SHAPES[name]
    return {
        "records": [shape(kind_of, row) for row in chosen],
        "count": len(chosen),
        "total": len(ordered),
        "page": page,
        "next_page": page + 1 if has_more else None,
        "has_more": has_more,
        "latest_edited": iso_moment(max(times)) if times else None,
    }
