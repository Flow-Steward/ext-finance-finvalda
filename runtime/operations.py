"""Run one workflow operation against the Finvalda web service."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from typing import Any

from flowsteward_extension_sdk import (
    find_artifact_descriptor,
    read_artifact_bytes,
    write_artifact_stream,
)

from . import budget, catalog, errors, friendly
from .connection import Connection, connection_from_payload
from .errors import ExtensionError, error_response, invalid
from .fields import field
from .reference_data import CALLABLE_METHODS, CLASSES, DESCRIPTION_TYPES, OPERATIONS
from .transport import MIN_CALL_WINDOW_SECONDS, DeadlineReached, FinvaldaTransport, Reply

#: The entrypoint is given 600 s (extension.yaml). Every request must start early
#: enough to finish inside it; a step that saves a file keeps the artifact write's
#: own timeout in reserve, so it can always save what it read.
STEP_TIMEOUT_SECONDS = 600.0
STEP_MARGIN_SECONDS = 30.0
ARTIFACT_WRITE_TIMEOUT_SECONDS = 120.0
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000
DEFAULT_MAX_PAGES = 10
MAX_PAGES = 100
EXPORT_PAGE = 1000
EXPORT_MAX_PAGES = 2000
#: Files travel hex-encoded (two characters a byte) inside one JSON body of at most
#: 20 MB (transport.MAX_REQUEST_BODY_BYTES), so 8 MB is the most that fits.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
MAX_EXPORT_BYTES = 200 * 1024 * 1024
MAX_TEXT = 1000
MAX_LIST_ITEMS = 1000

#: Must equal the content types granted in contracts/artifact_policies.yaml.
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"
EXPORT_CONTENT_TYPE = "application/x-ndjson"
EXPORT_FILENAME = "finvalda-export.jsonl"

_ALLOWED_ACTION_KEYS = frozenset(
    {"action_id", "operation_id", "page_id", "component_id", "context", "input", "target"}
)
_DIGITS = re.compile(r"-?[0-9]{1,10}")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_TIME_RE = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?$")
_COLUMN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_NUMBER_RE = re.compile(r"^-?\d{1,15}(\.\d{1,8})?$")
#: "Numeric(14,2)": at most 14 digits, 2 of them after the point.
_NUMERIC_SIZE = re.compile(r"^numeric\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_QUERY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_FILE_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,200}$")
#: Filter values that are lists in the manual's examples.
_LIST_FILTERS = frozenset({"Codes", "Products", "BarCodes"})
#: Fields kept when a write's echo is shrunk to fit the step output.
_KEY_FIELDS = re.compile(
    r"(code|kodas|journal|zurnalas|number|numeris|series|serija|document|dokumentas)", re.I
)

TransportFactory = Callable[[Connection], FinvaldaTransport]
ArtifactWriter = Callable[..., Mapping[str, Any]]


# ---------------------------------------------------------------------------
# Entry point


def handle_runtime(
    payload: Mapping[str, Any],
    *,
    transport_factory: TransportFactory = FinvaldaTransport,
    artifact_reader: Callable[..., bytes] = read_artifact_bytes,
    artifact_writer: ArtifactWriter = write_artifact_stream,
) -> dict[str, Any]:
    """Dispatch one invocation through the closed registry."""
    transport: FinvaldaTransport | None = None
    try:
        operation_id, raw_input = _envelope(payload)
        if operation_id not in catalog.OPERATION_IDS:
            raise errors.no_effect(
                errors.UNSUPPORTED_OPERATION, "That operation is not part of this extension"
            )
        operation_input = _as_input(raw_input)
        write = operation_id in catalog.WRITE_OPERATION_IDS
        if write and _test_mode(payload):
            # The host suppresses test-mode writes before spawning the extension;
            # this is the backstop. Validated first, then nothing is sent.
            validate_only(operation_id, operation_input)
            return _ok(neutral_result(operation_id))
        connection = connection_from_payload(
            payload, connection_ref=_text(operation_input.get("connection_ref"))
        )
        transport = transport_factory(connection)
        reserve = ARTIFACT_WRITE_TIMEOUT_SECONDS if operation_id in catalog.FILE_OUTPUT_IDS else 0.0
        transport.deadline = (
            transport.clock() + STEP_TIMEOUT_SECONDS - STEP_MARGIN_SECONDS - reserve
        )
        context = Context(
            payload=payload,
            operation_input=operation_input,
            transport=transport,
            artifact_reader=artifact_reader,
            artifact_writer=artifact_writer,
        )
        if operation_id in catalog.FRIENDLY_IDS:
            result = friendly.run(context, operation_id)
            if operation_id in catalog.FRIENDLY_WRITE_IDS:
                return _ok(mark_write(fit_write_result(result)))
            budget.ensure_fits(result)
            return _ok(result)
        spec = OPERATIONS[operation_id]
        result = RUNNERS[spec["runner"]](context, spec)
        if operation_id in catalog.WRITE_OPERATION_IDS:
            return _ok(mark_write(fit_write_result(result)))
        budget.ensure_fits(result)
        return _ok(result)
    except DeadlineReached:
        if transport is not None and transport.mutated:
            return error_response(
                errors.TIMEOUT_UNKNOWN,
                "The step ran out of time after a change was sent to Finvalda; check Finvalda "
                "before running it again",
            )
        return error_response(
            errors.TIMEOUT,
            "The step ran out of time before Finvalda could be called",
            retry_facts={"definitely_no_external_effect": True, "external_effect_status": "failed"},
        )
    except ExtensionError as exc:
        _mark_unsent(exc, transport)
        if (
            transport is not None
            and transport.committed
            and exc.definitely_no_external_effect is not False
        ):
            # Backstop: once Finvalda confirmed a change in this step, no later
            # failure may report "nothing changed".
            exc = ExtensionError(
                exc.code if exc.code != errors.INVALID_PAYLOAD else errors.INTERNAL_ERROR,
                f"{exc.message}. A change was already saved in Finvalda by this step; check "
                "Finvalda before running it again.",
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=False,
                external_effect_status="timeout_unknown",
                http_status=exc.http_status,
                finvalda_code=exc.finvalda_code,
            )
        return error_response(
            exc.code, exc.message, retry_facts=exc.retry_facts(), finvalda_code=exc.finvalda_code
        )
    except Exception:
        response = error_response(errors.INTERNAL_ERROR, "The operation could not be completed")
        if transport is None or not transport.mutated:
            response["definitely_no_external_effect"] = True
            response["external_effect_status"] = "failed"
        else:
            response["definitely_no_external_effect"] = False
            response["external_effect_status"] = "timeout_unknown"
        return response


def _mark_unsent(exc: ExtensionError, transport: FinvaldaTransport | None) -> None:
    """A failure before any change could be sent cannot have had an effect."""
    if transport is not None and transport.mutated:
        return
    if exc.definitely_no_external_effect is None:
        exc.definitely_no_external_effect = True
    if exc.definitely_no_external_effect and exc.external_effect_status is None:
        exc.external_effect_status = "failed"


class Context:
    """One invocation: its input, its connection's transport and the artifact plane."""

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        operation_input: Mapping[str, Any],
        transport: FinvaldaTransport,
        artifact_reader: Callable[..., bytes],
        artifact_writer: ArtifactWriter,
    ) -> None:
        self.payload = payload
        self.operation_input = operation_input
        self.transport = transport
        self.artifact_reader = artifact_reader
        self.artifact_writer = artifact_writer

    @property
    def connection(self) -> Connection:
        return self.transport.connection

    def clock(self) -> float:
        clock = getattr(self.transport, "clock", None)
        return float(clock()) if callable(clock) else time.monotonic()

    def call(
        self,
        method: str,
        *,
        verb: str,
        query: list[tuple[str, str]] | None = None,
        body: Mapping[str, Any] | None = None,
        mutating: bool,
        language: str | None = None,
    ) -> Reply:
        return self.transport.request(
            method, verb=verb, query=query or [], body=body, mutating=mutating, language=language
        )


# ---------------------------------------------------------------------------
# Envelope and inputs


def _envelope(payload: Mapping[str, Any]) -> tuple[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("mode") != "action":
        raise invalid("Request mode must be action")
    action = payload.get("action")
    if not isinstance(action, Mapping) or set(action) - _ALLOWED_ACTION_KEYS:
        raise invalid("The action envelope is invalid")
    operation_id = action.get("action_id") or action.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise invalid("An operation id is required")
    return operation_id.strip(), action.get("input")


def _as_input(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise invalid("The operation input must be an object")
    return {str(k): v for k, v in raw.items() if v is not None and v != ""}


def _test_mode(payload: Mapping[str, Any]) -> bool:
    context = payload.get("runtime_context")
    if not isinstance(context, Mapping):
        return False
    value = context.get("test_mode", False)
    if not isinstance(value, bool):
        raise invalid("runtime_context.test_mode must be boolean")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def text_input(name: str, value: Any, *, limit: int = 200, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise invalid(f"Required input missing: {name}")
        return ""
    if not isinstance(value, str):
        raise invalid(f"{name} must be text")
    text = value.strip()
    if len(text) > limit or not text.isprintable():
        raise invalid(f"{name} must be printable text of at most {limit} characters")
    if required and not text:
        raise invalid(f"Required input missing: {name}")
    return text


def whole_input(
    name: str, value: Any, *, default: int | None = None, minimum: int, maximum: int
) -> int | None:
    if value is None or value == "":
        return default
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise invalid(f"{name} must be a whole number from {minimum} to {maximum}")
    return value


def flag_input(name: str, value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise invalid(f"{name} must be true or false")
    return value


def date_input(name: str, value: Any, *, required: bool = False) -> str:
    text = text_input(name, value, limit=10, required=required)
    if not text:
        return ""
    if not _DATE_RE.fullmatch(text):
        raise invalid(f"{name} must be a date, YYYY-MM-DD")
    try:
        Date.fromisoformat(text)
    except ValueError as exc:
        raise invalid(f"{name} is not a real date") from exc
    return text


def decimal_input(
    name: str, value: Any, *, minimum: Decimal | None = None, places: int = 4
) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise invalid(f"{name} must be a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise invalid(f"{name} must be a finite number")
        value = repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise invalid(f"{name} must be a finite number")
        value = format(value, "f")
    text = str(value).strip().replace(" ", "")
    if not _NUMBER_RE.fullmatch(text):
        raise invalid(f"{name} must be a number with '.' as the decimal point")
    number = Decimal(text)
    if minimum is not None and number < minimum:
        raise invalid(f"{name} must be at least {minimum}")
    if -number.as_tuple().exponent > places:  # type: ignore[operator]
        raise invalid(f"{name} may have at most {places} decimal places")
    return number


def allowed(inputs: Mapping[str, Any], names: set[str] | frozenset[str]) -> None:
    unknown = set(inputs) - set(names) - {"connection_ref"}
    if unknown:
        raise invalid(f"Unknown input: {', '.join(sorted(unknown))}")


# ---------------------------------------------------------------------------
# Fields of records and operations, checked against the manual's tables


def _field_value(cls: str, spec: Mapping[str, Any], value: Any) -> Any:
    name = spec["name"]
    kind = str(spec.get("type") or "").lower()
    where = f"{cls}.{name}"
    if isinstance(value, (dict, list)):
        raise invalid(f"{where} must be a single value")
    if kind.startswith("string") or kind == "char":
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise invalid(f"{where} must be text")
        text = str(value)
        length = spec.get("length")
        if (isinstance(length, int) and len(text) > length) or len(text) > MAX_TEXT:
            raise invalid(
                f"{where} may be at most {length or MAX_TEXT} characters (Finvalda's limit)"
            )
        if any(ch in text for ch in "\x00\r") or not text.replace("\n", " ").isprintable():
            raise invalid(f"{where} contains characters that are not allowed")
        return text
    if kind in {"long", "int", "integer"}:
        return whole_input(where, value, minimum=-2_147_483_648, maximum=2_147_483_647)
    if kind.startswith(("double", "numeric", "decimal")):
        size = _NUMERIC_SIZE.match(kind)
        if size is None:
            return str(decimal_input(where, value, places=8))
        precision, scale = int(size.group(1)), int(size.group(2))
        number = decimal_input(where, value, places=scale)
        if abs(number) >= Decimal(10) ** (precision - scale):
            raise invalid(
                f"{where} must be below {Decimal(10) ** (precision - scale)} "
                f"(Finvalda's {spec.get('type')})"
            )
        return str(number)
    if kind.startswith("datetime") or kind == "date":
        text = text_input(where, value, limit=27)
        if not _DATE_TIME_RE.fullmatch(text):
            raise invalid(f"{where} must be a date, YYYY-MM-DD")
        return text
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(decimal_input(where, value, places=8))
    if isinstance(value, (int, float, str)):
        return value
    raise invalid(f"{where} has a value of the wrong kind")


def check_fields(
    cls: str,
    fields: Any,
    *,
    lines: Mapping[str, Any] | None = None,
    enforce_required: bool = False,
    free_form: bool = False,
) -> dict[str, Any]:
    """``fields`` of ``cls`` by the manual's names, with nested line classes in ``lines``."""
    if not isinstance(fields, Mapping):
        raise invalid(f"{cls}: the fields must be an object")
    table = {f["name"]: f for f in CLASSES.get(cls, [])}
    lowered = {name.lower(): name for name in table}
    checked: dict[str, Any] = {}
    for raw_name, value in fields.items():
        name = str(raw_name)
        if lines is not None and name in lines:
            items = value if isinstance(value, list) else [value]
            if not items or len(items) > MAX_LIST_ITEMS:
                raise invalid(f"{cls}.{name}: give 1 to {MAX_LIST_ITEMS} lines")
            checked[name] = [check_fields(name, item, enforce_required=True) for item in items]
            continue
        if free_form:
            if not _COLUMN_RE.fullmatch(name):
                raise invalid(f"{cls}: {name} is not a field name")
            checked[name] = _field_value(
                cls, {"name": name, "type": "string", "length": 255}, value
            )
            continue
        canonical = name if name in table else lowered.get(name.lower())
        if canonical is None:
            known = list(table) + list(lines or {})
            raise invalid(
                f"{cls} has no field {name} in Finvalda's manual; its fields are: "
                + ", ".join(known[:80])
            )
        if value is None:
            continue
        checked[canonical] = _field_value(cls, table[canonical], value)
    if enforce_required:
        missing = [
            n
            for n, f in table.items()
            if f.get("required") and not f.get("from_parameter") and n not in checked
        ]
        if missing:
            raise invalid(f"{cls}: required field missing: {', '.join(missing)}")
    return checked


# ---------------------------------------------------------------------------
# Results


def rows_of(data: Mapping[str, Any]) -> list[Any]:
    """The list of rows in a Finvalda answer (``items``, or the one list it holds)."""
    rest = {
        k: v
        for k, v in data.items()
        if str(k).lower() not in {"accessresult", "error", "serror", "nresult"}
    }
    items = field(rest, "items")
    if isinstance(items, list):
        return items
    lists = [v for v in rest.values() if isinstance(v, list)]
    if len(lists) == 1:
        return lists[0]
    if len(rest) == 1:
        (only,) = rest.values()
        if isinstance(only, Mapping):
            nested = [v for v in only.values() if isinstance(v, list)]
            if len(nested) == 1:
                return nested[0]
            if len(only) == 1:
                (inner,) = only.values()
                if isinstance(inner, Mapping):
                    return [inner]
    return []


def rest_of(data: Mapping[str, Any], rows: list[Any]) -> dict[str, Any] | None:
    rest = {
        k: v
        for k, v in data.items()
        if str(k).lower() not in {"accessresult", "error", "serror"} and v is not rows
    }
    return rest or None


def op_result(data: Mapping[str, Any]) -> dict[str, Any]:
    """Journal, number, series and document of a saved operation (OP_DUOMENYS)."""
    info = field(data, "OP_DUOMENYS")
    if not isinstance(info, Mapping):
        text = field(data, "error") or field(data, "sError") or ""
        info = {}
        if isinstance(text, str) and "<OP_DUOMENYS>" in text:
            for tag in ("SERIJA", "DOKUMENTAS", "ZURNALAS", "NUMERIS"):
                match = re.search(rf"<{tag}>(.*?)</\s*{tag}>", text, re.S)
                if match:
                    info[tag] = match.group(1).strip()
    number = field(info, "NUMERIS")
    try:
        op_number = int(str(number).strip()) if number not in (None, "") else None
    except ValueError:
        op_number = None
    code = field(data, "nResult")
    return {
        "journal": errors.safe_text(field(info, "ZURNALAS"), 20) or None,
        "op_number": op_number,
        "series": errors.safe_text(field(info, "SERIJA"), 20) or None,
        "document": errors.safe_text(field(info, "DOKUMENTAS"), 40) or None,
        "finvalda_result": code if isinstance(code, int) and not isinstance(code, bool) else None,
    }


def mark_write(result: dict[str, Any]) -> dict[str, Any]:
    result["external_effect_status"] = "succeeded"
    result["definitely_no_external_effect"] = False
    return result


def fit_write_result(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink a write's echo until it fits; the write itself already happened."""
    if budget.fits(result):
        return result
    for key in ("record", "response"):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k: v for k, v in value.items() if _KEY_FIELDS.search(str(k))}
    if not budget.fits(result):
        for key in ("record", "response"):
            if key in result:
                result[key] = None
    return result


def exact(value: Any) -> Any:
    """Finvalda's numbers are read as Decimal; a result carries them as exact text."""
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, Mapping):
        return {k: exact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [exact(v) for v in value]
    return value


def _ok(result: Mapping[str, Any]) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "result": exact(dict(result)),
        "error_code": None,
        "error": None,
        "errors": [],
    }
    if "external_effect_status" in result:
        response["external_effect_status"] = result["external_effect_status"]
        response["definitely_no_external_effect"] = result["definitely_no_external_effect"]
    return response


def neutral_result(operation_id: str) -> dict[str, Any]:
    """Every declared output of a write, for a test run where nothing was sent."""
    if operation_id in catalog.FRIENDLY_WRITE_IDS:
        result = friendly.neutral_result(operation_id)
    else:
        result = dict(NEUTRAL_OUTPUTS[OPERATIONS[operation_id]["runner"]])
    # Nothing was sent. "failed" + definitely-no-effect is the pair Core accepts
    # for "no external effect happened"; an extension may not report "suppressed".
    result["external_effect_status"] = "failed"
    result["definitely_no_external_effect"] = True
    return result


_OP_NEUTRAL = {
    "journal": None,
    "op_number": None,
    "series": None,
    "document": None,
    "finvalda_result": None,
}
NEUTRAL_OUTPUTS: dict[str, dict[str, Any]] = {
    "item_write": {"item_class": "", "code": None, "finvalda_result": None},
    "props_write": {"finvalda_result": None},
    "delete_item": {"item_class": "", "code": "", "finvalda_result": None},
    "append_group": {"finvalda_result": None},
    "operation_write": dict(_OP_NEUTRAL),
    "delete_operation": dict(_OP_NEUTRAL),
    "lock": {"finvalda_result": None},
    "change_journal": dict(_OP_NEUTRAL),
    "copy_operation": dict(_OP_NEUTRAL),
    "insert_document": {"file_name": "", "size_bytes": 0},
    "delete_document": {"file_name": ""},
    "attach_document": {"attached": False, "already_attached": False},
    "call_method": {"method": "", "response": None},
}


def validate_only(operation_id: str, operation_input: Mapping[str, Any]) -> None:
    """Check a write's inputs without calling Finvalda (test runs)."""
    if operation_id in catalog.FRIENDLY_IDS:
        friendly.validate(operation_id, operation_input)
        return
    spec = OPERATIONS[operation_id]
    VALIDATORS[spec["runner"]](spec, operation_input, None)


# ---------------------------------------------------------------------------
# Reads


def _method_query(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[tuple[str, str]]:
    names = {p[0] for p in spec["params"]}
    allowed(inputs, names)
    query: list[tuple[str, str]] = []
    for name, wire, kind, aliases in spec["params"]:
        value = inputs.get(name)
        if value is None:
            if kind == "json":
                raise invalid(f"Required input missing: {name}")
            continue
        if kind == "text":
            rendered = text_input(name, value, limit=100)
        elif kind == "date":
            rendered = date_input(name, value)
        elif kind == "integer":
            rendered = str(whole_input(name, value, minimum=-2_147_483_648, maximum=2_147_483_647))
        elif kind == "boolean":
            rendered = "true" if flag_input(name, value) else "false"
        elif kind == "number":
            rendered = str(decimal_input(name, value, places=8))
        else:  # json
            if not isinstance(value, Mapping):
                raise invalid(f"{name} must be an object")
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if len(rendered) > 20_000:
                raise invalid(f"{name} is too large")
        for wire_name in (wire, *aliases):
            query.append((wire_name, rendered))
    return query


def _validate_method_read(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    _method_query(spec, inputs)


def run_method_read(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    query = _method_query(spec, context.operation_input)
    reply = context.call(spec["method"], verb=spec["verb"], query=query, mutating=False)
    rows = rows_of(reply.data)
    return {
        "records": rows,
        "record": rows[0] if rows else None,
        "count": len(rows),
        "response": rest_of(reply.data, rows),
    }


def check_columns(value: Any, name: str = "columns") -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200:
        raise invalid(f"{name} must be a list of at most 200 column names")
    columns = []
    for item in value:
        if not isinstance(item, str) or not _COLUMN_RE.fullmatch(item.strip()):
            raise invalid(f"{name}: {item!r} is not a column name")
        columns.append(item.strip())
    return columns


def _filter_value(where: str, key: str, value: Any) -> Any:
    if key in _LIST_FILTERS:
        items = value if isinstance(value, list) else [value]
        if len(items) > MAX_LIST_ITEMS:
            raise invalid(f"{where}.{key}: at most {MAX_LIST_ITEMS} values")
        return [
            text_input(f"{where}.{key}", str(v) if isinstance(v, int) else v, limit=60)
            for v in items
        ]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) < 2**31:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(repr(value))
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if isinstance(value, str):
        return text_input(f"{where}.{key}", value, limit=200)
    raise invalid(f"{where}.{key} must be text, a number or true/false")


def check_filter(
    raw: Any, groups: Mapping[str, list[str]], *, label: str
) -> dict[str, dict[str, Any]]:
    """A GetDescriptions filter: flat for one filter group, one object per group otherwise."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise invalid("filter must be an object")
    if not groups:
        raise invalid(f"{label} takes no filter")
    nested = len(groups) > 1 or (
        set(raw) and set(raw) <= set(groups) and all(isinstance(v, Mapping) for v in raw.values())
    )
    sources: dict[str, Any] = dict(raw) if nested else {next(iter(groups)): raw}
    out: dict[str, dict[str, Any]] = {}
    for group, conditions in sources.items():
        if group not in groups:
            raise invalid(
                f"filter: {group} is not a filter group of {label}; use {', '.join(groups)}"
            )
        if not isinstance(conditions, Mapping):
            raise invalid(f"filter.{group} must be an object")
        names = {n.lower(): n for n in groups[group]}
        checked: dict[str, Any] = {}
        for key, value in conditions.items():
            canonical = names.get(str(key).lower())
            if canonical is None:
                raise invalid(
                    f"filter: {key} is not documented for {label}; use {', '.join(groups[group])}"
                )
            if value is None or value == "":
                continue
            checked[canonical] = _filter_value(f"filter.{group}", canonical, value)
        out[group] = checked
    return out


def descriptions_body(
    kind: str, groups: Mapping[str, list[str]], inputs: Mapping[str, Any], *, page: int, limit: int
) -> dict[str, Any]:
    params: dict[str, Any] = {"type": kind, "page": page, "limit": limit}
    params.update(check_filter(inputs.get("filter"), groups, label=kind))
    columns = check_columns(inputs.get("columns"))
    if columns:
        params["columns"] = columns
    return {"readParams": params}


_DESCRIPTION_INPUTS = {"filter", "columns", "page", "limit", "fetch_all_pages", "max_pages"}


def _validate_descriptions(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    allowed(inputs, _DESCRIPTION_INPUTS)
    descriptions_body(spec["type"], spec["filters"], inputs, page=1, limit=1)
    whole_input("page", inputs.get("page"), minimum=1, maximum=1_000_000)
    whole_input("limit", inputs.get("limit"), minimum=1, maximum=MAX_LIMIT)
    whole_input("max_pages", inputs.get("max_pages"), minimum=1, maximum=MAX_PAGES)
    flag_input("fetch_all_pages", inputs.get("fetch_all_pages"))


def read_description_pages(
    context: Context,
    kind: str,
    groups: Mapping[str, list[str]],
    inputs: Mapping[str, Any],
    *,
    first_page: int,
    limit: int,
    max_pages: int,
    meter: budget.Meter | None,
) -> dict[str, Any]:
    """Read pages until a short page, ``max_pages``, the output budget or the deadline."""
    records: list[Any] = []
    page = first_page
    fetched = 0
    has_more = False
    last_page: int | None = None
    while fetched < max_pages:
        body = descriptions_body(kind, groups, inputs, page=page, limit=limit)
        try:
            reply = context.call("GetDescriptions", verb="POST", body=body, mutating=False)
        except DeadlineReached:
            if fetched == 0:
                raise
            has_more = True
            break
        rows = rows_of(reply.data)
        chunk = [rows, rows[0]] if fetched == 0 and rows else rows
        if meter is not None and not meter.admit(chunk):
            if fetched == 0:
                raise budget.too_large()
            has_more = True
            break
        records.extend(rows)
        fetched += 1
        last_page = page
        has_more = len(rows) >= limit
        if not has_more:
            break
        page += 1
    return {
        "records": records,
        "page": last_page,
        "next_page": ((last_page or first_page - 1) + 1) if has_more else None,
        "has_more": has_more,
        "pages_fetched": fetched,
    }


def run_descriptions(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    _validate_descriptions(spec, inputs, None)
    fetch_all = flag_input("fetch_all_pages", inputs.get("fetch_all_pages"))
    limit = whole_input(
        "limit", inputs.get("limit"), default=DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT
    )
    page = whole_input("page", inputs.get("page"), default=1, minimum=1, maximum=1_000_000)
    max_pages = whole_input(
        "max_pages",
        inputs.get("max_pages"),
        default=DEFAULT_MAX_PAGES,
        minimum=1,
        maximum=MAX_PAGES,
    )
    read = read_description_pages(
        context,
        spec["type"],
        spec["filters"],
        inputs,
        first_page=page or 1,
        limit=limit or DEFAULT_LIMIT,
        max_pages=(max_pages or 1) if fetch_all else 1,
        meter=budget.Meter(),
    )
    records = read.pop("records")
    return {
        "records": records,
        "record": records[0] if records else None,
        "count": len(records),
        **read,
    }


def operations_body(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    names = {"filter", "columns"} | ({"full_op", "columns_det"} if not spec["detail"] else set())
    allowed(inputs, names)
    raw = inputs.get("filter") or {}
    if not isinstance(raw, Mapping):
        raise invalid("filter must be an object")
    known = {n.lower(): n for n in spec["filters"]}
    checked: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = known.get(str(key).lower())
        if canonical is None:
            raise invalid(
                f"filter: {key} is not documented for {spec['op_class']}; use "
                + ", ".join(spec["filters"])
            )
        if value is None or value == "":
            continue
        if canonical == "OpNumber":
            checked[canonical] = whole_input(
                "filter.OpNumber", value, minimum=1, maximum=2_147_483_647
            )
        elif canonical == "AdvancePaymentSettled":
            checked[canonical] = (
                "true" if flag_input("filter.AdvancePaymentSettled", value) else "false"
            )
        elif "Date" in canonical:
            text = text_input(f"filter.{canonical}", value, limit=27)
            if not _DATE_TIME_RE.fullmatch(text):
                raise invalid(f"filter.{canonical} must be a date, YYYY-MM-DD")
            checked[canonical] = text
        else:
            checked[canonical] = _filter_value("filter", canonical, value)
    params: dict[str, Any] = {"OpClass": spec["op_class"], "filter": checked}
    columns = check_columns(inputs.get("columns"))
    if columns:
        params["columns"] = {"column": columns}
    if not spec["detail"]:
        params["fullOp"] = flag_input("full_op", inputs.get("full_op"))
        det = check_columns(inputs.get("columns_det"), "columns_det")
        if det:
            params["columnsDet"] = {"column": det}
    return {"opReadParams": params}


def _validate_operations(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    operations_body(spec, inputs)


def run_operations(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    body = operations_body(spec, context.operation_input)
    reply = context.call("GetOperations", verb="POST", body=body, mutating=False)
    rows = rows_of(reply.data)
    result = {"records": rows, "record": rows[0] if rows else None, "count": len(rows)}
    if not budget.fits(result):
        raise errors.no_effect(
            errors.RESPONSE_TOO_LARGE,
            "These operations are too many for one step output; narrow the filter (a date range, "
            "a journal, a client) or choose fewer columns",
        )
    return result


# ---------------------------------------------------------------------------
# Writes of records


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
        "xmlstring": json.dumps({cls: fields} if not free_form else fields, ensure_ascii=False),
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
    reply = context.call(spec["method"], verb="POST", body=body, mutating=True)
    return {
        "item_class": cls,
        "code": code or None,
        "finvalda_result": op_result(reply.data)["finvalda_result"],
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
    return {"xmlstring": json.dumps({"Fvs.EditItemProps": groups}, ensure_ascii=False)}


def _validate_props(spec: Mapping[str, Any], inputs: Mapping[str, Any], _: Any) -> None:
    props_body(spec, inputs)


def run_props_write(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    body = props_body(spec, context.operation_input)
    reply = context.call("EditItemProps", verb="POST", body=body, mutating=True)
    return {"finvalda_result": op_result(reply.data)["finvalda_result"]}


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
    reply = context.call("DeleteItem", verb="POST", body=body, mutating=True)
    return {
        "item_class": cls,
        "code": code,
        "finvalda_result": op_result(reply.data)["finvalda_result"],
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
    reply = context.call(
        "AppendGroup", verb="POST", body=append_group_body(context.operation_input), mutating=True
    )
    return {"finvalda_result": op_result(reply.data)["finvalda_result"]}


# ---------------------------------------------------------------------------
# Writes of operations


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
        "xmlstring": json.dumps({cls: operation}, ensure_ascii=False),
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
        "xmlstring": json.dumps(
            {cls: {"sZurnalas": journal, "nNumeris": number}}, ensure_ascii=False
        ),
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
    reply = context.call(
        spec["method"], verb="POST", body=lock_body(context.operation_input, context), mutating=True
    )
    return {"finvalda_result": op_result(reply.data)["finvalda_result"]}


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


# ---------------------------------------------------------------------------
# Files


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
        query=[("sParam", json.dumps(param, ensure_ascii=False, separators=(",", ":")))],
        mutating=False,
    )
    raw = file_contents(reply.data, "MakeInvoice")
    name = (
        f"{param['sSerija']}{param['sDokumentas']}" or f"{param['sZurnalas']}-{param['nNumeris']}"
    )
    return save_file(context, raw, _safe_name(name) + ".pdf", what="The invoice PDF")


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "finvalda"


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
        query=[("sParam", json.dumps(param, ensure_ascii=False, separators=(",", ":")))],
        mutating=False,
    )
    raw = file_contents(reply.data, "MakeReport")
    return save_file(context, raw, _safe_name(code) + ".pdf", what="The report PDF")


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
        _safe_name(code) + ".jpg",
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


def file_name_input(name: str, value: Any) -> str:
    text = text_input(name, value, limit=200, required=True)
    if not _FILE_NAME_RE.fullmatch(text) or text in {".", ".."}:
        raise invalid(f"{name} must be a plain file name with its extension, for example S-1.pdf")
    return text


def fin_user_of(context: Context | None, inputs: Mapping[str, Any], *, required: bool) -> str:
    """The Finvalda employee (finUser) of the newer methods (manual 3.79-3.84).

    It is a Finvalda employee, "not the same as the web service user" (manual
    3.80), so the web service user is never sent in its place.
    """
    given = text_input("fin_user", inputs.get("fin_user"), limit=20)
    if given:
        return given
    if context is None:
        return ""
    if context.connection.fin_user:
        return context.connection.fin_user
    if required:
        raise invalid(
            "Give fin_user, or set the Finvalda employee on the connection: this method needs "
            "a Finvalda employee, which is not the web service user"
        )
    return ""


def _composition_date(value: str) -> dict[str, int]:
    day = Date.fromisoformat(value) if value else Date.today()
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


def run_attached_documents(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"entity_type", "entity_id1", "entity_id2", "file_name"})
    query = entity_query(inputs)
    wanted = text_input("file_name", inputs.get("file_name"), limit=200)
    reply = context.call("GetAttachedDocument", verb="GET", query=query, mutating=False)
    files = attached_files(reply.data)
    listing = [
        {"name": errors.safe_text(f["name"], 200), "size_bytes": len(f["data"]) // 2} for f in files
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
        try:
            raw = bytes.fromhex(match["data"])
        except ValueError as exc:
            raise errors.no_effect(
                errors.INVALID_RESPONSE, "The attached file's content is not hex"
            ) from exc
        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise errors.no_effect(errors.RESPONSE_TOO_LARGE, "The file is larger than 40 MB")
        result.update(save_file(context, raw, wanted, what="The attached file"))
    return result


def run_recommended_price(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(
        inputs,
        {
            "invoice_type",
            "invoice_date",
            "item_type",
            "item_code",
            "quantity",
            "warehouse_code",
            "client_code",
            "client_address_code",
            "fin_user",
        },
    )
    invoice_type = whole_input("invoice_type", inputs.get("invoice_type"), minimum=0, maximum=1)
    item_type = whole_input("item_type", inputs.get("item_type"), minimum=1, maximum=2)
    if invoice_type is None or item_type is None:
        raise invalid("invoice_type and item_type are required")
    day = Date.fromisoformat(date_input("invoice_date", inputs.get("invoice_date"), required=True))
    params = {
        "invoiceType": invoice_type,
        "invoiceDate": {"year": day.year, "month": day.month, "day": day.day},
        "itemType": item_type,
        "itemCode": text_input("item_code", inputs.get("item_code"), limit=20, required=True),
        "itemAmount": decimal_input("quantity", inputs.get("quantity"), minimum=Decimal(0)),
        "warehouseCode": text_input("warehouse_code", inputs.get("warehouse_code"), limit=10),
        "clientCode": text_input("client_code", inputs.get("client_code"), limit=15),
        "clientAdressCode": text_input(
            "client_address_code", inputs.get("client_address_code"), limit=13
        ),
        "finUser": fin_user_of(context, inputs, required=False),
    }
    reply = context.call(
        "GetRecommendedPrice", verb="POST", body={"inParams": params}, mutating=False
    )
    data = reply.data
    result = field(data, "result")
    source = result if isinstance(result, Mapping) else data

    def money(key: str) -> str | None:
        value = field(source, key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            return str(Decimal(str(value)))
        except InvalidOperation:
            return None

    return {
        "price": money("price"),
        "vat": money("vat"),
        "discount": money("discount"),
        "response": rest_of(data, []),
    }


def run_user_permissions(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"fin_user"})
    reply = context.call(
        "GetUserPermissions",
        verb="GET",
        query=[("finUser", fin_user_of(context, inputs, required=True))],
        mutating=False,
    )
    permissions = rows_of(reply.data)
    if not permissions:
        result = field(reply.data, "result")
        found = field(result, "perrmissions") if isinstance(result, Mapping) else None
        permissions = found if isinstance(found, list) else []
    return {"permissions": permissions, "count": len(permissions), "response": None}


def run_related_invoices(context: Context, spec: Mapping[str, Any]) -> dict[str, Any]:
    inputs = context.operation_input
    allowed(inputs, {"client_code", "debt_type"})
    query = [
        (
            "nDebtType",
            str(
                whole_input("debt_type", inputs.get("debt_type"), default=0, minimum=0, maximum=10)
            ),
        )
    ]
    client = text_input("client_code", inputs.get("client_code"), limit=15)
    if client:
        query.append(("sKlientoKodas", client))
    reply = context.call(
        "GetInvoicesRelatedToCustomer", verb="POST", query=query, body={}, mutating=False
    )
    rows = rows_of(reply.data)
    return {
        "records": rows,
        "record": rows[0] if rows else None,
        "count": len(rows),
        "response": rest_of(reply.data, rows),
    }


# ---------------------------------------------------------------------------
# Export and raw calls


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
        page_chunks = [(json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows]
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
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, Decimal):
            rendered = format(value, "f")
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
    reply = context.call(method, verb=verb, query=query, body=body, mutating=True)
    result: dict[str, Any] = {"method": method, "response": reply.data}
    if not budget.fits(result):
        result["response"] = None
    return result


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
for _runner in RUNNERS:
    VALIDATORS.setdefault(_runner, _no_validation)


__all__ = [
    "Context",
    "DOWNLOAD_CONTENT_TYPE",
    "EXPORT_CONTENT_TYPE",
    "RUNNERS",
    "attach_document",
    "check_fields",
    "check_filter",
    "decimal_input",
    "file_contents",
    "handle_runtime",
    "make_invoice_pdf",
    "neutral_result",
    "op_result",
    "read_description_pages",
    "read_input_file",
    "rows_of",
    "save_file",
    "validate_only",
]
