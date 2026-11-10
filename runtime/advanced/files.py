"""Advanced file steps: PDFs, pictures, the document register, attachments and exports."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from datetime import date as Date
from typing import Any

from flowsteward_extension_sdk import (
    find_artifact_descriptor,
)

from .. import catalog, errors
from ..context import Context
from ..errors import ExtensionError, invalid
from ..fields import field
from ..inputs import _text, allowed, date_input, file_name_input, text_input, whole_input
from ..limits import (
    ARTIFACT_WRITE_TIMEOUT_SECONDS,
    DOWNLOAD_CONTENT_TYPE,
    EXPORT_CONTENT_TYPE,
    EXPORT_FILENAME,
    EXPORT_MAX_PAGES,
    EXPORT_PAGE,
    MAX_DOWNLOAD_BYTES,
    MAX_EXPORT_BYTES,
    MAX_UPLOAD_BYTES,
)
from ..reference_data import DESCRIPTION_TYPES
from ..rows import rest_of, rows_of
from ..transport import MIN_CALL_WINDOW_SECONDS, DeadlineReached, dumps_json
from ..values import lithuanian_today, safe_name
from .reads import _filter_value, descriptions_body, fin_user_of


def write_artifact(
    context: Context, chunks: list[bytes], *, binding_key: str, content_type: str, what: str
) -> str:
    try:
        written = context.artifact_writer(
            dict(context.payload),
            chunks,
            binding_key=binding_key,
            content_type=content_type,
            timeout_seconds=ARTIFACT_WRITE_TIMEOUT_SECONDS,
        )
    except ExtensionError:
        raise
    except Exception as exc:
        raise errors.no_effect(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, f"{what} could not be saved"
        ) from exc
    handle = _text(written.get("artifact_handle")) if isinstance(written, Mapping) else ""
    if not handle:
        raise errors.no_effect(errors.ARTIFACT_OUTPUT_UNAVAILABLE, f"{what} could not be saved")
    return handle


def file_contents(data: Mapping[str, Any], method: str) -> bytes:
    """The Base64 ``fileContents`` of a PDF or picture answer, decoded."""
    value = field(data, "fileContents")
    if value is None:
        value = field(data, "data")
    if not isinstance(value, str) or not value.strip():
        rows = rest_of(data, [])
        values = [v for v in (rows or {}).values() if isinstance(v, str) and len(v) > 40]
        value = values[0] if len(values) == 1 else None
    if not isinstance(value, str) or not value.strip():
        raise errors.no_effect(errors.INVALID_RESPONSE, f"Finvalda's {method} answer holds no file")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", value), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise errors.no_effect(
            errors.INVALID_RESPONSE, f"Finvalda's {method} file is not valid Base64"
        ) from exc
    if not raw:
        raise errors.no_effect(errors.INVALID_RESPONSE, f"Finvalda's {method} file is empty")
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise errors.no_effect(errors.RESPONSE_TOO_LARGE, "The file is larger than 40 MB")
    return raw


def save_file(context: Context, raw: bytes, name: str, *, what: str) -> dict[str, Any]:
    handle = write_artifact(
        context,
        [raw],
        binding_key=catalog.DOWNLOAD_BINDING,
        content_type=DOWNLOAD_CONTENT_TYPE,
        what=what,
    )
    return {"document_file": handle, "file_name": name or None, "size_bytes": len(raw)}


def invoice_param(inputs: Mapping[str, Any]) -> dict[str, Any]:
    form = text_input("invoice_form", inputs.get("invoice_form"), limit=30, required=True)
    journal = text_input("journal", inputs.get("journal"), limit=10)
    number = whole_input("op_number", inputs.get("op_number"), minimum=1, maximum=2_147_483_647)
    series = text_input("series", inputs.get("series"), limit=10)
    document = text_input("document", inputs.get("document"), limit=20)
    if not ((journal and number) or (series and document)):
        raise invalid("Give journal and op_number, or series and document, of the operation")
    return {
        "FakturosKodas": form,
        "sSerija": series,
        "sDokumentas": document,
        "sZurnalas": journal,
        "nNumeris": number or 0,
    }


def make_invoice_pdf(context: Context, inputs: Mapping[str, Any]) -> dict[str, Any]:
    allowed(inputs, {"invoice_form", "journal", "op_number", "series", "document"})
    param = invoice_param(inputs)
    reply = context.call(
        "MakeInvoice",
        verb="GET",
        query=[("sParam", dumps_json(param))],
        mutating=False,
    )
    raw = file_contents(reply.data, "MakeInvoice")
    name = (
        f"{param['sSerija']}{param['sDokumentas']}" or f"{param['sZurnalas']}-{param['nNumeris']}"
    )
    return save_file(context, raw, safe_name(name) + ".pdf", what="The invoice PDF")


def run_make_invoice(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    return make_invoice_pdf(context, context.operation_input)


def run_make_report(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"report_code", "filter"})
    code = text_input("report_code", inputs.get("report_code"), limit=40, required=True)
    raw_filter = inputs.get("filter") or {}
    if not isinstance(raw_filter, Mapping):
        raise invalid("filter must be an object")
    param: dict[str, Any] = {"Code": code}
    for key, value in raw_filter.items():
        canonical = next((k for k in spec["filters"] if k.lower() == str(key).lower()), None)
        if canonical is None or canonical == "Code":
            raise invalid(
                f"filter: {key} is not a report filter; use {', '.join(spec['filters'][1:])}"
            )
        if canonical in {"Goods", "Client"}:
            if not isinstance(value, Mapping) or len(value) > 20:
                raise invalid(f"filter.{canonical} must be an object")
            param[canonical] = {
                str(k): text_input(f"filter.{canonical}.{k}", v, limit=40) for k, v in value.items()
            }
        else:
            param[canonical] = _filter_value("filter", canonical, value)
    reply = context.call(
        "MakeReport",
        verb="GET",
        query=[("sParam", dumps_json(param))],
        mutating=False,
    )
    raw = file_contents(reply.data, "MakeReport")
    return save_file(context, raw, safe_name(code) + ".pdf", what="The report PDF")


def run_auto_reports_list(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    allowed(context.operation_input, set())
    reply = context.call("GetAutoReportsList", verb="GET", mutating=False)
    names = [errors.safe_text(n, 200) for n in rows_of(reply.data) if isinstance(n, str)]
    return {"names": names, "count": len(names)}


def run_auto_report(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"report_name"})
    name = text_input("report_name", inputs.get("report_name"), limit=200, required=True)
    reply = context.call("GetAutoReport", verb="GET", query=[("fileName", name)], mutating=False)
    return save_file(context, file_contents(reply.data, "GetAutoReport"), name, what="The report")


def run_product_image(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"product_code", "edited_since", "created_since"})
    code = text_input("product_code", inputs.get("product_code"), limit=20, required=True)
    query = [("sPreKod", code)]
    for name, wire in (("edited_since", "tKoregavimoData"), ("created_since", "tSukurimoData")):
        value = date_input(name, inputs.get(name))
        if value:
            query.append((wire, value))
    reply = context.call("GetPrekesImage", verb="GET", query=query, mutating=False)
    return save_file(
        context,
        file_contents(reply.data, "GetPrekesImage"),
        safe_name(code) + ".jpg",
        what="The picture",
    )


def read_input_file(context: Context, handle: Any) -> bytes:
    """A workflow file's bytes, size-checked before and while reading."""
    handle = text_input("document_file", handle, limit=300, required=True)
    declared = None
    try:
        descriptor = find_artifact_descriptor(dict(context.payload), artifact_id=handle)
    except Exception:
        descriptor = {}
    if isinstance(descriptor, Mapping):
        size = descriptor.get("size_bytes")
        declared = size if isinstance(size, int) and not isinstance(size, bool) else None
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise invalid("The file is larger than 8 MB")
    remaining = context.transport.remaining() - MIN_CALL_WINDOW_SECONDS
    if remaining <= 0:
        raise DeadlineReached
    try:
        body = context.artifact_reader(
            dict(context.payload),
            artifact_id=handle,
            timeout_seconds=min(ARTIFACT_WRITE_TIMEOUT_SECONDS, remaining),
        )
    except ExtensionError:
        raise
    except Exception as exc:
        raise errors.no_effect(
            errors.ARTIFACT_INPUT_UNAVAILABLE,
            "The file could not be read from Flow Steward storage",
        ) from exc
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise invalid("The file is empty")
    if len(body) > MAX_UPLOAD_BYTES:
        raise invalid("The file is larger than 8 MB")
    return bytes(body)


def _composition_date(value: str) -> dict[str, int]:
    day = Date.fromisoformat(value) if value else lithuanian_today()
    return {"year": day.year, "month": day.month, "day": day.day}


def insert_document_params(
    inputs: Mapping[str, Any], context: Context | None, content: bytes
) -> dict[str, Any]:
    info = inputs.get("info") or []
    if not isinstance(info, list) or len(info) > 3:
        raise invalid("info must be a list of up to three texts")
    return {
        "inParams": {
            "fileName": file_name_input("file_name", inputs.get("file_name")),
            "fileDescription": text_input(
                "file_description", inputs.get("file_description"), limit=200
            ),
            "regNr": text_input("registration_number", inputs.get("registration_number"), limit=50),
            "compositionDate": _composition_date(
                date_input("composition_date", inputs.get("composition_date"))
            ),
            "searchPhrase": text_input("search_phrase", inputs.get("search_phrase"), limit=200),
            "info": [text_input("info", i, limit=200) for i in info],
            "content": content.hex().upper(),
            "finUser": fin_user_of(context, inputs, required=True),
        }
    }


_INSERT_DOCUMENT_INPUTS = {
    "document_file",
    "file_name",
    "file_description",
    "registration_number",
    "composition_date",
    "search_phrase",
    "info",
    "fin_user",
}


def _validate_insert_document(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    allowed(inputs, _INSERT_DOCUMENT_INPUTS)
    text_input("document_file", inputs.get("document_file"), limit=300, required=True)
    insert_document_params(inputs, None, b"")


def run_insert_document(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    _validate_insert_document(spec, inputs, None)
    insert_document_params(inputs, context, b"")
    raw = read_input_file(context, inputs.get("document_file"))
    body = insert_document_params(inputs, context, raw)
    context.call("InsertDocument", verb="POST", body=body, mutating=True)
    return {"file_name": body["inParams"]["fileName"], "size_bytes": len(raw)}


def run_delete_document(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"file_name"})
    name = file_name_input("file_name", inputs.get("file_name"))
    context.call("DeleteDocument", verb="POST", body={"fileName": name}, mutating=True)
    return {"file_name": name}


def _validate_delete_document(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    allowed(inputs, {"file_name"})
    file_name_input("file_name", inputs.get("file_name"))


ENTITY_TYPES = frozenset({1, 2, 3, 7, 8, 16, 74, 75, 76, 77, 78, 97, 98})


def entity_query(inputs: Mapping[str, Any]) -> list[tuple[str, str]]:
    kind = whole_input("entity_type", inputs.get("entity_type"), minimum=1, maximum=1000)
    if kind not in ENTITY_TYPES:
        raise invalid(
            "entity_type must be one of the documented kinds: "
            + ", ".join(map(str, sorted(ENTITY_TYPES)))
        )
    query = [
        ("entityType", str(kind)),
        ("id1", text_input("entity_id1", inputs.get("entity_id1"), limit=20, required=True)),
    ]
    second = text_input("entity_id2", inputs.get("entity_id2"), limit=20)
    if second:
        query.append(("id2", second))
    return query


def attach_document_query(
    inputs: Mapping[str, Any], context: Context | None
) -> list[tuple[str, str]]:
    allowed(inputs, {"entity_type", "entity_id1", "entity_id2", "file_name", "fin_user"})
    query = entity_query(inputs)
    query.append(("documentId", file_name_input("file_name", inputs.get("file_name"))))
    user = fin_user_of(context, inputs, required=True)
    if user:
        query.append(("finUser", user))
    return query


def _validate_attach(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    attach_document_query(inputs, None)


#: AttachDocument errorCode 1: "the file is already attached to that entity" (manual 3.82).
ALREADY_ATTACHED = 1


def attach_document(context: Context, query: list[tuple[str, str]]) -> bool:
    """Attach; True when attached now, False when it was already there."""
    try:
        context.call("AttachDocument", verb="GET", query=query, mutating=True)
    except ExtensionError as exc:
        if exc.finvalda_code == ALREADY_ATTACHED and exc.definitely_no_external_effect:
            return False
        raise
    return True


def run_attach_document(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    query = attach_document_query(context.operation_input, context)
    attached = attach_document(context, query)
    return {"attached": attached, "already_attached": not attached}


def attached_files(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every attached file (name, hex data) in a GetAttachedDocument answer."""
    files: list[dict[str, Any]] = []
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, Mapping):
            name = field(node, "name")
            content = field(node, "data")
            if isinstance(name, str) and isinstance(content, str):
                files.append({"name": name, "data": content})
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    files.reverse()
    return files


_HEX = re.compile(r"[0-9A-Fa-f]*")


def _compact_hex(attached: Mapping[str, Any]) -> str:
    """An attached file's hex without spaces or line breaks; invalid_response when it
    is not hex. One reading for its content, its size and the comparison."""
    compact = "".join(str(attached["data"]).split())
    if len(compact) % 2 or not _HEX.fullmatch(compact):
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"The content of the attached file {errors.safe_text(attached['name'], 200)} in "
            "Finvalda's answer is not hex",
        )
    return compact


def attached_content(attached: Mapping[str, Any]) -> bytes:
    """The bytes of an attached file (hex in GetAttachedDocument's answer)."""
    return bytes.fromhex(_compact_hex(attached))


def same_content(attached: Mapping[str, Any], content: bytes) -> bool:
    """Whether an attached file holds exactly ``content``; sizes are compared before
    anything is decoded."""
    compact = _compact_hex(attached)
    if len(compact) // 2 != len(content):
        return False
    return bytes.fromhex(compact) == content


def attached_size(attached: Mapping[str, Any]) -> int:
    """An attached file's size in bytes, from its hex without spaces (invalid_response
    when it is not hex)."""
    return len(_compact_hex(attached)) // 2


def run_attached_documents(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"entity_type", "entity_id1", "entity_id2", "file_name"})
    query = entity_query(inputs)
    wanted = text_input("file_name", inputs.get("file_name"), limit=200)
    reply = context.call("GetAttachedDocument", verb="GET", query=query, mutating=False)
    files = attached_files(reply.data)
    listing = [
        {"name": errors.safe_text(f["name"], 200), "size_bytes": attached_size(f)} for f in files
    ]
    result: dict[str, Any] = {
        "files": listing,
        "count": len(listing),
        "document_file": None,
        "file_name": None,
        "size_bytes": 0,
    }
    if wanted:
        match = next((f for f in files if f["name"] == wanted), None)
        if match is None:
            raise errors.no_effect(errors.NOT_FOUND, f"No attached file is named {wanted}")
        compact = _compact_hex(match)
        if len(compact) // 2 > MAX_DOWNLOAD_BYTES:
            raise errors.no_effect(errors.RESPONSE_TOO_LARGE, "The file is larger than 40 MB")
        raw = bytes.fromhex(compact)
        result.update(save_file(context, raw, wanted, what="The attached file"))
    return result


def run_export(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"list_type", "filter", "columns", "start_page", "max_pages"})
    kind = text_input("list_type", inputs.get("list_type"), limit=40, required=True)
    if kind not in DESCRIPTION_TYPES:
        raise invalid(f"list_type must be one of: {', '.join(sorted(DESCRIPTION_TYPES))}")
    groups = DESCRIPTION_TYPES[kind]["filters"]
    sub = {k: inputs[k] for k in ("filter", "columns") if k in inputs}
    descriptions_body(kind, groups, sub, page=1, limit=1)
    max_pages = (
        whole_input(
            "max_pages",
            inputs.get("max_pages"),
            default=EXPORT_MAX_PAGES,
            minimum=1,
            maximum=EXPORT_MAX_PAGES,
        )
        or 1
    )
    page = (
        whole_input("start_page", inputs.get("start_page"), default=1, minimum=1, maximum=1_000_000)
        or 1
    )
    chunks: list[bytes] = []
    size = count = fetched = 0
    complete = False
    stopped: str | None = None
    while fetched < max_pages:
        try:
            reply = context.call(
                "GetDescriptions",
                verb="POST",
                body=descriptions_body(kind, groups, sub, page=page, limit=EXPORT_PAGE),
                mutating=False,
            )
        except DeadlineReached:
            if fetched == 0:
                raise
            stopped = "The step ran out of time"
            break
        except ExtensionError as exc:
            if fetched == 0:
                raise
            stopped = f"{exc.code}: {exc.message}"
            break
        rows = rows_of(reply.data)
        page_chunks = [(dumps_json(row) + "\n").encode("utf-8") for row in rows]
        page_bytes = sum(len(c) for c in page_chunks)
        if size + page_bytes > MAX_EXPORT_BYTES:
            if fetched == 0:
                raise errors.no_effect(
                    errors.RESPONSE_TOO_LARGE, "One page exceeds the 200 MB export limit"
                )
            stopped = "The export reached 200 MB"
            break
        fetched += 1
        chunks.extend(page_chunks)
        size += page_bytes
        count += len(rows)
        page += 1
        if len(rows) < EXPORT_PAGE:
            complete = True
            break
    handle = write_artifact(
        context,
        chunks,
        binding_key=catalog.EXPORT_BINDING,
        content_type=EXPORT_CONTENT_TYPE,
        what="The export file",
    )
    return {
        "artifact_handle": handle,
        "filename": EXPORT_FILENAME,
        "content_type": EXPORT_CONTENT_TYPE,
        "record_count": count,
        "pages_fetched": fetched,
        "complete": complete,
        "resume_page": None if complete else page,
        "stopped_by": stopped,
    }
