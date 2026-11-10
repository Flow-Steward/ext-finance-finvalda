"""Create sales invoice, Create purchase invoice."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .. import errors
from ..advanced.files import read_input_file
from ..advanced.reads import fin_user_of
from ..advanced.writes import import_parameter
from ..errors import invalid
from ..fields import field
from ..inputs import date_input, decimal_input, text_input
from ..records import check_fields
from ..rows import op_result
from ..shapes import shape
from ..transport import dumps_json
from ..values import cents, decimal_of
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
) -> tuple[dict[str, list[dict[str, Any]]], Decimal, Decimal, Totals]:
    """Lines by class, with Finvalda's amount fields computed; the two totals; and what
    the document's amount may be (``Totals``)."""
    _head, _cls, product_class, service_class = INVOICES[operation_id]
    raw = inputs.get("lines")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LINES:
        raise invalid(f"lines must be a list of 1 to {MAX_LINES} lines")
    warehouse = text("warehouse", inputs, 10)
    out: dict[str, list[dict[str, Any]]] = {}
    total = vat_total = Decimal(0)
    totals = Totals()
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
            gross_eur = money(f"{where}.amount_eur", line["amount_eur"], places=2)
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
        totals.add(
            gross - discount_amount,
            gross_eur - discount_eur,
            vat if line.get("vat_percent") is not None else None,
            fields.get("dSumaPVML"),
        )
    return out, total, vat_total, totals


class Totals:
    """What an invoice of these lines can show as its amount, in its currency and in EUR.

    A line without vat_percent takes the VAT of the product's or service's card,
    which this request does not know; its VAT is then anything from 0 up to (not
    including) the line's own amount (Finvalda's rate is a Numeric(4,2) percent).
    """

    def __init__(self) -> None:
        self.net = [Decimal(0), Decimal(0)]
        self.vat = [Decimal(0), Decimal(0)]
        self.unknown = [Decimal(0), Decimal(0)]

    def add(self, net: Decimal, net_eur: Decimal, vat: Decimal | None, vat_eur: Any) -> None:
        self.net[0] += net
        self.net[1] += net_eur
        if vat is None:
            self.unknown[0] += net
            self.unknown[1] += net_eur
        else:
            self.vat[0] += vat
            self.vat[1] += Decimal(str(vat_eur)) if vat_eur is not None else Decimal(0)

    def matches(self, amount: Decimal, *, eur: bool) -> bool:
        i = 1 if eur else 0
        amount = cents(amount)
        net, with_vat = cents(self.net[i]), cents(self.net[i] + self.vat[i])
        if amount in {net, with_vat}:
            return True
        unknown = self.unknown[i]
        return unknown > 0 and with_vat <= amount < cents(with_vat + unknown)

    def shown(self, currency: str) -> str:
        net = cents(self.net[0])
        if self.unknown[0]:
            return f"{net} {currency} without VAT, VAT from the cards"
        return f"{net} {currency} without VAT, {cents(self.net[0] + self.vat[0])} with VAT"


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
    lines, total, vat_total, totals = invoice_lines(inputs, operation_id, currency)
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
        same_as_request(existing, client, document, currency, totals)
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
            generated_name=True,
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
    totals: Totals,
) -> None:
    """A document found by number is this one only when its currency and amount match.

    The manual (3.77) names the header's ``amount`` and ``initial_debt`` columns
    but not whether they include VAT, nor whether a foreign-currency operation shows
    them in its currency or in EUR, so each is compared with this request's total
    without and with VAT, in the currency and in EUR. Lines without vat_percent
    take the card's VAT, so any VAT is accepted for them. Another currency or an
    amount that fits none of these is not this request.
    """
    found_currency = str(field(existing, "currency") or "").strip().upper()
    amounts = [
        number
        for number in (
            decimal_of(field(existing, "amount")),
            decimal_of(field(existing, "initial_debt")),
        )
        if number is not None
    ]
    if found_currency == currency and any(
        totals.matches(a, eur=False) or (currency != "EUR" and totals.matches(a, eur=True))
        for a in amounts
    ):
        return
    shown = f"{amounts[0] if amounts else '?'} {found_currency or '?'}"
    raise errors.no_effect(
        errors.CONFLICT,
        f"Document {document} of client {client} is already in Finvalda with {shown}, and "
        f"this request is {totals.shown(currency)}. Nothing was written. Check that "
        "document in Finvalda: if it is this invoice, nothing more needs to be done; if "
        "it is not, correct the document in Finvalda or the request before running the "
        "step again",
    )
