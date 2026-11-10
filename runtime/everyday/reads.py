"""Test the connection, Stock levels, Product prices, Client debt, Changes since."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as Date
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..shapes import shape
from ..values import iso_moment
from .. import budget, errors
from ..advanced.reads import read_description_pages
from ..errors import invalid
from ..fields import field
from ..inputs import _DATE_TIME_RE, date_input, whole_input
from ..reference_data import CHANGE_ENTITIES, DESCRIPTION_TYPES
from ..rows import rows_of
from ..values import as_decimal, cents, finvalda_date, finvalda_datetime
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
    rows = describe(context, "Products", {"Codes": codes}, columns, limit=500)
    items = [shape("product_price", r) for r in rows]
    client_prices: list[Any] = []
    client = text("client_code", inputs, 15)
    if client:
        client_prices = describe(
            context,
            "Prices",
            {"Type": 1, "Client": client, "Codes": codes},
            CLIENT_PRICE_COLUMNS,
            limit=200,
            max_pages=5,
        )
    if len(rows) > len(codes):
        raise errors.no_effect(
            errors.RESPONSE_TOO_LARGE, "Finvalda returned more products than were asked for"
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
        document = shape("debt_document", row) or {}
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


#: Rows per step: 300 rows of up to 13 columns stay inside the step output's limits.
CHANGES_MAX_LIMIT = 300

#: Records of one edit time are never split; a group bigger than this cannot be read.
MAX_GROUP = 600

#: Rows whose edit time is in no known form, returned with the last page only.
MAX_UNDATED = 100


def changes_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Records edited at or after ``since``, in whole groups of one edit time.

    Finvalda's edit times are coarse (seconds, sometimes minutes or a date), so
    since is inclusive and a group of one time is never split: the records at the
    boundary come again on the next call — repeated, never lost. Each step also
    moves past the time it started at, so a long run of one time cannot stall.
    """
    entity = inputs.get("entity")
    if entity not in CHANGE_ENTITIES:
        raise invalid(f"entity must be one of: {', '.join(CHANGE_ENTITIES)}")
    raw_since = text("since", inputs, 35, required=True)
    since = finvalda_datetime(raw_since)
    if since is None or not _DATE_TIME_RE.fullmatch(raw_since.replace(" ", "T")):
        raise invalid("since must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
    # Finvalda's filters take a date only (manual: yyyy.MM.dd or yyyy-MM-dd); the time
    # part of since is applied here, on the rows of that day.
    day = since.date().isoformat()
    limit = (
        whole_input("limit", inputs.get("limit"), default=200, minimum=1, maximum=CHANGES_MAX_LIMIT)
        or 200
    )
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
                f"More than {CHANGES_MAX_ROWS} records changed since {raw_since}; use a later "
                "since, or Export a list to a file with the EditedFrom filter",
            )
    else:
        rows = operations_of(context, name, {"DateEditedFrom": day}, OPERATION_COLUMNS)
    # Each row's edit time, read once.
    dated: list[tuple[datetime, dict[str, Any]]] = []
    undated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        moment = _edited(row)
        if moment is None:
            undated.append(dict(row))
        elif moment >= since:
            dated.append((moment, dict(row)))
    # By time, then by code (or journal and number), so a page reads in a fixed order.
    dated.sort(
        key=lambda item: (
            item[0],
            str(field(item[1], "code") or field(item[1], "product") or ""),
            str(field(item[1], "journal") or ""),
            _op_number(item[1]) or 0,
        )
    )
    groups: list[tuple[datetime, list[dict[str, Any]]]] = []
    for moment, row in dated:
        if groups and groups[-1][0] == moment:
            groups[-1][1].append(row)
        else:
            groups.append((moment, [row]))
    for moment, members in groups:
        if len(members) > MAX_GROUP or not budget.fits({"records": members}):
            skip = iso_moment(moment + timedelta(microseconds=1))
            raise errors.no_effect(
                errors.RESPONSE_TOO_LARGE,
                f"{len(members)} records share the edit time {iso_moment(moment)}, more than one "
                f"step can return; read them with Export a list to a file, then continue with "
                f"since = {skip}",
            )
    page: list[tuple[datetime, list[dict[str, Any]]]] = []
    rest = list(groups)
    moved_on = True
    if rest and rest[0][0] == since:
        # The group at since itself, repeated from the last call ("better again than
        # lost"). When it is larger than limit it comes alone, and the next call starts
        # just after it — every record of it is in this page.
        if len(rest[0][1]) > limit:
            page, rest, moved_on = [rest[0]], rest[1:], False
        else:
            page.append(rest.pop(0))
    if moved_on:
        taken = 0
        while rest:
            size = len(rest[0][1])
            if taken and taken + size > limit:
                break
            candidate = [*page, rest[0]]
            if taken and not budget.fits({"records": [r for _, g in candidate for r in g]}):
                break
            page.append(rest.pop(0))
            taken += size
            if taken >= limit:
                break
    while len(page) > 1 and not budget.fits({"records": [r for _, g in page for r in g]}):
        rest.insert(0, page.pop())
    has_more = bool(rest)
    last = page[-1][0] if page else None
    if last is None:
        next_moment = since
    elif not moved_on or (len(page) == 1 and last == since):
        next_moment = last + timedelta(microseconds=1)
    else:
        next_moment = last
    kind = CHANGE_SHAPES[name]
    records = [shape(kind, row) for _, members in page for row in members]
    result: dict[str, Any] = {
        "records": records,
        "count": len(records),
        "latest_edited": iso_moment(last),
        "next_since": iso_moment(next_moment),
        "has_more": has_more,
        "undated": [] if has_more else [shape(kind, row) for row in undated[:MAX_UNDATED]],
        "undated_count": len(undated),
    }
    if not budget.fits(result):
        result["undated"] = []
    return result
