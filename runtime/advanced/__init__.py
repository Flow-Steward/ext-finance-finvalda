"""The Advanced steps' runners and validators, one per runner kind."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .. import catalog
from ..context import Context
from .files import (
    _validate_attach,
    _validate_delete_document,
    _validate_insert_document,
    run_attach_document,
    run_attached_documents,
    run_auto_report,
    run_auto_reports_list,
    run_delete_document,
    run_export,
    run_insert_document,
    run_make_invoice,
    run_make_report,
    run_product_image,
)
from .reads import (
    _validate_descriptions,
    _validate_method_read,
    _validate_operations,
    run_descriptions,
    run_method_read,
    run_operations,
    run_recommended_price,
    run_related_invoices,
    run_user_permissions,
)
from .writes import (
    _validate_append,
    _validate_call,
    _validate_change_journal,
    _validate_copy,
    _validate_delete_item,
    _validate_delete_operation,
    _validate_is_locked,
    _validate_item_write,
    _validate_lock,
    _validate_operation_write,
    _validate_props,
    run_append_group,
    run_call_method,
    run_change_journal,
    run_copy_operation,
    run_delete_item,
    run_delete_operation,
    run_is_locked,
    run_item_write,
    run_lock,
    run_operation_write,
    run_props_write,
)

OP_NEUTRAL = catalog.OP_NEUTRAL

NEUTRAL_OUTPUTS: dict[str, dict[str, Any]] = {
    "item_write": {"item_class": "", "code": None, "finvalda_result": None},
    "props_write": {"finvalda_result": None},
    "delete_item": {"item_class": "", "code": "", "finvalda_result": None},
    "append_group": {"finvalda_result": None},
    "operation_write": dict(OP_NEUTRAL),
    "delete_operation": dict(OP_NEUTRAL),
    "lock": {"finvalda_result": None},
    "change_journal": dict(OP_NEUTRAL),
    "copy_operation": dict(OP_NEUTRAL),
    "insert_document": {"file_name": "", "size_bytes": 0},
    "delete_document": {"file_name": ""},
    "attach_document": {"attached": False, "already_attached": False},
    "call_method": {"method": "", "response": None},
}


def _no_validation(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    return None


RUNNERS: dict[str, Callable[[Context, Mapping[str, Any]], dict[str, Any]]] = {
    "method_read": run_method_read,
    "descriptions": run_descriptions,
    "operations": run_operations,
    "item_write": run_item_write,
    "props_write": run_props_write,
    "delete_item": run_delete_item,
    "append_group": run_append_group,
    "operation_write": run_operation_write,
    "delete_operation": run_delete_operation,
    "lock": run_lock,
    "is_locked": run_is_locked,
    "change_journal": run_change_journal,
    "copy_operation": run_copy_operation,
    "make_invoice": run_make_invoice,
    "make_report": run_make_report,
    "auto_reports_list": run_auto_reports_list,
    "auto_report": run_auto_report,
    "product_image": run_product_image,
    "insert_document": run_insert_document,
    "delete_document": run_delete_document,
    "attach_document": run_attach_document,
    "attached_documents": run_attached_documents,
    "recommended_price": run_recommended_price,
    "user_permissions": run_user_permissions,
    "related_invoices": run_related_invoices,
    "export": run_export,
    "call_method": run_call_method,
}

VALIDATORS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any], Any], None]] = {
    "method_read": _validate_method_read,
    "descriptions": _validate_descriptions,
    "operations": _validate_operations,
    "item_write": _validate_item_write,
    "props_write": _validate_props,
    "delete_item": _validate_delete_item,
    "append_group": _validate_append,
    "operation_write": _validate_operation_write,
    "delete_operation": _validate_delete_operation,
    "lock": _validate_lock,
    "is_locked": _validate_is_locked,
    "change_journal": _validate_change_journal,
    "copy_operation": _validate_copy,
    "insert_document": _validate_insert_document,
    "delete_document": _validate_delete_document,
    "attach_document": _validate_attach,
    "call_method": _validate_call,
}
