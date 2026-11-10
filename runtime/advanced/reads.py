"""Advanced read steps: lists, operations, the older read methods, prices, permissions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import budget, errors
from ..context import Context
from ..errors import invalid
from ..fields import field
from ..inputs import (
    _COLUMN_RE,
    _FINVALDA_DATE_TIME_RE,
    allowed,
    date_input,
    decimal_input,
    flag_input,
    text_input,
    whole_input,
)
from ..limits import DEFAULT_LIMIT, DEFAULT_MAX_PAGES, MAX_LIMIT, MAX_LIST_ITEMS, MAX_PAGES
from ..reference_data import DESCRIPTION_TYPES
from ..rows import rest_of, rows_of
from ..shapes import normalize_row
from ..transport import DeadlineReached, dumps_json

#: Filter values that are lists in the manual's examples.
_LIST_FILTERS = frozenset({"Codes", "Products", "BarCodes"})


def _method_query(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[tuple[str, str]]:
    names = {p[0] for p in spec["params"]}
    allowed(inputs, names)
    query: list[tuple[str, str]] = []
    for name, wire, kind, aliases in spec["params"]:
        value = inputs.get(name)
        if value is None:
            if kind == "json":
                raise invalid(f"Required input missing: {name}")
            continue
        if kind == "text":
            rendered = text_input(name, value, limit=100)
        elif kind == "date":
            rendered = date_input(name, value)
        elif kind == "integer":
            rendered = str(whole_input(name, value, minimum=-2_147_483_648, maximum=2_147_483_647))
        elif kind == "boolean":
            rendered = "true" if flag_input(name, value) else "false"
        elif kind == "number":
            rendered = str(decimal_input(name, value, places=8))
        else:  # json
            if not isinstance(value, Mapping):
                raise invalid(f"{name} must be an object")
            rendered = dumps_json(value)
            if len(rendered) > 20_000:
                raise invalid(f"{name} is too large")
        for wire_name in (wire, *aliases):
            query.append((wire_name, rendered))
    return query


def _validate_method_read(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    _method_query(spec, inputs)


def run_method_read(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    query = _method_query(spec, context.operation_input)
    reply = context.call(spec["method"], verb=spec["verb"], query=query, mutating=False)
    rows = rows_of(reply.data, strict=False)
    kinds = {name: kind for name, kind in spec.get("columns", [])}
    result: dict[str, Any] = {
        "records": [normalize_row(row, kinds) for row in rows],
        "count": len(rows),
    }
    if not spec.get("columns"):
        # Methods the manual documents without columns (one record, a status):
        # their answer is returned whole.
        result["response"] = normalize_row(rest_of(reply.data, rows))
    return result


def check_columns(
    value: Any, name: str = "columns", documented: list[str] | None = None
) -> list[str]:
    """Column names; only the documented ones when the documents list them."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200:
        raise invalid(f"{name} must be a list of at most 200 column names")
    columns = []
    for item in value:
        if not isinstance(item, str) or not _COLUMN_RE.fullmatch(item.strip()):
            raise invalid(f"{name}: {item!r} is not a column name")
        if documented and item.strip() not in documented:
            raise invalid(
                f"{name}: {item.strip()} is not a documented column; use "
                + ", ".join(documented[:60])
            )
        columns.append(item.strip())
    return columns


def _filter_value(where: str, key: str, value: Any) -> Any:
    if key in _LIST_FILTERS:
        items = value if isinstance(value, list) else [value]
        if len(items) > MAX_LIST_ITEMS:
            raise invalid(f"{where}.{key}: at most {MAX_LIST_ITEMS} values")
        return [
            text_input(f"{where}.{key}", str(v) if isinstance(v, int) else v, limit=60)
            for v in items
        ]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) < 2**31:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(repr(value))
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if isinstance(value, str):
        return text_input(f"{where}.{key}", value, limit=200)
    raise invalid(f"{where}.{key} must be text, a number or true/false")


def check_filter(
    raw: Any, groups: Mapping[str, list[str]], *, label: str
) -> dict[str, dict[str, Any]]:
    """A GetDescriptions filter: flat for one filter group, one object per group otherwise."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise invalid("filter must be an object")
    if not groups:
        raise invalid(f"{label} takes no filter")
    nested = len(groups) > 1 or (
        set(raw) and set(raw) <= set(groups) and all(isinstance(v, Mapping) for v in raw.values())
    )
    sources: dict[str, Any] = dict(raw) if nested else {next(iter(groups)): raw}
    out: dict[str, dict[str, Any]] = {}
    for group, conditions in sources.items():
        if group not in groups:
            raise invalid(
                f"filter: {group} is not a filter group of {label}; use {', '.join(groups)}"
            )
        if not isinstance(conditions, Mapping):
            raise invalid(f"filter.{group} must be an object")
        names = {n.lower(): n for n in groups[group]}
        checked: dict[str, Any] = {}
        for key, value in conditions.items():
            canonical = names.get(str(key).lower())
            if canonical is None:
                raise invalid(
                    f"filter: {key} is not documented for {label}; use {', '.join(groups[group])}"
                )
            if value is None or value == "":
                continue
            checked[canonical] = _filter_value(f"filter.{group}", canonical, value)
        out[group] = checked
    return out


def descriptions_body(
    kind: str, groups: Mapping[str, list[str]], inputs: Mapping[str, Any], *, page: int, limit: int
) -> dict[str, Any]:
    params: dict[str, Any] = {"type": kind, "page": page, "limit": limit}
    params.update(check_filter(inputs.get("filter"), groups, label=kind))
    columns = check_columns(
        inputs.get("columns"), documented=DESCRIPTION_TYPES.get(kind, {}).get("columns") or None
    )
    if columns:
        params["columns"] = columns
    return {"readParams": params}


_DESCRIPTION_INPUTS = {"filter", "columns", "page", "limit", "fetch_all_pages", "max_pages"}


def _validate_descriptions(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    allowed(inputs, _DESCRIPTION_INPUTS)
    descriptions_body(spec["type"], spec["filters"], inputs, page=1, limit=1)
    whole_input("page", inputs.get("page"), minimum=1, maximum=1_000_000)
    whole_input("limit", inputs.get("limit"), minimum=1, maximum=MAX_LIMIT)
    whole_input("max_pages", inputs.get("max_pages"), minimum=1, maximum=MAX_PAGES)
    flag_input("fetch_all_pages", inputs.get("fetch_all_pages"))


def read_description_pages(
    context: Context,
    kind: str,
    groups: Mapping[str, list[str]],
    inputs: Mapping[str, Any],
    *,
    first_page: int,
    limit: int,
    max_pages: int,
    meter: budget.Meter | None,
) -> dict[str, Any]:
    """Read pages until a short page, ``max_pages``, the output budget or the deadline."""
    records: list[Any] = []
    page = first_page
    fetched = 0
    has_more = False
    timed_out = False
    last_page: int | None = None
    while fetched < max_pages:
        body = descriptions_body(kind, groups, inputs, page=page, limit=limit)
        try:
            reply = context.call("GetDescriptions", verb="POST", body=body, mutating=False)
        except DeadlineReached:
            if fetched == 0:
                raise
            has_more = timed_out = True
            break
        rows = rows_of(reply.data)
        chunk = [rows, rows[0]] if fetched == 0 and rows else rows
        if meter is not None and not meter.admit(chunk):
            if fetched == 0:
                raise budget.too_large()
            has_more = True
            break
        records.extend(rows)
        fetched += 1
        last_page = page
        has_more = len(rows) >= limit
        if not has_more:
            break
        page += 1
    return {
        "records": records,
        "page": last_page,
        "next_page": ((last_page or first_page - 1) + 1) if has_more else None,
        "has_more": has_more,
        "pages_fetched": fetched,
        #: True when the step's time ran out between pages (the rows read are kept).
        "timed_out": timed_out,
    }


def run_descriptions(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    _validate_descriptions(spec, inputs, None)
    fetch_all = flag_input("fetch_all_pages", inputs.get("fetch_all_pages"))
    limit = whole_input(
        "limit", inputs.get("limit"), default=DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT
    )
    page = whole_input("page", inputs.get("page"), default=1, minimum=1, maximum=1_000_000)
    max_pages = whole_input(
        "max_pages",
        inputs.get("max_pages"),
        default=DEFAULT_MAX_PAGES,
        minimum=1,
        maximum=MAX_PAGES,
    )
    read = read_description_pages(
        context,
        spec["type"],
        spec["filters"],
        inputs,
        first_page=page or 1,
        limit=limit or DEFAULT_LIMIT,
        max_pages=(max_pages or 1) if fetch_all else 1,
        meter=budget.Meter(),
    )
    records = read.pop("records")
    # The advanced list step keeps what it read and says where to continue.
    read.pop("timed_out")
    return {
        "records": [normalize_row(row) for row in records],
        "count": len(records),
        **read,
    }


def operations_body(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    names = {"filter", "columns"} | ({"full_op", "columns_det"} if not spec["detail"] else set())
    allowed(inputs, names)
    raw = inputs.get("filter") or {}
    if not isinstance(raw, Mapping):
        raise invalid("filter must be an object")
    known = {n.lower(): n for n in spec["filters"]}
    checked: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = known.get(str(key).lower())
        if canonical is None:
            raise invalid(
                f"filter: {key} is not documented for {spec['op_class']}; use "
                + ", ".join(spec["filters"])
            )
        if value is None or value == "":
            continue
        if canonical == "OpNumber":
            checked[canonical] = whole_input(
                "filter.OpNumber", value, minimum=1, maximum=2_147_483_647
            )
        elif canonical == "AdvancePaymentSettled":
            checked[canonical] = (
                "true" if flag_input("filter.AdvancePaymentSettled", value) else "false"
            )
        elif "Date" in canonical:
            text = text_input(f"filter.{canonical}", value, limit=27)
            if not _FINVALDA_DATE_TIME_RE.fullmatch(text):
                raise invalid(f"filter.{canonical} must be a date, YYYY-MM-DD")
            checked[canonical] = text
        else:
            checked[canonical] = _filter_value("filter", canonical, value)
    params: dict[str, Any] = {"OpClass": spec["op_class"], "filter": checked}
    columns = check_columns(inputs.get("columns"), documented=spec.get("columns") or None)
    if columns:
        params["columns"] = {"column": columns}
    if not spec["detail"]:
        params["fullOp"] = flag_input("full_op", inputs.get("full_op"))
        det = check_columns(
            inputs.get("columns_det"), "columns_det", documented=spec.get("columns_det") or None
        )
        if det:
            params["columnsDet"] = {"column": det}
    return {"opReadParams": params}


def _validate_operations(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    operations_body(spec, inputs)


def run_operations(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    body = operations_body(spec, context.operation_input)
    reply = context.call("GetOperations", verb="POST", body=body, mutating=False)
    rows = rows_of(reply.data)
    result = {"records": [normalize_row(row) for row in rows], "count": len(rows)}
    if not budget.fits(result):
        raise errors.no_effect(
            errors.RESPONSE_TOO_LARGE,
            "These operations are too many for one step output; narrow the filter (a date range, "
            "a journal, a client) or choose fewer columns",
        )
    return result


def run_recommended_price(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(
        inputs,
        {
            "invoice_type",
            "invoice_date",
            "item_type",
            "item_code",
            "quantity",
            "warehouse_code",
            "client_code",
            "client_address_code",
            "fin_user",
        },
    )
    invoice_type = whole_input("invoice_type", inputs.get("invoice_type"), minimum=0, maximum=1)
    item_type = whole_input("item_type", inputs.get("item_type"), minimum=1, maximum=2)
    if invoice_type is None or item_type is None:
        raise invalid("invoice_type and item_type are required")
    day = Date.fromisoformat(date_input("invoice_date", inputs.get("invoice_date"), required=True))
    params = {
        "invoiceType": invoice_type,
        "invoiceDate": {"year": day.year, "month": day.month, "day": day.day},
        "itemType": item_type,
        "itemCode": text_input("item_code", inputs.get("item_code"), limit=20, required=True),
        "itemAmount": decimal_input("quantity", inputs.get("quantity"), minimum=Decimal(0)),
        "warehouseCode": text_input("warehouse_code", inputs.get("warehouse_code"), limit=10),
        "clientCode": text_input("client_code", inputs.get("client_code"), limit=15),
        "clientAdressCode": text_input(
            "client_address_code", inputs.get("client_address_code"), limit=13
        ),
        "finUser": fin_user_of(context, inputs, required=False),
    }
    reply = context.call(
        "GetRecommendedPrice", verb="POST", body={"inParams": params}, mutating=False
    )
    data = reply.data
    result = field(data, "result")
    source = result if isinstance(result, Mapping) else data

    def money(key: str) -> str | None:
        value = field(source, key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            return str(Decimal(str(value)))
        except InvalidOperation:
            return None

    return {
        "price": money("price"),
        "vat": money("vat"),
        "discount": money("discount"),
    }


def run_user_permissions(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"fin_user"})
    reply = context.call(
        "GetUserPermissions",
        verb="GET",
        query=[("finUser", fin_user_of(context, inputs, required=True))],
        mutating=False,
    )
    permissions = rows_of(reply.data, strict=False)
    if not permissions:
        result = field(reply.data, "result")
        found = field(result, "perrmissions") if isinstance(result, Mapping) else None
        permissions = found if isinstance(found, list) else []
    return {"permissions": normalize_row(permissions), "count": len(permissions)}


def run_related_invoices(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"client_code", "debt_type"})
    query = [
        (
            "nDebtType",
            str(
                whole_input("debt_type", inputs.get("debt_type"), default=0, minimum=0, maximum=10)
            ),
        )
    ]
    client = text_input("client_code", inputs.get("client_code"), limit=15)
    if client:
        query.append(("sKlientoKodas", client))
    reply = context.call(
        "GetInvoicesRelatedToCustomer", verb="POST", query=query, body={}, mutating=False
    )
    rows_all = rows_of(reply.data, strict=False)
    if client:
        # The client parameter's name comes from a published client of the web service,
        # not from Finvalda's documents. Were it ignored, Finvalda would answer with every
        # client's invoices: each row must show it belongs to the client asked for.
        for row in rows_all:
            owner = next(
                (
                    field(row, key)
                    for key in ("client", "klientas", "kliento_kodas", "sKlientas", "client_code")
                    if field(row, key) not in (None, "")
                ),
                None,
            )
            if (
                not isinstance(row, Mapping)
                or owner is None
                or str(owner).strip().upper() != client.upper()
            ):
                raise errors.no_effect(
                    errors.INVALID_RESPONSE,
                    f"Finvalda's answer holds invoices that are not shown to belong to client "
                    f"{client}; it is not passed on",
                )
    return {"records": [normalize_row(row) for row in rows_all], "count": len(rows_all)}


def fin_user_of(context: Context | None, inputs: Mapping[str, Any], *, required: bool) -> str:
    """The Finvalda employee (finUser) of the newer methods (manual 3.79-3.84).

    It is a Finvalda employee, "not the same as the web service user" (manual
    3.80), so the web service user is never sent in its place.
    """
    given = text_input("fin_user", inputs.get("fin_user"), limit=20)
    if given:
        return given
    if context is None:
        return ""
    if context.connection.fin_user:
        return context.connection.fin_user
    if required:
        raise invalid(
            "Give fin_user, or set the Finvalda employee on the connection: this method needs "
            "a Finvalda employee, which is not the web service user"
        )
    return ""
