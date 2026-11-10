"""Stable, safe error vocabulary for the Finvalda extension.

Every failure maps to exactly one of these codes. No message carries the
password. Finvalda's own refusal (its ``nResult`` code and error text) is
business information the operator needs to fix the document, so it is passed
on, bounded and stripped of control characters, with the meaning the manual
gives the code.
"""

from __future__ import annotations

from collections.abc import Mapping

INVALID_PAYLOAD = "invalid_payload"
INVALID_HOST_ENVELOPE = "invalid_host_envelope"
INVALID_CONNECTION = "invalid_connection"
UNSUPPORTED_OPERATION = "unsupported_operation"
AUTHENTICATION_FAILED = "authentication_failed"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
UPSTREAM_VALIDATION_FAILED = "upstream_validation_failed"
UPSTREAM_FAILURE = "upstream_failure"
INVALID_RESPONSE = "invalid_response"
RESPONSE_TOO_LARGE = "response_too_large"
TIMEOUT = "timeout"
TIMEOUT_UNKNOWN = "timeout_unknown"
CONNECTION_FAILED = "connection_failed"
REDIRECT_REJECTED = "redirect_rejected"
BLOCKED_ADDRESS = "blocked_address"
ARTIFACT_INPUT_UNAVAILABLE = "artifact_input_unavailable"
ARTIFACT_OUTPUT_UNAVAILABLE = "artifact_output_unavailable"
INTERNAL_ERROR = "internal_error"

SAFE_ERROR_CODES: frozenset[str] = frozenset(
    {
        INVALID_PAYLOAD,
        INVALID_HOST_ENVELOPE,
        INVALID_CONNECTION,
        UNSUPPORTED_OPERATION,
        AUTHENTICATION_FAILED,
        NOT_FOUND,
        CONFLICT,
        UPSTREAM_VALIDATION_FAILED,
        UPSTREAM_FAILURE,
        INVALID_RESPONSE,
        RESPONSE_TOO_LARGE,
        TIMEOUT,
        TIMEOUT_UNKNOWN,
        CONNECTION_FAILED,
        REDIRECT_REJECTED,
        BLOCKED_ADDRESS,
        ARTIFACT_INPUT_UNAVAILABLE,
        ARTIFACT_OUTPUT_UNAVAILABLE,
        INTERNAL_ERROR,
    }
)

#: Codes that leave a write's outcome unknown to the caller.
AMBIGUOUS_ERROR_CODES: frozenset[str] = frozenset({TIMEOUT_UNKNOWN})

_MAX_MESSAGE = 1500


def safe_text(value: object, limit: int = 300) -> str:
    """Printable, single-line, bounded text from an upstream value."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ExtensionError(Exception):
    """A failure already reduced to a safe code and a safe message."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        failure_class: str | None = None,
        retryable: bool | None = None,
        definitely_no_external_effect: bool | None = None,
        external_effect_status: str | None = None,
        http_status: int | None = None,
        finvalda_code: int | None = None,
    ) -> None:
        safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
        bounded = safe_text(message, _MAX_MESSAGE)
        super().__init__(bounded)
        self.code = safe_code
        self.message = bounded
        self.failure_class = failure_class
        self.retryable = retryable
        self.definitely_no_external_effect = definitely_no_external_effect
        self.external_effect_status = external_effect_status
        self.http_status = http_status
        self.finvalda_code = finvalda_code

    def retry_facts(self) -> dict[str, object]:
        """Closed, non-secret facts understood by Core."""
        facts: dict[str, object] = {}
        for key in (
            "failure_class",
            "retryable",
            "definitely_no_external_effect",
            "external_effect_status",
            "http_status",
        ):
            value = getattr(self, key)
            if value is not None:
                facts[key] = value
        if self.failure_class in {"provider", "transient"}:
            facts["provider_error_code"] = self.code
        return facts


def no_effect(code: str, message: str, **facts: object) -> ExtensionError:
    """A refusal that certainly changed nothing in Finvalda."""
    return ExtensionError(
        code,
        message,
        definitely_no_external_effect=True,
        external_effect_status="failed",
        **facts,  # type: ignore[arg-type]
    )


def invalid(message: str) -> ExtensionError:
    return no_effect(INVALID_PAYLOAD, message)


def error_response(
    code: str,
    message: str,
    *,
    retry_facts: Mapping[str, object] | None = None,
    finvalda_code: int | None = None,
) -> dict[str, object]:
    """The failing runtime envelope for one safe error."""
    safe_code = code if code in SAFE_ERROR_CODES else INTERNAL_ERROR
    bounded = safe_text(message, _MAX_MESSAGE)
    entry: dict[str, object] = {"code": safe_code, "message": bounded}
    if finvalda_code is not None:
        entry["finvalda_code"] = finvalda_code
    response: dict[str, object] = {
        "ok": False,
        "result": {},
        "error_code": safe_code,
        "error": bounded,
        "errors": [entry],
    }
    if safe_code in AMBIGUOUS_ERROR_CODES:
        response["external_effect_status"] = TIMEOUT_UNKNOWN
        response["definitely_no_external_effect"] = False
    if retry_facts:
        response.update(retry_facts)
    return response


__all__ = [
    "AMBIGUOUS_ERROR_CODES",
    "SAFE_ERROR_CODES",
    "ExtensionError",
    "error_response",
    "invalid",
    "no_effect",
    "safe_text",
]
