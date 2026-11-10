"""Everyday steps: plain inputs, several Finvalda calls in one step.

Each write looks up before it creates, so a rerun after an unknown outcome finds
what the first run made. A step that writes more than once reports, when a later
call fails, exactly what it already saved in Finvalda."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import catalog, errors
from ..advanced.files import entity_query, invoice_param
from ..errors import invalid
from ..inputs import allowed, date_input, file_name_input, text_input
from ..reference_data import CHANGE_ENTITIES
from .common import Progress, _codes, code_input, currency_input, money, text
from .files import ATTACH_INPUTS, attach_op, download_op
from .invoices import INVOICE_INPUTS, INVOICES, invoice_header, invoice_lines, invoice_op
from .parties import (
    CLIENT_INPUTS,
    PRODUCT_INPUTS,
    SERVICE_INPUTS,
    _client_values,
    client_op,
    product_op,
    service_op,
)
from .payments import PAYMENT_INPUTS, payment_body, payment_op
from .reads import changes_op, debt_op, prices_op, stock_op, test_connection_op

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
    allowed(inputs, ALLOWED_INPUTS[operation_id])
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
            text_input("document_file", inputs.get("document_file"), limit=300, required=True)
    elif operation_id == "register_payment":
        if not text("client_code", inputs, 15) and not text("client_company_code", inputs, 30):
            raise invalid("Give client_code, or client_company_code to look the client up")
        payment_body(inputs, "VALIDATE")
    elif operation_id == "attach_file":
        entity_query(inputs)
        file_name_input("file_name", inputs.get("file_name"))
        text_input("document_file", inputs.get("document_file"), limit=300, required=True)
        text("file_description", inputs, 200)
    elif operation_id == "download_invoice_pdf":
        invoice_param(inputs)
    elif operation_id == "changes_since":
        if inputs.get("entity") not in CHANGE_ENTITIES:
            raise invalid(f"entity must be one of: {', '.join(CHANGE_ENTITIES)}")
        text("since", inputs, 27, required=True)
    elif operation_id == "client_debt":
        text("client_code", inputs, 15, required=True)
        date_input("as_of", inputs.get("as_of"))
    elif operation_id == "product_prices":
        _codes(inputs, "product_codes", required=True, most=200)


_OP_NEUTRAL: dict[str, Any] = dict(catalog.OP_NEUTRAL)

EVERYDAY_NEUTRAL: dict[str, dict[str, Any]] = {
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


def everyday_neutral_result(operation_id: str) -> dict[str, Any]:
    return dict(EVERYDAY_NEUTRAL[operation_id])
