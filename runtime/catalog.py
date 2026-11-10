"""Operation registry: every Advanced step (from registry.json) plus the everyday steps."""

from __future__ import annotations

from .reference_data import OPERATIONS

TEST_CONNECTION = "test_connection"

#: Everyday steps: plain inputs, several Finvalda calls in one step.
FRIENDLY_WRITE_IDS: frozenset[str] = frozenset(
    {
        "find_or_create_client",
        "find_or_create_product",
        "find_or_create_service",
        "create_sales_invoice",
        "create_purchase_invoice",
        "register_payment",
        "attach_file",
    }
)
FRIENDLY_READ_IDS: frozenset[str] = frozenset(
    {
        TEST_CONNECTION,
        "download_invoice_pdf",
        "stock_levels",
        "product_prices",
        "client_debt",
        "changes_since",
    }
)
FRIENDLY_IDS: frozenset[str] = FRIENDLY_WRITE_IDS | FRIENDLY_READ_IDS

#: Runners whose steps change data in Finvalda.
WRITE_RUNNERS: frozenset[str] = frozenset(
    {
        "item_write",
        "props_write",
        "delete_item",
        "append_group",
        "operation_write",
        "delete_operation",
        "lock",
        "change_journal",
        "copy_operation",
        "insert_document",
        "delete_document",
        "attach_document",
        "call_method",
    }
)
ADVANCED_IDS: frozenset[str] = frozenset(OPERATIONS)
OPERATION_IDS: frozenset[str] = ADVANCED_IDS | FRIENDLY_IDS
WRITE_OPERATION_IDS: frozenset[str] = (
    frozenset(oid for oid, spec in OPERATIONS.items() if spec["runner"] in WRITE_RUNNERS)
    | FRIENDLY_WRITE_IDS
)
#: Steps that save a workflow file after reading.
FILE_OUTPUT_IDS: frozenset[str] = frozenset(
    {
        "download_invoice_pdf",
        "make_invoice",
        "make_report",
        "auto_report",
        "product_image",
        "attached_documents",
        "export_list",
    }
)

#: The outputs of a saved operation, empty: what a test run returns for them.
OP_NEUTRAL: dict[str, None] = {
    "journal": None,
    "op_number": None,
    "series": None,
    "document": None,
}

UPLOAD_BINDING = "finvalda_upload"
DOWNLOAD_BINDING = "finvalda_file"
EXPORT_BINDING = "finvalda_export"

__all__ = [
    "ADVANCED_IDS",
    "DOWNLOAD_BINDING",
    "EXPORT_BINDING",
    "FILE_OUTPUT_IDS",
    "FRIENDLY_IDS",
    "FRIENDLY_READ_IDS",
    "FRIENDLY_WRITE_IDS",
    "OP_NEUTRAL",
    "OPERATION_IDS",
    "TEST_CONNECTION",
    "UPLOAD_BINDING",
    "WRITE_OPERATION_IDS",
    "WRITE_RUNNERS",
]
