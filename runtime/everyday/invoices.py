"""Create sales invoice, Create purchase invoice."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .. import errors
from ..advanced.files import read_input_file
from ..advanced.reads import fin_user_of
from ..advanced.writes import import_parameter
from ..shapes import shape
from ..values import decimal_of
from ..errors import invalid
from ..fields import field
from ..inputs import date_input, decimal_input, text_input
from ..records import check_fields
from ..rows import op_result
from ..transport import dumps_json
from ..values import cents
from .common import (
    MAX_LINES,
    Progress,
    _best_effort,
    client_code_of,
    currency_input,
    find_operation,
    money,
    operation_identity,
    own_file_name,
    text,
    today,
)
from .files import store_and_attach

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
        code = text_input(
            f"{where}.code", line.get("code"), limit=20 if kind == "product" else 13, required=True
        )
        quantity = decimal_input(
            f"{where}.quantity", line.get("quantity"), places=4 if kind == "product" else 2
        )
        if quantity <= 0:
            raise invalid(f"{where}.quantity must be positive")
        price = money(f"{where}.price", line.get("price"))
        discount = (
            decimal_input(
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
            store = text_input(f"{where}.warehouse", line.get("warehouse"), limit=10) or warehouse
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
            rate = decimal_input(
                f"{where}.vat_percent", line["vat_percent"], minimum=Decimal(0), places=2
            )
            if rate >= 100:
                raise invalid(f"{where}.vat_percent must be below 100 (Finvalda's Numeric(4,2))")
            vat = cents((gross - discount_amount) * rate / 100)
            fields["dPVM_Procentas"] = str(rate)
            fields["dSumaPVMV"] = str(vat)
            fields["dSumaPVML"] = str(cents((gross_eur - discount_eur) * rate / 100))
        vat_code = text_input(f"{where}.vat_code", line.get("vat_code"), limit=10)
        if vat_code:
            fields["sPvmKodas"] = vat_code
        objects = line.get("objects") or []
        if not isinstance(objects, list) or len(objects) > 6:
            raise invalid(f"{where}.objects must list up to six object codes")
        for n, value in enumerate(objects, start=1):
            if value:
                fields[f"sObjektas{n}"] = text_input(f"{where}.objects", value, limit=10)
        cls = product_class if kind == "product" else service_class
        out.setdefault(cls, []).append(check_fields(cls, fields, enforce_required=True))
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
        "tData": date_input("date", inputs.get("date")) or today(),
    }
    series = text("series", inputs, 10)
    if series:
        fields["sSerija"] = series
    due = date_input("due_date", inputs.get("due_date"))
    if due:
        fields["tMokejimoData"] = due
    notes = text("notes", inputs, 255)
    if notes:
        fields["sPastaba"] = notes
    return check_fields(head, fields)


def invoice_op(
    context: Any, operation_id: str, inputs: Mapping[str, Any], progress: Progress
) -> dict[str, Any]:
    head, op_class, _product, _service = INVOICES[operation_id]
    currency = currency_input(inputs)
    lines, total, vat_total = invoice_lines(inputs, operation_id, currency)
    parameter = import_parameter(context, inputs)
    document = text("document", inputs, 20, required=True)
    series = text("series", inputs, 10)
    client = client_code_of(context, inputs)
    header = invoice_header(operation_id, inputs, client, currency)
    has_file = bool(inputs.get("document_file"))
    content: bytes | None = None
    if has_file:
        # Everything the file needs is checked before the invoice is written.
        fin_user_of(context, {}, required=True)
        content = read_input_file(context, inputs["document_file"])
    per_client = head == "PirkDok"
    existing = find_operation(context, op_class, client, document, series, per_client=per_client)
    created = existing is None
    warnings: list[str] = []
    if existing is not None:
        same_as_request(existing, client, document, currency, total, vat_total)
    if existing is None:
        operation = {**header, **lines}
        body = {
            "ItemClassName": head,
            "sParametras": parameter,
            "xmlstring": dumps_json({head: operation}),
        }
        reply = context.call("InsertNewOperation", verb="POST", body=body, mutating=True)
        saved = op_result(reply.data)
        progress.add(
            f"{head} {saved['journal'] or '?'} #{saved['op_number'] if saved['op_number'] is not None else '?'} "
            f"(document {document})"
        )
        record = _best_effort(
            lambda: find_operation(
                context, op_class, client, document, series, per_client=per_client
            ),
            warnings,
            f"{head} document {document}",
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
    invoice = shape("operation", record)
    return {
        "journal": saved["journal"],
        "op_number": saved["op_number"],
        "series": saved["series"],
        "document": saved["document"],
        "created": created,
        "client_code": client,
        "currency": currency,
        "amount": invoice["amount"] if invoice else None,
        # The totals of this request; an existing document's are Finvalda's amount.
        "total_without_vat": str(cents(total)) if created else None,
        "total_vat": str(cents(vat_total)) if created else None,
        "attachment_name": attachment,
        "invoice": invoice,
        "warnings": warnings,
    }


def same_as_request(
    existing: Mapping[str, Any],
    client: str,
    document: str,
    currency: str,
    total: Decimal,
    vat_total: Decimal,
) -> None:
    """A document found by number is this one only when its currency and amount match.

    Finvalda's operation amount is compared with this request's total with and
    without VAT (which one it shows is not documented); anything else — another
    currency, another amount, an amount that cannot be read — is another document.
    """
    found_currency = str(field(existing, "currency") or "").strip().upper()
    amount = decimal_of(field(existing, "amount"))
    totals = {cents(total), cents(total + vat_total)}
    if found_currency == currency and amount is not None and cents(amount) in totals:
        return
    shown = f"{amount if amount is not None else '?'} {found_currency or '?'}"
    raise errors.no_effect(
        errors.CONFLICT,
        f"Document {document} of client {client} is already in Finvalda with {shown}, which "
        f"does not match this request ({cents(total + vat_total)} {currency}); check Finvalda, "
        "or give this document a number of its own",
    )
