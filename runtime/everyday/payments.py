"""Register payment."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import errors
from ..advanced.writes import import_parameter
from ..shapes import shape
from ..errors import invalid
from ..fields import field
from ..inputs import date_input
from ..records import check_fields
from ..rows import op_result
from ..transport import dumps_json
from ..values import CENT
from .common import (
    operation_identity,
    Progress,
    _best_effort,
    _op_number,
    client_code_of,
    currency_input,
    money,
    same_document,
    text,
    today,
)

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


def _identity(row: Mapping[str, Any]) -> str:
    """Journal and number of an operation row, as safe text (a strict lookup
    guarantees both; a number that is not whole is shown as Finvalda sent it)."""
    found = operation_identity(row, "")
    number = found["op_number"]
    # A number that is not whole is shown as sent, cut to 20 characters like the
    # journal, so the hint at the end of the message is never cut off.
    shown = number if number is not None else errors.safe_text(str(field(row, "op_number")), 20)
    return f"{found['journal'] or '?'} #{shown}"


def payment_body(inputs: Mapping[str, Any], client: str) -> tuple[str, str, dict[str, Any]]:
    direction = inputs.get("direction")
    if direction not in {"incoming", "outgoing"}:
        raise invalid("direction must be incoming or outgoing")
    head, line_class, op_class = (
        ("IplDok", "IplDokDetEil", "Inflows")
        if direction == "incoming"
        else ("IsmDok", "IsmDokDetEil", "Disbursement")
    )
    amount = money("amount", inputs.get("amount"), minimum=CENT, places=2)
    invoice = text("invoice_document", inputs, 20)
    invoice_series = text("invoice_series", inputs, 10)
    if invoice_series and not invoice:
        raise invalid("invoice_series needs invoice_document")
    header: dict[str, Any] = {
        "sKlientas": client,
        "sValiuta": currency_input(inputs),
        "nTipas": SETTLE_DOCUMENTS if invoice else PREPAYMENT,
        "sDokumentas": text("document", inputs, 20, required=True),
        "tData": date_input("date", inputs.get("date")) or today(),
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
    checked = check_fields(head, header, enforce_required=True)
    checked[line_class] = [check_fields(line_class, line, enforce_required=True)]
    return head, op_class, checked


def payment_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    parameter = import_parameter(context, inputs)
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
            f"({', '.join(_identity(r) for r in existing[:5])}). "
            "If this is a rerun, nothing more is needed; if it is a different payment, give it "
            "a document number of its own",
        )
    body = {
        "ItemClassName": head,
        "sParametras": parameter,
        "xmlstring": dumps_json({head: operation}),
    }
    reply = context.call("InsertNewOperation", verb="POST", body=body, mutating=True)
    saved = op_result(reply.data)
    warnings: list[str] = []
    record = _best_effort(
        lambda: next(
            (
                r
                for r in same_document(context, op_class, document, client=client)
                if saved["op_number"] is None or _op_number(r) == saved["op_number"]
            ),
            None,
        ),
        warnings,
        f"payment {document}",
    )
    payment = shape("operation", record)
    return {
        "journal": saved["journal"] or (payment or {}).get("journal"),
        "op_number": saved["op_number"]
        if saved["op_number"] is not None
        else (payment or {}).get("op_number"),
        "series": saved["series"],
        "document": saved["document"] or document,
        "client_code": client,
        "payment": payment,
        "warnings": warnings,
    }
