"""Test the connection, Stock levels, Product prices, Client debt, Changes since."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..shapes import shape
from .. import errors
from ..advanced.reads import read_description_pages
from ..errors import invalid
from ..fields import field
from ..inputs import date_input, whole_input
from ..reference_data import CHANGE_ENTITIES, DESCRIPTION_TYPES
from ..rows import rows_of
from ..values import as_decimal, cents, finvalda_date, finvalda_datetime, iso_moment
from .common import (
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
    rows, _more = read_list(context, "Clients", {}, ["code"], limit=1, max_pages=1)
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
    rows = [
        r
        for r in describe(context, "Products", {"Codes": codes}, columns, limit=500)
        # The product's code as the record reads it: product, else code.
        if str(field(r, "product") or field(r, "code") or "").strip().upper() in wanted
    ]
    if len(rows) > len(codes):
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            "Finvalda returned more products than were asked for; nothing is passed on",
        )
    items = [shape("product_price", r) for r in rows]
    client_prices: list[Any] = []
    client = text("client_code", inputs, 15)
    if client:
        client_prices = [
            r
            for r in describe(
                context,
                "Prices",
                {"Type": 1, "Client": client, "Codes": codes},
                CLIENT_PRICE_COLUMNS,
                limit=200,
                max_pages=5,
            )
            # Only the prices of the codes asked for, of this client.
            if str(field(r, "code") or "").strip().upper() in wanted
            and _same(field(r, "client") or client, client)
        ]
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
            context, "Clients", {"Codes": [client]}, ["code", "title", "debt", "current_debt"]
        )
        if _same(field(r, "code"), client)
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


def _change_key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    """A fixed order for the pages: by code, or by journal and number."""
    code = str(field(row, "code") or field(row, "product") or "")
    return (code, str(field(row, "journal") or ""), _op_number(row) or 0)


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
        )
        if more:
            raise errors.no_effect(
                errors.RESPONSE_TOO_LARGE,
                f"More than {CHANGES_MAX_ROWS} records changed since {day}; use a later "
                "since, or Export a list to a file with the EditedFrom filter",
            )
    else:
        rows = operations_of(context, name, {"DateEditedFrom": day}, OPERATION_COLUMNS)
    ordered = sorted((dict(r) for r in rows if isinstance(r, Mapping)), key=_change_key)
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
