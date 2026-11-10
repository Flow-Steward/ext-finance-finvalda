"""Attach file, Download invoice PDF."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..advanced.files import (
    attach_document,
    attached_files,
    same_content,
    entity_query,
    fin_user_of,
    insert_document_params,
    make_invoice_pdf,
    read_input_file,
)
from .. import errors
from ..errors import ExtensionError
from ..inputs import file_name_input
from .common import Progress

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
    generated_name: bool = False,
) -> dict[str, Any]:
    """Store a workflow file in the register and attach it, unless the same file (same
    name, same content) is already attached; another file of that name is a conflict.

    Every call is guarded, so a failure after an earlier write names what was saved.
    InsertDocument documents no "already stored" code (manual 3.80: 1 no name,
    2 unknown employee, 3 no content) and the register cannot be listed, so a
    refused store is never taken for a stored file: it is reported as it is, with a
    hint for the case of a file stored by an earlier run.
    """
    query = entity_query(inputs)
    name = file_name_input("file_name", inputs.get("file_name"))
    user = fin_user_of(context, {}, required=True)
    reply = progress.guard(
        "Reading the attached files",
        lambda: context.call("GetAttachedDocument", verb="GET", query=query, mutating=False),
    )
    if content is None:
        content = read_input_file(context, inputs.get("document_file"))
    same_name = [f for f in attached_files(reply.data) if f["name"] == name]
    if same_name:
        # Already attached only when the content is the same, byte for byte. Any
        # failure here goes through progress, so a write made earlier in this step
        # (an invoice) is named, not hidden.
        step = "Checking the attached file"
        if progress.guard(step, lambda: any(same_content(f, content) for f in same_name)):
            return {"file_name": name, "stored": False, "attached": False}
        hint = (
            "replace it in Finvalda by hand, or attach the new file under another name with "
            "the Attach a document step"
            if generated_name
            else "give the new file another name, or replace the attached one in Finvalda"
        )
        raise progress.fail(
            errors.no_effect(
                errors.CONFLICT,
                f"A different file named {name} is already attached there, so the new file "
                f"was not stored or attached; {hint}",
            ),
            step,
        )
    document_inputs = {"file_name": name}
    if inputs.get("file_description"):
        document_inputs["file_description"] = inputs["file_description"]
    body = insert_document_params(document_inputs, context, content)
    attach_query = [*query, ("documentId", name), ("finUser", user)]
    try:
        progress.guard(
            "Storing the file",
            lambda: context.call("InsertDocument", verb="POST", body=body, mutating=True),
        )
    except ExtensionError as refused:
        # Finvalda's own refusal (nothing saved): passed on with a hint. A network
        # failure or a timeout is passed on as it is.
        if refused.definitely_no_external_effect is not True:
            raise
        raise ExtensionError(
            refused.code,
            f"{refused.message}. If an earlier run already stored {name} in the register, "
            "attach it with the Attach a document step",
            failure_class=refused.failure_class,
            retryable=refused.retryable,
            definitely_no_external_effect=refused.definitely_no_external_effect,
            external_effect_status=refused.external_effect_status,
            http_status=refused.http_status,
            finvalda_code=refused.finvalda_code,
        ) from refused
    progress.add(f"document {name} in the register")
    attached = progress.guard(
        "Attaching the document", lambda: attach_document(context, attach_query)
    )
    return {"file_name": name, "stored": True, "attached": attached}


def attach_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    return store_and_attach(context, inputs, progress)


def download_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    return make_invoice_pdf(context, inputs)
