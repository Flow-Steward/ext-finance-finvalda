"""Run one workflow operation against the Finvalda web service: the entry point."""

from __future__ import annotations

import sys
import traceback

from collections.abc import Callable, Mapping
from typing import Any

from flowsteward_extension_sdk import (
    read_artifact_bytes,
    write_artifact_stream,
)

from . import budget, catalog, errors, everyday
from .advanced import NEUTRAL_OUTPUTS, RUNNERS, VALIDATORS
from .connection import connection_from_payload
from .context import ArtifactWriter, Context, TransportFactory
from .errors import ExtensionError, error_response, invalid
from .inputs import _text
from .limits import ARTIFACT_WRITE_TIMEOUT_SECONDS, STEP_MARGIN_SECONDS, STEP_TIMEOUT_SECONDS
from .reference_data import OPERATIONS
from .shapes import collect_value_warnings
from .results import _ok, fit_write_result, mark_write
from .transport import DeadlineReached, FinvaldaTransport


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
        with collect_value_warnings() as value_warnings:
            if operation_id in catalog.FRIENDLY_IDS:
                result = everyday.run(context, operation_id)
            else:
                spec = OPERATIONS[operation_id]
                result = RUNNERS[spec["runner"]](context, spec)
        if value_warnings:
            result["warnings"] = [*(result.get("warnings") or []), *value_warnings]
        if operation_id in catalog.FRIENDLY_IDS:
            if operation_id in catalog.FRIENDLY_WRITE_IDS:
                return _ok(mark_write(fit_write_result(result)))
            budget.ensure_fits(result)
            return _ok(result)
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
    except Exception as exc:
        where = _where(exc)
        # A safe trace for the operator: the exception's type and place, never its text
        # (which could hold data), on stderr for the host's logs and in the message.
        print(f"flowsteward.finvalda internal error: {where}", file=sys.stderr)
        response = error_response(
            errors.INTERNAL_ERROR, f"The operation could not be completed ({where})"
        )
        if transport is None or not transport.mutated:
            response["definitely_no_external_effect"] = True
            response["external_effect_status"] = "failed"
        else:
            response["definitely_no_external_effect"] = False
            response["external_effect_status"] = "timeout_unknown"
        return response


def _where(exc: BaseException) -> str:
    """``TypeError at runtime/everyday/invoices.py:123 in invoice_op`` — no values."""
    frames = traceback.extract_tb(exc.__traceback__)
    inside = [f for f in frames if "/runtime/" in f.filename.replace("\\", "/")]
    chosen = inside or list(frames)
    if not chosen:
        return type(exc).__name__
    frame = chosen[-1]
    path = frame.filename.replace("\\", "/")
    path = path[path.rfind("/runtime/") + 1 :] if "/runtime/" in path else path.rsplit("/", 1)[-1]
    return f"{type(exc).__name__} at {path}:{frame.lineno} in {frame.name}"


def _mark_unsent(exc: ExtensionError, transport: FinvaldaTransport | None) -> None:
    """A failure before any change could be sent cannot have had an effect."""
    if transport is not None and transport.mutated:
        return
    if exc.definitely_no_external_effect is None:
        exc.definitely_no_external_effect = True
    if exc.definitely_no_external_effect and exc.external_effect_status is None:
        exc.external_effect_status = "failed"


_ALLOWED_ACTION_KEYS = frozenset(
    {"action_id", "operation_id", "page_id", "component_id", "context", "input", "target"}
)


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


def neutral_result(operation_id: str) -> dict[str, Any]:
    """Every declared output of a write, for a test run where nothing was sent."""
    if operation_id in catalog.FRIENDLY_WRITE_IDS:
        result = everyday.everyday_neutral_result(operation_id)
    else:
        result = dict(NEUTRAL_OUTPUTS[OPERATIONS[operation_id]["runner"]])
    # Nothing was sent. "failed" + definitely-no-effect is the pair Core accepts
    # for "no external effect happened"; an extension may not report "suppressed".
    result["external_effect_status"] = "failed"
    result["definitely_no_external_effect"] = True
    return result


def validate_only(operation_id: str, operation_input: Mapping[str, Any]) -> None:
    """Check a write's inputs without calling Finvalda (test runs)."""
    if operation_id in catalog.FRIENDLY_IDS:
        everyday.validate(operation_id, operation_input)
        return
    spec = OPERATIONS[operation_id]
    VALIDATORS[spec["runner"]](spec, operation_input, None)
