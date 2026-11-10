"""Attach file, Download invoice PDF."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import errors
from ..advanced.files import (
    attach_document,
    attached_files,
    entity_query,
    fin_user_of,
    insert_document_params,
    make_invoice_pdf,
    read_input_file,
)
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
    query = entity_query(inputs)
    name = file_name_input("file_name", inputs.get("file_name"))
    user = fin_user_of(context, {}, required=True)
    reply = progress.guard(
        "Reading the attached files",
        lambda: context.call("GetAttachedDocument", verb="GET", query=query, mutating=False),
    )
    if any(f["name"] == name for f in attached_files(reply.data)):
        return {"file_name": name, "stored": False, "attached": False}
    if content is None:
        content = read_input_file(context, inputs.get("document_file"))
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
                lambda: attach_document(context, attach_query),
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
        "Attaching the document", lambda: attach_document(context, attach_query)
    )
    return {"file_name": name, "stored": True, "attached": attached}


def attach_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    return store_and_attach(context, inputs, progress)


def download_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    return make_invoice_pdf(context, inputs)
