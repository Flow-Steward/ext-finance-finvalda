"""Everyday steps: plain inputs, several Finvalda calls in one step.

Each write looks up before it creates, so a rerun after an unknown outcome finds
what the first run made. A step that writes more than once reports, when a later
call fails, exactly what it already saved in Finvalda.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import errors
from . import operations as ops
from .errors import ExtensionError, invalid
from .fields import field
from .reference_data import CHANGE_ENTITIES, DESCRIPTION_TYPES
from . import budget, catalog
from .transport import dumps_json, DeadlineReached

CENT = Decimal("0.01")
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


# ---------------------------------------------------------------------------
# Inputs


def text(name: str, inputs: Mapping[str, Any], limit: int, *, required: bool = False) -> str:
    return ops.text_input(name, inputs.get(name), limit=limit, required=required)


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


def money(name: str, value: Any, *, minimum: Decimal = Decimal(0)) -> Decimal:
    number = ops.decimal_input(name, value, minimum=minimum, places=4)
    if number > MAX_AMOUNT:
        raise invalid(f"{name} is too large")
    return number


def as_decimal(value: Any) -> str | None:
    """An amount from Finvalda (a JSON number or text) as an exact decimal string."""
    number = ops.decimal_of(value)
    return None if number is None else format(number, "f")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def today() -> str:
    return Date.today().isoformat()


# ---------------------------------------------------------------------------
# Lookups


def read_list(
    context: Any,
    kind: str,
    conditions: Mapping[str, Any],
    columns: list[str] | None,
    *,
    limit: int,
    max_pages: int,
    meter: budget.Meter | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Rows of a GetDescriptions type and whether more may follow.

    ``conditions`` belong to the type's first filter group.
    """
    groups = DESCRIPTION_TYPES[kind]["filters"]
    group = next(iter(groups))
    inputs: dict[str, Any] = {
        "filter": {group: dict(conditions)} if len(groups) > 1 else dict(conditions)
    }
    if columns:
        inputs["columns"] = columns
    read = ops.read_description_pages(
        context, kind, groups, inputs, first_page=1, limit=limit, max_pages=max_pages, meter=meter
    )
    if read["timed_out"]:
        # Rows cut short by the clock are no answer: report the timeout, not "more".
        raise DeadlineReached
    rows = [dict(row) for row in read["records"] if isinstance(row, Mapping)]
    return rows, bool(read["has_more"])


def describe(
    context: Any,
    kind: str,
    conditions: Mapping[str, Any],
    columns: list[str] | None,
    *,
    limit: int = 100,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Every row a lookup matches; a lookup that matches more is an error, never cut short."""
    # One page more than allowed tells "exactly that many" from "more".
    rows, more = read_list(context, kind, conditions, columns, limit=limit, max_pages=max_pages + 1)
    if more or len(rows) > limit * max_pages:
        raise errors.no_effect(
            errors.CONFLICT,
            f"More than {len(rows)} Finvalda {kind} rows match {dict(conditions)}; give a code",
        )
    return rows


def operations_of(
    context: Any, op_class: str, conditions: Mapping[str, Any], columns: list[str] | None = None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"OpClass": op_class, "fullOp": False, "filter": dict(conditions)}
    if columns:
        params["columns"] = {"column": columns}
    reply = context.call(
        "GetOperations", verb="POST", body={"opReadParams": params}, mutating=False
    )
    return [dict(row) for row in ops.rows_of(reply.data) if isinstance(row, Mapping)]


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
    return str(field(rows[0], "code"))


# ---------------------------------------------------------------------------
# Find or create client, product, service

CLIENT_INPUTS = {
    "code",
    "company_code",
    "vat_code",
    "email",
    "name",
    "address",
    "city",
    "country_code",
    "phone",
    "client_type",
    "is_person",
    "item_parameter",
}


def _client_values(inputs: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "code": code_input("code", inputs, 15),
        "company_code": text("company_code", inputs, 13),
        "vat_code": text("vat_code", inputs, 30),
        "email": text("email", inputs, 30),
        "name": text("name", inputs, 100),
        "address": text("address", inputs, 90),
        "city": text("city", inputs, 20),
        "country_code": text("country_code", inputs, 3).upper(),
        "phone": text("phone", inputs, 15),
        "client_type": text("client_type", inputs, 10),
        "is_person": ops.flag_input("is_person", inputs.get("is_person")),
    }
    if values["email"] and not _EMAIL_RE.fullmatch(str(values["email"])):
        raise invalid("email is not an e-mail address")
    return values


def client_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    values = _client_values(inputs)
    lookups = [
        ("code", "Codes", [values["code"]] if values["code"] else None, "code"),
        ("company_code", "CompanyCode", values["company_code"] or None, "company_code"),
        ("vat_code", "VatCode", values["vat_code"] or None, "vat_payer_code"),
        ("email", "Email", values["email"] or None, "email"),
    ]
    for matched_by, key, value, column in lookups:
        if value is None:
            continue
        wanted = value[0] if isinstance(value, list) else value
        rows = [
            r
            for r in describe(context, "Clients", {key: value}, CLIENT_COLUMNS)
            if _same(field(r, column), wanted)
        ]
        if len(rows) > 1:
            raise errors.no_effect(
                errors.CONFLICT,
                f"{len(rows)} Finvalda clients match {matched_by} {wanted}; give the client's code",
            )
        if rows:
            record = rows[0]
            for other, column_name in (
                ("company_code", "company_code"),
                ("vat_code", "vat_payer_code"),
            ):
                found = str(field(record, column_name) or "").strip()
                if values[other] and found and not _same(found, values[other]):
                    raise errors.no_effect(
                        errors.CONFLICT,
                        f"Client {field(record, 'code')} has {other} {found}, not {values[other]}",
                    )
            return {
                "client_code": str(field(record, "code")),
                "created": False,
                "matched_by": matched_by,
                "record": dict(record),
            }
    if not values["code"] or not values["name"]:
        raise invalid("No client matches; give code and name to create it")
    fields: dict[str, Any] = {"sKodas": values["code"], "sPavadinimas": values["name"]}
    for key, wire in (
        ("company_code", "sImKodas"),
        ("vat_code", "sPvmMoketojoKod"),
        ("email", "sEMail"),
        ("address", "sAdresas"),
        ("city", "sMiestas"),
        ("country_code", "sValstybeKodas"),
        ("phone", "sTelefonas"),
        ("client_type", "sRusis"),
    ):
        if values[key]:
            fields[wire] = values[key]
    if values["is_person"]:
        fields["nFizinisAsmuo"] = 1
    insert_item(context, "Fvs.Klientas", fields, inputs)
    progress.add(f"client {values['code']}")
    record = _best_effort(
        lambda: describe(context, "Clients", {"Codes": [values["code"]]}, CLIENT_COLUMNS)
    )
    return {
        "client_code": values["code"],
        "created": True,
        "matched_by": None,
        "record": dict(record[0]) if record else None,
    }


def insert_item(context: Any, cls: str, fields: dict[str, Any], inputs: Mapping[str, Any]) -> None:
    item_inputs = {"item_class": cls, "fields": fields}
    if inputs.get("item_parameter"):
        item_inputs["item_parameter"] = inputs["item_parameter"]
    spec = {"method": "InsertNewItem", "classes": [cls]}
    _cls, body, _code = ops.item_write_body(spec, item_inputs, context)
    context.call("InsertNewItem", verb="POST", body=body, mutating=True)


def _best_effort(read: Callable[[], Any]) -> Any:
    """A read after a saved write: its failure must not turn the write into an error."""
    try:
        return read()
    except (ExtensionError, DeadlineReached):
        return None


PRODUCT_INPUTS = {
    "code",
    "barcode",
    "name",
    "unit",
    "vat_code",
    "account_link",
    "product_type",
    "price",
    "purchase_price",
    "item_parameter",
}


def barcode_owners(rows: list[dict[str, Any]], barcode: str) -> list[str]:
    """The product codes of the rows that carry this barcode.

    The manual (4.28) does not name BarCodes' columns, so a row is used only when
    it shows both a product code and the barcode; anything else is an error rather
    than a reason to create a second product.
    """
    owners: set[str] = set()
    for row in rows:
        code = str(field(row, "code") or field(row, "product") or "").strip()
        values = {
            str(v).strip()
            for k, v in row.items()
            if "bar" in str(k).lower() and isinstance(v, (str, int))
        }
        if not code or not values:
            raise errors.no_effect(
                errors.CONFLICT,
                f"Finvalda found barcode {barcode} but its answer does not say which product has "
                "it; give the product code",
            )
        if barcode in values:
            owners.add(code)
    return sorted(owners)


def product_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    code = code_input("code", inputs, 20)
    barcode = text("barcode", inputs, 13)
    if code:
        rows = [
            r
            for r in describe(context, "Products", {"Codes": [code]}, PRODUCT_COLUMNS)
            if _same(field(r, "product"), code)
        ]
        if rows:
            return {
                "product_code": code,
                "created": False,
                "matched_by": "code",
                "record": dict(rows[0]),
            }
    if barcode:
        hits = describe(context, "BarCodes", {"BarCodes": [barcode]}, None)
        codes = barcode_owners(hits, barcode)
        if len(codes) > 1:
            raise errors.no_effect(errors.CONFLICT, f"{len(codes)} products have barcode {barcode}")
        if codes:
            if code and not _same(codes[0], code):
                raise errors.no_effect(
                    errors.CONFLICT, f"Barcode {barcode} belongs to product {codes[0]}, not {code}"
                )
            rows = describe(context, "Products", {"Codes": codes}, PRODUCT_COLUMNS)
            return {
                "product_code": codes[0],
                "created": False,
                "matched_by": "barcode",
                "record": dict(rows[0]) if rows else None,
            }
    name = text("name", inputs, 50)
    if not code or not name:
        raise invalid("No product matches; give code and name to create it")
    fields: dict[str, Any] = {"sKodas": code, "sPavadinimas": name}
    for key, wire, limit in (
        ("barcode", "sBarKodas", 13),
        ("unit", "sMatavimoVnt", 10),
        ("vat_code", "sMokestis", 10),
        ("account_link", "sRysysSuSask", 10),
        ("product_type", "sRusis", 13),
    ):
        value = text(key, inputs, limit)
        if value:
            fields[wire] = value
    if inputs.get("price") is not None:
        fields["dKaina1"] = str(money("price", inputs["price"]))
    if inputs.get("purchase_price") is not None:
        fields["dPirkimoKaina"] = str(money("purchase_price", inputs["purchase_price"]))
    insert_item(context, "Fvs.Preke", fields, inputs)
    progress.add(f"product {code}")
    rows = _best_effort(lambda: describe(context, "Products", {"Codes": [code]}, PRODUCT_COLUMNS))
    return {
        "product_code": code,
        "created": True,
        "matched_by": None,
        "record": dict(rows[0]) if rows else None,
    }


SERVICE_INPUTS = {
    "code",
    "name",
    "unit",
    "vat_code",
    "account_link",
    "price",
    "purchase_price",
    "item_parameter",
}


def service_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    code = code_input("code", inputs, 13, required=True)
    rows = [
        r
        for r in describe(context, "Services", {"Codes": [code]}, SERVICE_COLUMNS)
        if _same(field(r, "code"), code)
    ]
    if rows:
        return {"service_code": code, "created": False, "record": dict(rows[0])}
    name = text("name", inputs, 50)
    if not name:
        raise invalid("No service has this code; give name to create it")
    fields: dict[str, Any] = {"sKodas": code, "sPavadinimas": name}
    for key, wire in (
        ("unit", "sMatVienetas"),
        ("vat_code", "sMokestis"),
        ("account_link", "sRysysSuSask"),
    ):
        value = text(key, inputs, 10)
        if value:
            fields[wire] = value
    if inputs.get("price") is not None:
        fields["dPardavimoKaina"] = str(money("price", inputs["price"]))
    if inputs.get("purchase_price") is not None:
        fields["dPirkimoKaina"] = str(money("purchase_price", inputs["purchase_price"]))
    insert_item(context, "Fvs.Paslauga", fields, inputs)
    progress.add(f"service {code}")
    rows = _best_effort(lambda: describe(context, "Services", {"Codes": [code]}, SERVICE_COLUMNS))
    return {"service_code": code, "created": True, "record": dict(rows[0]) if rows else None}


# ---------------------------------------------------------------------------
# Sales and purchase invoices

INVOICE_INPUTS = {
    "client_code",
    "client_company_code",
    "document",
    "series",
    "date",
    "due_date",
    "currency",
    "warehouse",
    "notes",
    "lines",
    "import_parameter",
    "document_file",
}
#: operation_id -> (header class, GetOperations class, product line class, service line class)
INVOICES: dict[str, tuple[str, str, str, str]] = {
    "create_sales_invoice": ("PardDok", "Sales", "PardDokPrekeDetEil", "PardDokPaslaugaDetEil"),
    "create_purchase_invoice": (
        "PirkDok",
        "Purchases",
        "PirkDokPrekeDetEil",
        "PirkDokPaslaugaDetEil",
    ),
}
_LINE_KEYS = {
    "kind",
    "code",
    "quantity",
    "price",
    "discount_percent",
    "vat_percent",
    "vat_code",
    "warehouse",
    "amount_eur",
    "objects",
}


def invoice_lines(
    inputs: Mapping[str, Any], operation_id: str, currency: str
) -> tuple[dict[str, list[dict[str, Any]]], Decimal, Decimal]:
    """Lines by class, with Finvalda's amount fields computed; and the two totals."""
    _head, _cls, product_class, service_class = INVOICES[operation_id]
    raw = inputs.get("lines")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LINES:
        raise invalid(f"lines must be a list of 1 to {MAX_LINES} lines")
    warehouse = text("warehouse", inputs, 10)
    out: dict[str, list[dict[str, Any]]] = {}
    total = vat_total = Decimal(0)
    for index, line in enumerate(raw):
        where = f"lines[{index}]"
        if not isinstance(line, Mapping) or set(line) - _LINE_KEYS:
            raise invalid(f"{where} may have: {', '.join(sorted(_LINE_KEYS))}")
        kind = line.get("kind") or "product"
        if kind not in {"product", "service"}:
            raise invalid(f"{where}.kind must be product or service")
        code = ops.text_input(
            f"{where}.code", line.get("code"), limit=20 if kind == "product" else 13, required=True
        )
        quantity = ops.decimal_input(
            f"{where}.quantity", line.get("quantity"), places=4 if kind == "product" else 2
        )
        if quantity <= 0:
            raise invalid(f"{where}.quantity must be positive")
        price = money(f"{where}.price", line.get("price"))
        discount = (
            ops.decimal_input(
                f"{where}.discount_percent", line["discount_percent"], minimum=Decimal(0), places=2
            )
            if line.get("discount_percent") is not None
            else Decimal(0)
        )
        if discount > 100:
            raise invalid(f"{where}.discount_percent must be 0 to 100")
        gross = cents(quantity * price)
        discount_amount = cents(gross * discount / 100)
        if currency == "EUR":
            gross_eur = gross
        else:
            if line.get("amount_eur") is None:
                raise invalid(f"{where}.amount_eur is needed when the currency is not EUR")
            gross_eur = cents(money(f"{where}.amount_eur", line["amount_eur"]))
        discount_eur = cents(gross_eur * discount / 100)
        fields: dict[str, Any] = {
            "sKodas": code,
            # Service lines take the quantity times 100 (manual 3.70: "Kiekis padaugintas
            # iš 100", 0.5 is sent as 50); product lines take it as is in the first unit.
            "nKiekis": str(quantity) if kind == "product" else str(int(quantity * 100)),
            "dSumaV": str(gross),
            "dSumaL": str(gross_eur),
        }
        if kind == "product":
            store = (
                ops.text_input(f"{where}.warehouse", line.get("warehouse"), limit=10) or warehouse
            )
            if not store:
                raise invalid(
                    f"{where}: a product line needs a warehouse (the line's or the step's)"
                )
            fields["sSandelis"] = store
            fields["nPirmasMat"] = 1
        if discount:
            fields["dNlProc"] = str(discount)
            fields["dSumaNV"] = str(discount_amount)
            fields["dSumaNL"] = str(discount_eur)
        vat = Decimal(0)
        if line.get("vat_percent") is not None:
            rate = ops.decimal_input(
                f"{where}.vat_percent", line["vat_percent"], minimum=Decimal(0), places=2
            )
            if rate >= 100:
                raise invalid(f"{where}.vat_percent must be below 100 (Finvalda's Numeric(4,2))")
            vat = cents((gross - discount_amount) * rate / 100)
            fields["dPVM_Procentas"] = str(rate)
            fields["dSumaPVMV"] = str(vat)
            fields["dSumaPVML"] = str(cents((gross_eur - discount_eur) * rate / 100))
        vat_code = ops.text_input(f"{where}.vat_code", line.get("vat_code"), limit=10)
        if vat_code:
            fields["sPvmKodas"] = vat_code
        objects = line.get("objects") or []
        if not isinstance(objects, list) or len(objects) > 6:
            raise invalid(f"{where}.objects must list up to six object codes")
        for n, value in enumerate(objects, start=1):
            if value:
                fields[f"sObjektas{n}"] = ops.text_input(f"{where}.objects", value, limit=10)
        cls = product_class if kind == "product" else service_class
        out.setdefault(cls, []).append(ops.check_fields(cls, fields, enforce_required=True))
        total += gross - discount_amount
        vat_total += vat
    return out, total, vat_total


def invoice_header(
    operation_id: str, inputs: Mapping[str, Any], client: str, currency: str
) -> dict[str, Any]:
    head = INVOICES[operation_id][0]
    fields: dict[str, Any] = {
        "sKlientas": client,
        "sDokumentas": text("document", inputs, 20, required=True),
        "sValiuta": currency,
        "tData": ops.date_input("date", inputs.get("date")) or today(),
    }
    series = text("series", inputs, 10)
    if series:
        fields["sSerija"] = series
    due = ops.date_input("due_date", inputs.get("due_date"))
    if due:
        fields["tMokejimoData"] = due
    notes = text("notes", inputs, 255)
    if notes:
        fields["sPastaba"] = notes
    return ops.check_fields(head, fields)


def same_document(
    context: Any, op_class: str, document: str, series: str = "", client: str = ""
) -> list[dict[str, Any]]:
    """Operations of ``op_class`` with this document number (and series, and client)."""
    conditions: dict[str, Any] = {"OrderNumber": document}
    if series:
        conditions["Series"] = series
    if client:
        conditions["Client"] = client
    rows = operations_of(context, op_class, conditions, OPERATION_COLUMNS)
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
    readable = "-".join(ops.safe_name(part) for part in (kind, client, series, document) if part)
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
        "finvalda_result": None,
    }


def _op_number(record: Mapping[str, Any]) -> int | None:
    value = field(record, "op_number")
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def invoice_op(
    context: Any, operation_id: str, inputs: Mapping[str, Any], progress: Progress
) -> dict[str, Any]:
    head, op_class, _product, _service = INVOICES[operation_id]
    currency = currency_input(inputs)
    lines, total, vat_total = invoice_lines(inputs, operation_id, currency)
    parameter = ops.import_parameter(context, inputs)
    document = text("document", inputs, 20, required=True)
    series = text("series", inputs, 10)
    client = client_code_of(context, inputs)
    header = invoice_header(operation_id, inputs, client, currency)
    has_file = bool(inputs.get("document_file"))
    content: bytes | None = None
    if has_file:
        # Everything the file needs is checked before the invoice is written.
        ops.fin_user_of(context, {}, required=True)
        content = ops.read_input_file(context, inputs["document_file"])
    per_client = head == "PirkDok"
    existing = find_operation(context, op_class, client, document, series, per_client=per_client)
    created = existing is None
    if existing is None:
        operation = {**header, **lines}
        body = {
            "ItemClassName": head,
            "sParametras": parameter,
            "xmlstring": dumps_json({head: operation}),
        }
        reply = context.call("InsertNewOperation", verb="POST", body=body, mutating=True)
        saved = ops.op_result(reply.data)
        progress.add(
            f"{head} {saved['journal'] or '?'} #{saved['op_number'] if saved['op_number'] is not None else '?'} "
            f"(document {document})"
        )
        record = _best_effort(
            lambda: find_operation(
                context, op_class, client, document, series, per_client=per_client
            )
        )
        if record is not None:
            # An answer without OP_DUOMENYS: the operation read back names it.
            for key, value in operation_identity(record, document).items():
                if saved.get(key) in (None, "") and value not in (None, ""):
                    saved[key] = value
    else:
        record = existing
        saved = operation_identity(existing, document)
    attachment = None
    if has_file:
        if not saved["journal"] or saved["op_number"] is None:
            raise progress.fail(
                errors.no_effect(
                    errors.INVALID_RESPONSE,
                    "Finvalda did not return the operation's journal and number, so the file "
                    "could not be attached",
                ),
                "Attaching the file",
            )
        entity = 3 if head == "PardDok" else 7
        # The register's file names are global: the kind and the client keep a sale's
        # and a purchase's (or two suppliers') equal document numbers apart.
        name = own_file_name("sale" if head == "PardDok" else "purchase", client, series, document)
        attachment = store_and_attach(
            context,
            {
                "entity_type": entity,
                "entity_id1": saved["journal"],
                "entity_id2": str(saved["op_number"]),
                "file_name": name,
            },
            progress,
            content=content,
            own_name=True,
        )["file_name"]
    return {
        **saved,
        "created": created,
        "client_code": client,
        "total_without_vat": str(cents(total)),
        "total_vat": str(cents(vat_total)),
        "attachment_name": attachment,
        "record": record,
    }


# ---------------------------------------------------------------------------
# Payments

PAYMENT_INPUTS = {
    "direction",
    "client_code",
    "client_company_code",
    "document",
    "amount",
    "date",
    "currency",
    "invoice_series",
    "invoice_document",
    "description",
    "import_parameter",
}
#: Manual 3.70, IplDok/IsmDok nTipas: 0 prepayment, 3 settles the named documents.
PREPAYMENT = 0
SETTLE_DOCUMENTS = 3


def payment_body(inputs: Mapping[str, Any], client: str) -> tuple[str, str, dict[str, Any]]:
    direction = inputs.get("direction")
    if direction not in {"incoming", "outgoing"}:
        raise invalid("direction must be incoming or outgoing")
    head, line_class, op_class = (
        ("IplDok", "IplDokDetEil", "Inflows")
        if direction == "incoming"
        else ("IsmDok", "IsmDokDetEil", "Disbursement")
    )
    amount = cents(money("amount", inputs.get("amount"), minimum=CENT))
    invoice = text("invoice_document", inputs, 20)
    invoice_series = text("invoice_series", inputs, 10)
    if invoice_series and not invoice:
        raise invalid("invoice_series needs invoice_document")
    header: dict[str, Any] = {
        "sKlientas": client,
        "sValiuta": currency_input(inputs),
        "nTipas": SETTLE_DOCUMENTS if invoice else PREPAYMENT,
        "sDokumentas": text("document", inputs, 20, required=True),
        "tData": ops.date_input("date", inputs.get("date")) or today(),
    }
    description = text("description", inputs, 50)
    if description:
        header["sPavadinimas"] = description
    line: dict[str, Any] = {"dSumaV": str(amount)}
    if invoice:
        line["sDokumentas"] = invoice
        if invoice_series:
            line["sSerija"] = invoice_series
    elif description:
        line["sPavadinimas"] = description
    checked = ops.check_fields(head, header, enforce_required=True)
    checked[line_class] = [ops.check_fields(line_class, line, enforce_required=True)]
    return head, op_class, checked


def payment_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    parameter = ops.import_parameter(context, inputs)
    payment_body(inputs, "VALIDATE")
    client = client_code_of(context, inputs)
    head, op_class, operation = payment_body(inputs, client)
    document = operation["sDokumentas"]
    # One statement may hold several payments of a client, and nothing in Finvalda's
    # answer tells a rerun from another payment for sure. So any operation of this
    # class with this document number for the client stops the step (0.1.0).
    existing = same_document(context, op_class, document, client=client)
    if existing:
        raise errors.no_effect(
            errors.CONFLICT,
            f"A payment of client {client} with document {document} is already in Finvalda "
            f"({', '.join(str(field(r, 'journal') or '?') + ' #' + str(_op_number(r) or '?') for r in existing[:5])}). "
            "If this is a rerun, nothing more is needed; if it is a different payment, give it "
            "a document number of its own",
        )
    body = {
        "ItemClassName": head,
        "sParametras": parameter,
        "xmlstring": dumps_json({head: operation}),
    }
    reply = context.call("InsertNewOperation", verb="POST", body=body, mutating=True)
    saved = ops.op_result(reply.data)
    record = _best_effort(
        lambda: next(
            (
                r
                for r in same_document(context, op_class, document, client=client)
                if _op_number(r) == saved["op_number"]
            ),
            None,
        )
    )
    return {**saved, "created": True, "client_code": client, "record": record}


# ---------------------------------------------------------------------------
# Files

ATTACH_INPUTS = {
    "entity_type",
    "entity_id1",
    "entity_id2",
    "document_file",
    "file_name",
    "file_description",
}


def store_and_attach(
    context: Any,
    inputs: Mapping[str, Any],
    progress: Progress,
    *,
    content: bytes | None = None,
    own_name: bool = False,
) -> dict[str, Any]:
    """Store a workflow file in the register and attach it, unless it is already attached.

    Every call is guarded, so a failure after an earlier write names what was saved.
    InsertDocument documents no "already stored" code (manual 3.80: 1 no name,
    2 unknown employee, 3 no content) and the register cannot be listed, so a stored
    file of the same name is assumed only for ``own_name``: a name this extension
    builds from the kind, the client and the document, which no other file has.
    Otherwise a refused store is reported with a hint to attach by hand.
    """
    query = ops.entity_query(inputs)
    name = ops.file_name_input("file_name", inputs.get("file_name"))
    user = ops.fin_user_of(context, {}, required=True)
    reply = progress.guard(
        "Reading the attached files",
        lambda: context.call("GetAttachedDocument", verb="GET", query=query, mutating=False),
    )
    if any(f["name"] == name for f in ops.attached_files(reply.data)):
        return {"file_name": name, "stored": False, "attached": False}
    if content is None:
        content = ops.read_input_file(context, inputs.get("document_file"))
    document_inputs = {"file_name": name}
    if inputs.get("file_description"):
        document_inputs["file_description"] = inputs["file_description"]
    body = ops.insert_document_params(document_inputs, context, content)
    attach_query = [*query, ("documentId", name), ("finUser", user)]
    try:
        progress.guard(
            "Storing the file",
            lambda: context.call("InsertDocument", verb="POST", body=body, mutating=True),
        )
    except ExtensionError as refused:
        # Only Finvalda's own refusal (nothing saved) can mean "already stored";
        # a network failure or a timeout is passed on as it is.
        if refused.definitely_no_external_effect is not True or refused.code not in {
            errors.UPSTREAM_VALIDATION_FAILED,
            errors.CONFLICT,
        }:
            raise
        if not own_name:
            raise ExtensionError(
                refused.code,
                f"{refused.message}. If an earlier run stored {name}, attach it with "
                "Advanced · Attach a document",
                failure_class=refused.failure_class,
                retryable=refused.retryable,
                definitely_no_external_effect=refused.definitely_no_external_effect,
                external_effect_status=refused.external_effect_status,
                http_status=refused.http_status,
                finvalda_code=refused.finvalda_code,
            ) from refused
        # Our own name: an earlier run of this step stored it. Attach that one; an
        # unknown outcome of the attach is passed on as it is.
        try:
            attached = progress.guard(
                "Attaching the stored document",
                lambda: ops.attach_document(context, attach_query),
            )
        except ExtensionError as failed:
            if failed.definitely_no_external_effect is not True:
                raise
            raise errors.no_effect(
                errors.CONFLICT,
                f"Finvalda refused to store {name} ({refused.message}) and to attach a stored "
                f"one ({failed.message}); attach the file by hand in Finvalda",
            ) from failed
        return {"file_name": name, "stored": False, "attached": attached}
    progress.add(f"document {name} in the register")
    attached = progress.guard(
        "Attaching the document", lambda: ops.attach_document(context, attach_query)
    )
    return {"file_name": name, "stored": True, "attached": attached}


def attach_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    return store_and_attach(context, inputs, progress)


# ---------------------------------------------------------------------------
# Reads


def test_connection_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    started = context.clock()
    rows, _more = read_list(context, "Clients", {}, ["code"], limit=1, max_pages=1)
    return {
        "connected": True,
        "company_id": context.connection.company_id or None,
        "clients_visible": bool(rows),
        "duration_ms": int((context.clock() - started) * 1000),
    }


def _codes(inputs: Mapping[str, Any], name: str, *, required: bool, most: int = 500) -> list[str]:
    raw = inputs.get(name)
    if raw is None:
        if required:
            raise invalid(f"Required input missing: {name}")
        return []
    if not isinstance(raw, list) or len(raw) > most or (required and not raw):
        raise invalid(f"{name} must be a list of 1 to {most} codes")
    return [ops.text_input(name, v, limit=20, required=True) for v in raw]


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
    limit = (
        ops.whole_input("limit", inputs.get("limit"), default=200, minimum=1, maximum=500) or 200
    )
    page = ops.whole_input("page", inputs.get("page"), default=1, minimum=1, maximum=1_000_000) or 1
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
    read = ops.read_description_pages(
        context,
        "CurrentStock",
        groups,
        {"filter": conditions, "columns": columns},
        first_page=page,
        limit=limit,
        max_pages=1,
        meter=None,
    )
    rows = [r for r in read["records"] if isinstance(r, Mapping)]
    items = [
        {
            "code": field(r, "code"),
            "title": field(r, "title"),
            "warehouse": field(r, "warehouse"),
            "warehouse_title": field(r, "warehouse_title"),
            "quantity": as_decimal(field(r, "quantity")),
            "quantity_with_reserved": as_decimal(field(r, "quantity_with_reserved")),
            "unit": field(r, "measure_unit"),
            "primary_cost": as_decimal(field(r, "primary_cost")),
        }
        for r in rows
    ]
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
    items = []
    for r in rows:
        item: dict[str, Any] = {"code": field(r, "product"), "title": field(r, "title")}
        for n in range(1, 7):
            item[f"sell_price{n}"] = as_decimal(field(r, f"sell_price{n}"))
        item["sell_currency"] = field(r, "sell_currency")
        item["purchase_price"] = as_decimal(field(r, "purchase_price"))
        item["purchase_currency"] = field(r, "purchase_currency")
        item["vat_percent"] = as_decimal(field(r, "vat_percent"))
        items.append(item)
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
    return {"items": items, "client_prices": client_prices, "count": len(items)}


def debt_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    client = text("client_code", inputs, 15, required=True)
    as_of = ops.date_input("as_of", inputs.get("as_of")) or today()
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
    for row in ops.rows_of(reply.data, strict=False):
        if not isinstance(row, Mapping):
            continue
        remaining = as_decimal(field(row, "likusi_skola"))
        due_date = finvalda_date(field(row, "apmokejimo_data"))
        due = due_date.isoformat() if due_date else None
        late: bool | None = None
        if due_date is not None and remaining is not None:
            late = due_date < Date.fromisoformat(as_of) and Decimal(remaining) > 0
        if remaining:
            unpaid += Decimal(remaining)
            if late:
                overdue += Decimal(remaining)
        documents.append(
            {
                "document": field(row, "dokumentas"),
                "series": field(row, "serija"),
                "journal": field(row, "zurnalas"),
                "op_number": field(row, "op_numeris"),
                "date": _iso(finvalda_date(field(row, "dokumento_data"))),
                "payment_date": due,
                "amount": as_decimal(field(row, "pradine_suma")),
                "remaining": remaining,
                "amount_currency": as_decimal(field(row, "pradine_suma_val")),
                "remaining_currency": as_decimal(field(row, "likusi_skola_val")),
                "currency": field(row, "valiuta"),
                "overdue": late,
            }
        )
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
_DOTNET_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


def finvalda_date(value: Any) -> Date | None:
    """A date in one of Finvalda's forms: 2026-11-10T00:00:00, 2026.11.10, /Date(ms+0300)/."""
    moment = finvalda_datetime(value)
    return moment.date() if moment else None


@lru_cache(maxsize=1)
def _vilnius_zone() -> Any:
    """Europe/Vilnius from the time zone database, looked up once; None without one."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Vilnius")
    except Exception:  # noqa: BLE001 - no time zone data: the EU rule is used
        return None


def _zone_offset(moment: datetime) -> timedelta | None:
    """Europe/Vilnius's offset at ``moment``, when the runtime has time zone data."""
    zone = _vilnius_zone()
    return moment.astimezone(zone).utcoffset() if zone is not None else None


def _vilnius_offset(moment: datetime) -> timedelta:
    """Lithuania's offset at a UTC moment: EET +2, EEST +3 from the last Sunday of
    March 01:00 UTC to the last Sunday of October 01:00 UTC (EU rule)."""
    offset = _zone_offset(moment)
    if offset is not None:
        return offset

    def last_sunday(month: int) -> datetime:
        day = datetime(moment.year, month, 31, 1, tzinfo=timezone.utc)
        return day - timedelta(days=(day.weekday() + 1) % 7)

    summer = last_sunday(3) <= moment < last_sunday(10)
    return timedelta(hours=3 if summer else 2)


def _dotnet(text: str) -> datetime | None:
    """/Date(ms±hhmm)/ as local wall time: by its offset, else Lithuania's.

    Every other time from Finvalda (ISO text, since) is the server's local time,
    so a /Date/ without an offset is read as Lithuanian local time too.
    """
    match = _DOTNET_DATE.match(text)
    if not match:
        return None
    moment = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
    offset = match.group(2)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        moment += sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
    else:
        moment += _vilnius_offset(moment)
    return moment.replace(tzinfo=None)


def _iso(value: Date | None) -> str | None:
    return value.isoformat() if value else None


_DATE_TIME = re.compile(
    r"^(\d{4})[-.](\d{2})[-.](\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,7}))?)?)?"
)


def finvalda_datetime(value: Any) -> datetime | None:
    """A moment in one of Finvalda's forms (T or space, dashes or dots, /Date(ms)/)."""
    text = str(value or "").strip()
    match = _DATE_TIME.match(text)
    try:
        if match:
            year, month, day, hour, minute, second, fraction = match.groups()
            micro = int((fraction or "0").ljust(6, "0")[:6])
            return datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
                micro,
            )
        return _dotnet(text)
    except (ValueError, OverflowError, OSError):
        return None
    return None


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
    raw_since = text("since", inputs, 27, required=True)
    since = finvalda_datetime(raw_since)
    if since is None or not ops._DATE_TIME_RE.fullmatch(raw_since.replace(" ", "T")):
        raise invalid("since must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
    # Finvalda's filters take a date only (manual: yyyy.MM.dd or yyyy-MM-dd); the time
    # part of since is applied here, on the rows of that day.
    day = since.date().isoformat()
    limit = (
        ops.whole_input(
            "limit", inputs.get("limit"), default=200, minimum=1, maximum=CHANGES_MAX_LIMIT
        )
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
            skip = (moment + timedelta(microseconds=1)).isoformat()
            raise errors.no_effect(
                errors.RESPONSE_TOO_LARGE,
                f"{len(members)} records share the edit time {moment.isoformat()}, more than one "
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
        next_since = since.isoformat()
    elif not moved_on or (len(page) == 1 and last == since):
        next_since = (last + timedelta(microseconds=1)).isoformat()
    else:
        next_since = last.isoformat()
    records = [row for _, members in page for row in members]
    result: dict[str, Any] = {
        "records": records,
        "count": len(records),
        "latest_edited": last.isoformat() if last else None,
        "next_since": next_since,
        "has_more": has_more,
        "undated": [] if has_more else undated[:MAX_UNDATED],
        "undated_count": len(undated),
    }
    if not budget.fits(result):
        result["undated"] = []
    return result


def download_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    return ops.make_invoice_pdf(context, inputs)


# ---------------------------------------------------------------------------
# Registry

ALLOWED_INPUTS: dict[str, set[str]] = {
    "test_connection": set(),
    "find_or_create_client": CLIENT_INPUTS,
    "find_or_create_product": PRODUCT_INPUTS,
    "find_or_create_service": SERVICE_INPUTS,
    "create_sales_invoice": INVOICE_INPUTS,
    "create_purchase_invoice": INVOICE_INPUTS,
    "register_payment": PAYMENT_INPUTS,
    "attach_file": ATTACH_INPUTS,
    "download_invoice_pdf": {"invoice_form", "journal", "op_number", "series", "document"},
    "stock_levels": {"product_codes", "warehouse", "warehouse_group", "limit", "page"},
    "product_prices": {"product_codes", "client_code"},
    "client_debt": {"client_code", "as_of"},
    "changes_since": {"entity", "since", "limit"},
}


def run(context: Any, operation_id: str) -> dict[str, Any]:
    inputs = {k: v for k, v in context.operation_input.items() if k != "connection_ref"}
    validate(operation_id, inputs)
    progress = Progress()
    if operation_id == "test_connection":
        return test_connection_op(context, inputs)
    if operation_id == "find_or_create_client":
        return client_op(context, inputs, progress)
    if operation_id == "find_or_create_product":
        return product_op(context, inputs, progress)
    if operation_id == "find_or_create_service":
        return service_op(context, inputs, progress)
    if operation_id in INVOICES:
        return invoice_op(context, operation_id, inputs, progress)
    if operation_id == "register_payment":
        return payment_op(context, inputs, progress)
    if operation_id == "attach_file":
        return attach_op(context, inputs, progress)
    if operation_id == "download_invoice_pdf":
        return download_op(context, inputs)
    if operation_id == "stock_levels":
        return stock_op(context, inputs)
    if operation_id == "product_prices":
        return prices_op(context, inputs)
    if operation_id == "client_debt":
        return debt_op(context, inputs)
    if operation_id == "changes_since":
        return changes_op(context, inputs)
    raise errors.no_effect(
        errors.UNSUPPORTED_OPERATION, "That operation is not part of this extension"
    )


def validate(operation_id: str, inputs: Mapping[str, Any]) -> None:
    """Check an everyday step's inputs without calling Finvalda."""
    inputs = {k: v for k, v in inputs.items() if k != "connection_ref"}
    ops.allowed(inputs, ALLOWED_INPUTS[operation_id])
    if operation_id == "find_or_create_client":
        values = _client_values(inputs)
        if not any(values[k] for k in ("code", "company_code", "vat_code", "email")):
            raise invalid("Give code, company_code, vat_code or email to look the client up")
    elif operation_id == "find_or_create_product":
        code_input("code", inputs, 20)
        if not text("code", inputs, 20) and not text("barcode", inputs, 13):
            raise invalid("Give code or barcode to look the product up")
        for key in ("price", "purchase_price"):
            if inputs.get(key) is not None:
                money(key, inputs[key])
    elif operation_id == "find_or_create_service":
        code_input("code", inputs, 13, required=True)
        for key in ("price", "purchase_price"):
            if inputs.get(key) is not None:
                money(key, inputs[key])
    elif operation_id in INVOICES:
        currency = currency_input(inputs)
        invoice_lines(inputs, operation_id, currency)
        if not text("client_code", inputs, 15) and not text("client_company_code", inputs, 30):
            raise invalid("Give client_code, or client_company_code to look the client up")
        invoice_header(
            operation_id, inputs, text("client_code", inputs, 15) or "VALIDATE", currency
        )
        if inputs.get("document_file") is not None:
            ops.text_input("document_file", inputs.get("document_file"), limit=300, required=True)
    elif operation_id == "register_payment":
        if not text("client_code", inputs, 15) and not text("client_company_code", inputs, 30):
            raise invalid("Give client_code, or client_company_code to look the client up")
        payment_body(inputs, "VALIDATE")
    elif operation_id == "attach_file":
        ops.entity_query(inputs)
        ops.file_name_input("file_name", inputs.get("file_name"))
        ops.text_input("document_file", inputs.get("document_file"), limit=300, required=True)
        text("file_description", inputs, 200)
    elif operation_id == "download_invoice_pdf":
        ops.invoice_param(inputs)
    elif operation_id == "changes_since":
        if inputs.get("entity") not in CHANGE_ENTITIES:
            raise invalid(f"entity must be one of: {', '.join(CHANGE_ENTITIES)}")
        text("since", inputs, 27, required=True)
    elif operation_id == "client_debt":
        text("client_code", inputs, 15, required=True)
        ops.date_input("as_of", inputs.get("as_of"))
    elif operation_id == "product_prices":
        _codes(inputs, "product_codes", required=True, most=200)


_OP_NEUTRAL: dict[str, Any] = dict(catalog.OP_NEUTRAL)
NEUTRAL_OUTPUTS: dict[str, dict[str, Any]] = {
    "find_or_create_client": {
        "client_code": None,
        "created": False,
        "matched_by": None,
        "record": None,
    },
    "find_or_create_product": {
        "product_code": None,
        "created": False,
        "matched_by": None,
        "record": None,
    },
    "find_or_create_service": {"service_code": None, "created": False, "record": None},
    "create_sales_invoice": {
        **_OP_NEUTRAL,
        "created": False,
        "client_code": None,
        "total_without_vat": None,
        "total_vat": None,
        "attachment_name": None,
        "record": None,
    },
    "create_purchase_invoice": {
        **_OP_NEUTRAL,
        "created": False,
        "client_code": None,
        "total_without_vat": None,
        "total_vat": None,
        "attachment_name": None,
        "record": None,
    },
    "register_payment": {**_OP_NEUTRAL, "created": False, "client_code": None, "record": None},
    "attach_file": {"file_name": "", "stored": False, "attached": False},
}


def neutral_result(operation_id: str) -> dict[str, Any]:
    return dict(NEUTRAL_OUTPUTS[operation_id])


__all__ = ["NEUTRAL_OUTPUTS", "Progress", "neutral_result", "run", "validate"]
