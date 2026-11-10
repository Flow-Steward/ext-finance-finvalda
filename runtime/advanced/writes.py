"""Advanced write steps: records, operations, locks, journals, copies and raw calls."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .. import budget
from ..context import Context
from ..errors import invalid
from ..fields import field
from ..inputs import _QUERY_NAME_RE, allowed, flag_input, text_input, whole_input
from ..limits import MAX_LIST_ITEMS
from ..records import check_fields
from ..reference_data import CALLABLE_METHODS, CLASSES
from ..rows import op_result
from ..values import plain
from ..transport import WRITE_METHODS, dumps_json


def _item_parameter(context: Context | None, inputs: Mapping[str, Any]) -> str:
    given = text_input("item_parameter", inputs.get("item_parameter"), limit=100)
    if given:
        return given
    return context.connection.item_parameter if context is not None else ""


def _item_class(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    cls = text_input("item_class", inputs.get("item_class"), limit=40, required=True)
    if cls not in spec["classes"]:
        raise invalid(f"item_class must be one of: {', '.join(spec['classes'])}")
    return cls


def item_write_body(
    spec: Mapping[str, Any], inputs: Mapping[str, Any], context: Context | None
) -> tuple[str, dict[str, Any], str]:
    inserting = spec["method"] == "InsertNewItem"
    allowed(
        inputs, {"item_class", "fields", "item_parameter"} | (set() if inserting else {"item_code"})
    )
    cls = _item_class(spec, inputs)
    free_form = cls not in CLASSES
    fields = check_fields(
        cls, inputs.get("fields"), enforce_required=inserting and not free_form, free_form=free_form
    )
    if not fields:
        raise invalid("fields must name at least one field")
    parameter = _item_parameter(context, inputs)
    table = {f["name"] for f in CLASSES.get(cls, [])}
    if "sFvsImportoParametras" in table and "sFvsImportoParametras" not in fields and parameter:
        fields = {"sFvsImportoParametras": parameter, **fields}
    code = str(fields.get("sKodas") or fields.get("code") or "")
    body: dict[str, Any] = {
        "ItemClassName": cls,
        "xmlstring": dumps_json({cls: fields} if not free_form else fields),
    }
    if not inserting:
        item_code = text_input(
            "item_code", inputs.get("item_code"), limit=20, required=cls != "Fvs.Adresas"
        )
        if item_code:
            body["sItemCode"] = item_code
            code = item_code
    return cls, body, code


def _validate_item_write(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    item_write_body(spec, inputs, None)


def run_item_write(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    cls, body, code = item_write_body(spec, context.operation_input, context)
    context.call(spec["method"], verb="POST", body=body, mutating=True)
    return {
        "item_class": cls,
        "code": code or None,
        "saved": True,
    }


def props_body(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    allowed(inputs, {"changes"})
    changes = inputs.get("changes")
    if not isinstance(changes, Mapping) or not changes:
        raise invalid("changes must be an object with Fvs.Prekes, Fvs.Paslaugos or Fvs.Klientas")
    groups: dict[str, Any] = {}
    for cls, values in changes.items():
        if cls not in spec["classes"]:
            raise invalid(f"changes: {cls} is not one of {', '.join(spec['classes'])}")
        if not isinstance(values, Mapping):
            raise invalid(f"changes.{cls} must be an object")
        codes = values.get("Kodas")
        codes = codes if isinstance(codes, list) else [codes] if codes else []
        if not codes or len(codes) > MAX_LIST_ITEMS:
            raise invalid(f"changes.{cls}.Kodas must list 1 to {MAX_LIST_ITEMS} codes")
        rest = {k: v for k, v in values.items() if k != "Kodas"}
        if not rest:
            raise invalid(f"changes.{cls} must set at least one value")
        checked = check_fields(cls, rest)
        checked["Kodas"] = [text_input(f"changes.{cls}.Kodas", c, limit=20) for c in codes]
        groups[cls] = checked
    return {"xmlstring": dumps_json({"Fvs.EditItemProps": groups})}


def _validate_props(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    props_body(spec, inputs)


def run_props_write(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    body = props_body(spec, context.operation_input)
    context.call("EditItemProps", verb="POST", body=body, mutating=True)
    return {"saved": True}


def delete_item_body(
    spec: Mapping[str, Any], inputs: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    allowed(inputs, {"item_class", "item_code"})
    cls = _item_class(spec, inputs)
    code = text_input("item_code", inputs.get("item_code"), limit=20, required=True)
    return cls, code, {"input": {"ItemClassName": cls, "Code": code}}


def _validate_delete_item(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    delete_item_body(spec, inputs)


def run_delete_item(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    cls, code, body = delete_item_body(spec, context.operation_input)
    context.call("DeleteItem", verb="POST", body=body, mutating=True)
    return {
        "item_class": cls,
        "code": code,
        "saved": True,
    }


_GROUP_CLASSES = {
    "Fvs.Preke",
    "Fvs.Paslauga",
    "Fvs.Klientas",
    "Fvs.Adresas",
    "Fvs.ObjektasI",
    "Fvs.ObjektasII",
    "Fvs.ObjektasIII",
    "Fvs.ObjektasIV",
    "Fvs.ObjektasV",
    "Fvs.ObjektasVI",
    "Fvs.Sandelis",
}


def append_group_body(inputs: Mapping[str, Any]) -> dict[str, Any]:
    allowed(inputs, {"item_class", "group_code", "item_code", "item_code2"})
    cls = text_input("item_class", inputs.get("item_class"), limit=40, required=True)
    if cls not in _GROUP_CLASSES:
        raise invalid(f"item_class must be one of: {', '.join(sorted(_GROUP_CLASSES))}")
    body = {
        "ItemClassName": cls,
        "sGroupCode": text_input("group_code", inputs.get("group_code"), limit=20, required=True),
        "sItemCode": text_input("item_code", inputs.get("item_code"), limit=20, required=True),
    }
    second = text_input("item_code2", inputs.get("item_code2"), limit=20)
    if cls == "Fvs.Adresas" and not second:
        raise invalid("An address group needs item_code2, the address's client code")
    if second:
        body["sItemCode2"] = second
    return body


def _validate_append(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    append_group_body(inputs)


def run_append_group(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    context.call(
        "AppendGroup", verb="POST", body=append_group_body(context.operation_input), mutating=True
    )
    return {"saved": True}


def import_parameter(
    context: Context | None, inputs: Mapping[str, Any], *, required: bool = True
) -> str:
    given = text_input("import_parameter", inputs.get("import_parameter"), limit=100)
    if given:
        return given
    default = context.connection.operation_parameter if context is not None else ""
    if not default and required and context is not None:
        raise invalid(
            "Give import_parameter, or set the operation import parameter on the connection: "
            "Finvalda needs it for every operation"
        )
    return default


def operation_body(
    spec: Mapping[str, Any], inputs: Mapping[str, Any], context: Context | None
) -> tuple[str, dict[str, Any]]:
    allowed(inputs, {"op_class", "operation", "import_parameter"})
    classes: Mapping[str, list[str]] = spec["classes"]
    cls = text_input("op_class", inputs.get("op_class"), limit=40, required=True)
    if cls not in classes and cls not in spec.get("aliases", {}):
        raise invalid(f"op_class must be one of: {', '.join(sorted(classes))}")
    table_class = spec.get("aliases", {}).get(cls, cls)
    lines = {name: True for name in classes.get(table_class, [])}
    inserting = spec["method"] == "InsertNewOperation"
    operation = check_fields(
        table_class, inputs.get("operation"), lines=lines, enforce_required=inserting
    )
    if not inserting and ("sZurnalas" not in operation or "nNumeris" not in operation):
        raise invalid("operation must give sZurnalas and nNumeris of the operation to change")
    parameter = import_parameter(context, inputs)
    return cls, {
        "ItemClassName": cls,
        "sParametras": parameter,
        "xmlstring": dumps_json({cls: operation}),
    }


def _validate_operation_write(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    operation_body(spec, inputs, None)


def run_operation_write(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    _cls, body = operation_body(spec, context.operation_input, context)
    reply = context.call(spec["method"], verb="POST", body=body, mutating=True)
    return op_result(reply.data)


def journal_number(inputs: Mapping[str, Any]) -> tuple[str, int]:
    journal = text_input("journal", inputs.get("journal"), limit=10, required=True)
    number = whole_input("op_number", inputs.get("op_number"), minimum=1, maximum=2_147_483_647)
    if number is None:
        raise invalid("Required input missing: op_number")
    return journal, number


_DELETE_CLASSES = frozenset(
    {
        "DelPirkDok",
        "DelPirkUzsDok",
        "DelPirkGrazDok",
        "DelPardDok",
        "DelPardRezDok",
        "DelPardGrazDok",
        "DelVidPerkDok",
        "DelIplDok",
        "DelUVMPirkUzsDok",
        "DelGamybaDok",
    }
)


def delete_operation_body(inputs: Mapping[str, Any], context: Context | None) -> dict[str, Any]:
    allowed(inputs, {"op_class", "journal", "op_number", "import_parameter"})
    cls = text_input("op_class", inputs.get("op_class"), limit=40, required=True)
    if cls not in _DELETE_CLASSES:
        raise invalid(f"op_class must be one of: {', '.join(sorted(_DELETE_CLASSES))}")
    journal, number = journal_number(inputs)
    return {
        "ItemClassName": cls,
        "sParametras": import_parameter(context, inputs),
        "xmlstring": dumps_json({cls: {"sZurnalas": journal, "nNumeris": number}}),
    }


def _validate_delete_operation(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    delete_operation_body(inputs, None)


def run_delete_operation(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    body = delete_operation_body(context.operation_input, context)
    reply = context.call("DeleteOperation", verb="POST", body=body, mutating=True)
    return op_result(reply.data)


def lock_body(inputs: Mapping[str, Any], context: Context | None) -> dict[str, Any]:
    allowed(inputs, {"journal", "op_number", "import_parameter"})
    journal, number = journal_number(inputs)
    body: dict[str, Any] = {"sZurnalas": journal, "nNumeris": number}
    parameter = import_parameter(context, inputs, required=False)
    if parameter:
        body["sParametras"] = parameter
    return body


def _validate_lock(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    lock_body(inputs, None)


def run_lock(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    context.call(
        spec["method"], verb="POST", body=lock_body(context.operation_input, context), mutating=True
    )
    return {"saved": True}


def run_is_locked(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"journal", "op_number"})
    journal, number = journal_number(inputs)
    reply = context.call(
        "IsOperationLocked",
        verb="POST",
        body={"sZurnalas": journal, "nNumeris": number},
        mutating=False,
    )
    state = field(reply.data, "IsLocked")
    try:
        lock_state = int(state) if state is not None and not isinstance(state, bool) else None
    except (TypeError, ValueError):
        lock_state = None
    return {"locked": None if lock_state is None else lock_state != 0, "lock_state": lock_state}


def _validate_is_locked(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    allowed(inputs, {"journal", "op_number"})
    journal_number(inputs)


def change_journal_body(inputs: Mapping[str, Any]) -> dict[str, Any]:
    allowed(inputs, {"journal", "op_number", "new_journal"})
    journal, number = journal_number(inputs)
    return {
        "sJournal": journal,
        "nOpNumber": number,
        "sJournalNew": text_input(
            "new_journal", inputs.get("new_journal"), limit=10, required=True
        ),
    }


def _validate_change_journal(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    change_journal_body(inputs)


def run_change_journal(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    reply = context.call(
        "ChangeJournal",
        verb="POST",
        body=change_journal_body(context.operation_input),
        mutating=True,
    )
    return op_result(reply.data)


def copy_body(inputs: Mapping[str, Any], context: Context | None) -> dict[str, Any]:
    allowed(
        inputs,
        {
            "journal",
            "op_number",
            "new_journal",
            "delete_source",
            "keep_document",
            "new_document",
            "new_series",
            "copy_doc_to_description",
            "import_parameter",
        },
    )
    journal, number = journal_number(inputs)
    body: dict[str, Any] = {
        "sParameter": import_parameter(context, inputs),
        "sJournal": journal,
        "nOpNumber": number,
        "sJournalNew": text_input(
            "new_journal", inputs.get("new_journal"), limit=10, required=True
        ),
        "bDeleteSourceOp": flag_input("delete_source", inputs.get("delete_source")),
        "bKeepDocument": flag_input("keep_document", inputs.get("keep_document")),
    }
    document = text_input("new_document", inputs.get("new_document"), limit=20)
    series = text_input("new_series", inputs.get("new_series"), limit=10)
    if document:
        body["sNewDocument"] = document
    if series:
        body["sNewSeries"] = series
    target = whole_input(
        "copy_doc_to_description", inputs.get("copy_doc_to_description"), minimum=0, maximum=5
    )
    if target is not None:
        body["nCopyDocToDescription"] = target
    return {"input": body}


def _validate_copy(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    copy_body(inputs, None)


def run_copy_operation(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    reply = context.call(
        "CopyOperation",
        verb="POST",
        body=copy_body(context.operation_input, context),
        mutating=True,
    )
    return op_result(reply.data)


def call_request(
    inputs: Mapping[str, Any],
) -> tuple[str, str, list[tuple[str, str]], dict[str, Any] | None]:
    allowed(inputs, {"method", "verb", "query", "body"})
    method = text_input("method", inputs.get("method"), limit=80, required=True)
    if method not in CALLABLE_METHODS:
        raise invalid(
            f"{method} is not a method Finvalda documents; see the Coverage page for the list"
        )
    verb = text_input("verb", inputs.get("verb"), limit=4, required=True).upper()
    if verb not in {"GET", "POST"}:
        raise invalid("verb must be GET or POST")
    raw_query = inputs.get("query") or {}
    if not isinstance(raw_query, Mapping) or len(raw_query) > 40:
        raise invalid("query must be an object of at most 40 parameters")
    query: list[tuple[str, str]] = []
    for key, value in raw_query.items():
        name = str(key).strip()
        if not _QUERY_NAME_RE.fullmatch(name):
            raise invalid("query parameter names are letters, digits and _ only")
        if isinstance(value, (Mapping, list)):
            rendered = dumps_json(value)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, Decimal):
            rendered = plain(value) if value.is_finite() else str(value)
        elif isinstance(value, (int, float, str)):
            rendered = str(value)
        else:
            raise invalid(f"query.{name} has a value of the wrong kind")
        if len(rendered) > 20_000 or not rendered.isprintable():
            raise invalid(f"query.{name} is too long or not printable")
        query.append((name, rendered))
    body = inputs.get("body")
    if body is not None and not isinstance(body, Mapping):
        raise invalid("body must be a JSON object")
    if body is not None and verb == "GET":
        raise invalid("A GET call has no body; use query, or POST")
    return method, verb, query, dict(body) if body is not None else None


def _validate_call(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    call_request(inputs)


def run_call_method(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    method, verb, query, body = call_request(context.operation_input)
    # Only the methods that change Finvalda are sent as writes (one attempt, a
    # confirmation required); the documented reads are plain reads.
    reply = context.call(
        method, verb=verb, query=query, body=body, mutating=method in WRITE_METHODS
    )
    result: dict[str, Any] = {"method": method, "response": reply.data}
    if not budget.fits(result):
        result["response"] = None
    return result
