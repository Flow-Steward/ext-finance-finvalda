"""The only way this extension reaches Finvalda.

REST against the customer's FvsServicePure interface (``<address>/<Method>``)
with JSON. The web service user, its password, the company and the answer
language travel in request headers, as in Finvalda's Postman collection.
Requests go out through the public Flow Steward extension SDK, which pins DNS,
verifies the connected peer, refuses redirects and enforces the timeout.

Every answer is an object with ``AccessResult`` (``Success``, ``Fail`` or
``AccessDenied``), the error text in ``error`` (or ``sError``) and, for writes,
Finvalda's result code ``nResult`` (0 = saved; the codes are listed in the
manual's appendix I). Finvalda documents no rate limit and no idempotency key:

* a write is sent exactly once and never retried here;
* when Finvalda answers a write with its own refusal code, nothing was saved —
  except the codes the manual describes as program, memory, recordset or
  transaction errors (2001, 2002, 2010, 2031), which may come after part of the
  work: those, a server error, a timeout or a lost connection leave the outcome
  unknown (``timeout_unknown``).
"""

from __future__ import annotations

import html
import http.client
import json
import re
import secrets
import threading
import time

# DTDs and entity declarations are refused before anything is parsed (parse_body).
import xml.etree.ElementTree as ElementTree  # nosec B405
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from flowsteward_extension_sdk import PinnedPeerError, open_pinned_url

from . import errors
from .connection import LANGUAGES, Connection
from .errors import ExtensionError
from .fields import field
from .reference_data import ERROR_MEANINGS

DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_REQUEST_BODY_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 80 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 256 * 1024
CHUNK_BYTES = 64 * 1024
MIN_INTERVAL_SECONDS = 0.05
#: Shortest window still worth starting a request in.
MIN_CALL_WINDOW_SECONDS = 15.0
_USER_AGENT = "FlowSteward-Finvalda/0.1"
_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,80}$")

#: Finvalda result codes the manual describes as failures that may come after part
#: of the work (appendix I: recordset, memory, program and transaction errors).
AMBIGUOUS_RESULT_CODES: frozenset[int] = frozenset({2001, 2002, 2010, 2031})
#: "Already exists / already used" refusals (appendix I).
DUPLICATE_RESULT_CODES: frozenset[int] = frozenset(
    {2, 3, 4, 28, 29, 74, 1001, 1002, 1035, 1040, 1041, 1042, 1043, 1058, 1072, 1086}
)


class DeadlineReached(Exception):
    """The invocation's time budget leaves no room for another request."""


@dataclass
class Reply:
    status: int
    #: The answer object (AccessResult, error and the data keys).
    data: dict[str, Any]


def meaning(code: int | None) -> str:
    """The manual's text for a Finvalda result code, if it lists one."""
    return ERROR_MEANINGS.get(str(code), "") if code is not None else ""


class FinvaldaTransport:
    """Sends Finvalda web service requests for one connection."""

    def __init__(
        self,
        connection: Connection,
        *,
        opener: Callable[..., Any] = open_pinned_url,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._connection = connection
        self._opener = opener
        self._sleep = sleeper
        self.clock = clock
        self._timeout_seconds = timeout_seconds
        self._last_sent: float | None = None
        #: True once any request may have left this process.
        self.attempted = False
        #: True once a request that can change data may have left this process.
        self.mutated = False
        #: True once Finvalda confirmed a change in this invocation.
        self.committed = False
        #: Monotonic time after which no request may start (set by the operation).
        self.deadline: float | None = None
        self._call_timeout = timeout_seconds
        self._forms: list[str] | None = None

    @property
    def connection(self) -> Connection:
        return self._connection

    def remaining(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - self.clock()

    def request(
        self,
        method_name: str,
        *,
        verb: str,
        query: Iterable[tuple[str, str]] = (),
        body: Mapping[str, Any] | None = None,
        mutating: bool,
        language: str | None = None,
    ) -> Reply:
        """Send one call to ``<address>/<method_name>`` and check Finvalda's verdict.

        ``language`` ("lt" or "en") overrides the connection's answer language, for
        everyday steps that read an older method's Lithuanian column names.
        """
        if not _METHOD_RE.fullmatch(method_name) or verb not in {"GET", "POST"}:
            raise errors.invalid("Not a Finvalda method name")
        encoded: bytes | None = None
        if body is not None:
            encoded = dumps_json(body).encode("utf-8")
            if len(encoded) > MAX_REQUEST_BODY_BYTES:
                raise errors.invalid("The request is larger than the 20 MB this extension sends")
        query_list = list(query)
        url = f"{self._connection.base_url}/{method_name}"
        if query_list:
            url += "?" + urlencode(query_list)
        remaining = self.remaining()
        if remaining < MIN_CALL_WINDOW_SECONDS:
            raise DeadlineReached
        self._call_timeout = min(self._timeout_seconds, remaining)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": _USER_AGENT,
            **self._connection.headers(),
        }
        if language in LANGUAGES:
            headers["Language"] = LANGUAGES[language]
        request = Request(url, method=verb, data=encoded, headers=headers)
        self._pace()
        status, raw = self._open(request, method_name, mutating=mutating)
        data = self._decode(raw, status, method_name, mutating=mutating)
        self._verdict(data, status, method_name, mutating=mutating)
        if mutating:
            confirm_write(data, method_name, status=status)
            self.committed = True
        return Reply(status=status, data=data)

    # -- one request -------------------------------------------------------

    def _pace(self) -> None:
        if self._last_sent is not None:
            wait = MIN_INTERVAL_SECONDS - (self.clock() - self._last_sent)
            if wait > 0:
                self._sleep(wait)
        self._last_sent = self.clock()

    def _read_expiry(self) -> float | None:
        return None if self.deadline is None else self.deadline + MIN_CALL_WINDOW_SECONDS

    def _open(self, request: Request, method_name: str, *, mutating: bool) -> tuple[int, bytes]:
        """One exchange — connect, headers and body — bounded by the step's deadline.

        A socket timeout applies to each receive, and a server can answer one byte
        at a time, while it connects, while it sends its headers and while it sends
        its body. So the whole exchange runs in a worker thread that is waited for
        only until the read deadline; past it the step reports a timeout (an unknown
        outcome for a write) and the abandoned exchange ends with the process.
        """
        self.attempted = True
        if mutating:
            self.mutated = True
        expires = self._read_expiry()
        if expires is None:
            return self._exchange(request, method_name, mutating=mutating)
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["value"] = self._exchange(request, method_name, mutating=mutating)
            except BaseException as exc:  # handed to the caller below
                outcome["error"] = exc

        worker = threading.Thread(target=work, name="finvalda-call", daemon=True)
        worker.start()
        worker.join(max(0.0, expires - self.clock()))
        if worker.is_alive():
            _late(mutating)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _exchange(self, request: Request, method_name: str, *, mutating: bool) -> tuple[int, bytes]:
        try:
            with self._opener(
                request, timeout_seconds=self._call_timeout, purpose="Finvalda web service"
            ) as response:
                status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
                raw = read_bounded(
                    response,
                    MAX_RESPONSE_BYTES,
                    mutating=mutating,
                    expires=self._read_expiry(),
                    clock=self.clock,
                    per_read=self._call_timeout,
                )
        except HTTPError as exc:
            try:
                body = exc.read(MAX_ERROR_BODY_BYTES)
            except Exception:  # pragma: no cover - upstream body is optional
                body = b""
            raise self._status_error(int(exc.code), body, method_name, mutating=mutating) from exc
        except PinnedPeerError as exc:
            # Raised while resolving and pinning, before anything was sent.
            host = self._connection.host
            if "cannot be resolved" in str(exc):
                raise ExtensionError(
                    errors.CONNECTION_FAILED,
                    f"{host} could not be resolved; check the web service address",
                    failure_class="transient",
                    retryable=True,
                    definitely_no_external_effect=True,
                    external_effect_status="failed",
                ) from exc
            raise errors.no_effect(
                errors.BLOCKED_ADDRESS,
                f"{host} is not reachable under the host network policy (it must be a public "
                "https address)",
            ) from exc
        except TimeoutError as exc:
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN if mutating else errors.TIMEOUT,
                "Finvalda did not answer in time"
                + ("; check in Finvalda whether the change was saved" if mutating else ""),
                **_transport_failure_facts(mutating=mutating),
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN if mutating else errors.CONNECTION_FAILED,
                "The connection to the Finvalda web service failed"
                + ("; check in Finvalda whether the change was saved" if mutating else ""),
                **_transport_failure_facts(mutating=mutating),
            ) from exc
        if status >= 300 or status < 200:
            raise self._status_error(
                status, raw[:MAX_ERROR_BODY_BYTES], method_name, mutating=mutating
            )
        return status, raw

    def _secret_forms(self) -> list[str]:
        if self._forms is None:
            self._forms = self._build_secret_forms()
        return self._forms

    def _build_secret_forms(self) -> list[str]:
        forms: set[str] = set()
        for secret in self._connection.secret_fragments():
            escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
            forms |= {
                secret,
                escaped,
                escaped.replace("/", "\\/"),
                html.escape(secret, quote=True),
                html.escape(secret, quote=True).replace("&#x27;", "&apos;"),
                html.escape(secret, quote=False),
            }
        # Longest first, so a form that contains another is removed whole.
        return sorted((f for f in forms if f), key=len, reverse=True)

    def scrub_answer(self, raw: bytes) -> dict[str, Any]:
        """``raw`` parsed, with the password removed from its texts.

        The check runs on the parsed texts and keys (escapes and entities already
        resolved), never on the raw bytes; the answer is copied only when one of
        them holds a form of the password.
        """
        data = parse_body(raw)
        return dict(self.scrub(data)) if self._holds_secret(data) else data

    def _holds_secret(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(value) and any(f in value for f in self._secret_forms())
        if isinstance(value, Mapping):
            return any(self._holds_secret(k) or self._holds_secret(v) for k, v in value.items())
        if isinstance(value, list):
            return any(self._holds_secret(v) for v in value)
        return False

    def scrub(self, value: Any) -> Any:
        """The password removed from every text of an already parsed answer.

        Text values and keys only: replacing in the raw bytes would also change
        numbers and dates that happen to contain the same digits ("2024").
        """
        if isinstance(value, str):
            if value:
                for form in self._secret_forms():
                    if form in value:
                        value = value.replace(form, "[redacted]")
            return value
        if isinstance(value, Mapping):
            return {self.scrub(k): self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return value

    def _decode(
        self, raw: bytes, status: int, method_name: str, *, mutating: bool
    ) -> dict[str, Any]:
        try:
            data = self.scrub_answer(raw)
        except ValueError as exc:
            if mutating:
                raise ExtensionError(
                    errors.TIMEOUT_UNKNOWN,
                    f"Finvalda answered {method_name} (HTTP {status}) with something that could "
                    "not be read; check in Finvalda whether the change was saved",
                    failure_class="provider",
                    retryable=False,
                    definitely_no_external_effect=False,
                    external_effect_status="timeout_unknown",
                    http_status=status,
                ) from exc
            raise ExtensionError(
                errors.INVALID_RESPONSE,
                f"Finvalda answered {method_name} with something that is not a JSON object",
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=True,
                external_effect_status="failed",
                http_status=status,
            ) from exc
        return data

    def _verdict(
        self, data: Mapping[str, Any], status: int, method_name: str, *, mutating: bool
    ) -> None:
        access = field(data, "AccessResult")
        if access is None:
            access = field(data, "accessResult")
        text = error_text(data)
        code = result_code(data)
        nested_code, nested_text = nested_result(data)
        if isinstance(access, str) and access.lower() == "accessdenied":
            raise errors.no_effect(
                errors.AUTHENTICATION_FAILED,
                "Finvalda refused the web service user (wrong user name, password or company, or "
                f"no right to {method_name})" + (f": {text}" if text else ""),
                failure_class="provider",
                retryable=False,
                http_status=status,
            )
        if nested_code:
            # The newer methods (manual 3.79-3.84) answer AccessResult "Success" with
            # their own result object: {"result": {"errorCode", "errorText", ...}}.
            # Their codes are the method's own, not those of appendix I.
            raise ExtensionError(
                errors.UPSTREAM_VALIDATION_FAILED,
                f"Finvalda refused {method_name} with its error {nested_code}"
                + (f": {nested_text or text}" if (nested_text or text) else ""),
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=True,
                external_effect_status="failed",
                http_status=status,
                finvalda_code=nested_code,
            )
        failed = (isinstance(access, str) and access.lower() == "fail") or (
            code is not None and code != 0
        )
        if not failed:
            return
        if mutating and code is None:
            # A refusal without Finvalda's result code says nothing about how far a
            # multi-step method (copy and delete, move, document register) got.
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN,
                f"Finvalda answered {method_name} with a failure but no result code"
                + (f": {text}" if text else "")
                + "; check in Finvalda whether anything changed before retrying",
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=False,
                external_effect_status="timeout_unknown",
                http_status=status,
            )
        raise refusal(method_name, code, text, status=status, mutating=mutating)

    def _status_error(
        self, status: int, raw: bytes, method_name: str, *, mutating: bool
    ) -> ExtensionError:
        detail = ""
        try:
            data = self.scrub_answer(raw)
        except ValueError:
            text = re.sub(rb"<[^>]+>", b" ", raw).decode("utf-8", "replace")
            detail = errors.safe_text(self.scrub(text), 300)
        else:
            detail = error_text(data)
        suffix = f": {detail}" if detail else ""
        if 300 <= status < 400:
            return ExtensionError(
                errors.REDIRECT_REJECTED,
                f"The web service redirected {method_name} (HTTP {status}); redirects are not "
                "followed — check the address",
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=not mutating,
                external_effect_status="timeout_unknown" if mutating else "failed",
                http_status=status,
            )
        if status in {401, 403}:
            return errors.no_effect(
                errors.AUTHENTICATION_FAILED,
                f"The web service refused the request (HTTP {status}){suffix}",
                failure_class="provider",
                retryable=False,
                http_status=status,
            )
        if status in {404, 405}:
            return errors.no_effect(
                errors.NOT_FOUND,
                f"This Finvalda web service does not offer {method_name} (HTTP {status}); check "
                "the address (…/FvsServicePure.svc) and the Finvalda version",
                failure_class="provider",
                retryable=False,
                http_status=status,
            )
        if mutating and (status == 400 or status == 408 or status >= 500):
            # WCF answers an exception inside the method with 400 or 500: the method
            # may have saved part of its work. Not retried; checked by the operator.
            return ExtensionError(
                errors.TIMEOUT_UNKNOWN,
                f"Finvalda did not confirm {method_name} (HTTP {status}); check in Finvalda "
                f"whether the change was saved before retrying{suffix}",
                failure_class="provider",
                retryable=False,
                definitely_no_external_effect=False,
                external_effect_status="timeout_unknown",
                http_status=status,
            )
        if status == 400:
            return errors.no_effect(
                errors.UPSTREAM_VALIDATION_FAILED,
                f"Finvalda rejected the {method_name} request (HTTP 400){suffix}",
                failure_class="provider",
                retryable=False,
                http_status=status,
            )
        return ExtensionError(
            errors.TIMEOUT if status in {408, 504} else errors.UPSTREAM_FAILURE,
            f"The Finvalda web service could not complete {method_name} (HTTP {status}){suffix}",
            failure_class="provider",
            retryable=status >= 500 or status == 408,
            definitely_no_external_effect=True,
            external_effect_status="failed",
            http_status=status,
        )


def refusal(
    method_name: str, code: int | None, text: str, *, status: int = 200, mutating: bool
) -> ExtensionError:
    """Finvalda's own "Fail" answer, classified by its result code."""
    what = meaning(code)
    label = f"error {code}" + (f" ({what})" if what else "") if code is not None else "an error"
    message = f"Finvalda refused {method_name} with {label}" + (f": {text}" if text else "")
    if mutating and code in AMBIGUOUS_RESULT_CODES:
        return ExtensionError(
            errors.TIMEOUT_UNKNOWN,
            message + " — this kind of error can come after part of the work was saved; check in "
            "Finvalda before retrying",
            failure_class="provider",
            retryable=False,
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
            http_status=status,
            finvalda_code=code,
        )
    kind = errors.CONFLICT if code in DUPLICATE_RESULT_CODES else errors.UPSTREAM_VALIDATION_FAILED
    return ExtensionError(
        kind,
        message,
        failure_class="provider",
        retryable=False,
        definitely_no_external_effect=True,
        external_effect_status="failed",
        http_status=status,
        finvalda_code=code,
    )


#: Writes whose answer carries Finvalda's result code: saved only with nResult 0
#: (manual 3.66-3.74 "nResult – klaidos kodas"; every Postman example of them).
RESULT_CODE_WRITES: frozenset[str] = frozenset(
    {
        "InsertNewItem",
        "EditItem",
        "EditItemProps",
        "DeleteItem",
        "AppendGroup",
        "InsertNewOperation",
        "UpdateOperation",
        "DeleteOperation",
        "LockOperation",
        "UnLockOperation",
        "ChangeJournal",
        "CopyOperation",
    }
)
#: The newer document writes answer with a result object: saved only with errorCode 0
#: (manual 3.80 InsertDocumentOut, 3.82 AttachDocumentOut).
RESULT_OBJECT_WRITES: frozenset[str] = frozenset({"InsertDocument", "AttachDocument"})
#: Every method that changes Finvalda; every other documented method only reads
#: (Get…, IsOperationLocked, MakeInvoice, MakeReport).
WRITE_METHODS: frozenset[str] = RESULT_CODE_WRITES | RESULT_OBJECT_WRITES | {"DeleteDocument"}


def confirm_write(data: Mapping[str, Any], method_name: str, *, status: int) -> None:
    """Accept a write only on Finvalda's positive confirmation.

    An answer that refuses nothing but confirms nothing either (``{}``, an unknown
    AccessResult, a missing result code) leaves the outcome unknown: the write was
    sent, and nothing says it was saved.
    """
    access = field(data, "AccessResult")
    if access is None:
        access = field(data, "accessResult")
    success = isinstance(access, str) and access.lower() == "success"
    if method_name in RESULT_OBJECT_WRITES:
        # The JSON form carries AccessResult and ``result``; the XML form only the
        # ``…Out`` element (manual 3.80, 3.82). errorCode 0 confirms either one.
        code, _ = nested_result(data)
        confirmed = code == 0 and (access is None or success)
    elif method_name in RESULT_CODE_WRITES:
        confirmed = success and result_code(data, keys=("nResult",)) == 0
    else:
        confirmed = success
    if confirmed and method_name == "DeleteDocument":
        # DeleteDocument(string fileName, out string error) (manual 3.81).
        confirmed = field(data, "error") == "" or field(data, "sError") == ""
    if not confirmed:
        raise ExtensionError(
            errors.TIMEOUT_UNKNOWN,
            f"Finvalda's answer to {method_name} does not confirm that the change was saved; "
            "check Finvalda before running the step again",
            failure_class="provider",
            retryable=False,
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
            http_status=status,
        )


def nested_result(data: Mapping[str, Any]) -> tuple[int | None, str]:
    """``errorCode``/``errorText`` of a newer method's result object (JSON ``result``,
    or the one ``…Out`` element of the XML form)."""
    candidates: list[Any] = [field(data, "result")]
    candidates += [v for k, v in data.items() if str(k).endswith("Out")]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            code = result_code(candidate, keys=("errorCode",))
            if code is not None:
                text = field(candidate, "errorText")
                return code, errors.safe_text(text, 500) if isinstance(text, str) else ""
    return None, ""


def dumps_json(value: Any) -> str:
    """JSON with ``Decimal`` written as an exact number (never through float)."""
    marker = secrets.token_hex(8)

    def default(item: Any) -> str:
        if isinstance(item, Decimal) and item.is_finite():
            return f"@{marker}@{item}@{marker}@"
        raise TypeError(f"{type(item).__name__} is not JSON")

    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=default)
    return re.sub(rf'"@{marker}@([-+0-9.Ee]+)@{marker}@"', r"\1", text)


def error_text(data: Mapping[str, Any]) -> str:
    for key in ("error", "sError", "errorText"):
        value = field(data, key)
        if isinstance(value, str) and value.strip() and not value.lstrip().startswith("<OP_"):
            return errors.safe_text(value, 500)
    return ""


def result_code(
    data: Mapping[str, Any], keys: tuple[str, ...] = ("nResult", "errorCode")
) -> int | None:
    for key in keys:
        value = field(data, key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def parse_body(raw: bytes) -> dict[str, Any]:
    """A JSON object, or — for the few methods that answer in XML — an XML document as one.

    XML is parsed only after refusing any DOCTYPE or entity declaration, so no
    entity can be expanded and nothing outside the document is read.
    """
    text = raw.decode("utf-8-sig", "strict") if raw else ""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty")
    if stripped.startswith("<"):
        upper = stripped.upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            raise ValueError("DTD refused")
        try:
            root = ElementTree.fromstring(stripped)  # nosec B314
        except ElementTree.ParseError as exc:
            raise ValueError("bad XML") from exc
        value = _xml_value(root)
        tag = root.tag.split("}", 1)[-1]
        if tag.endswith("Out"):
            # The newer methods' XML answer is their result element itself
            # (<InsertDocumentOut>, <GetRecommendedPriceOut>, <GetUserPermissionsOut>;
            # manual 3.79-3.84): it is what the JSON form calls ``result``, and is read
            # the same way — its errorCode as the method's own, its fields as the
            # result's.
            return {"result": value if isinstance(value, dict) else {"value": value}}
        return value if isinstance(value, dict) else {"value": value}
    try:
        data = json.loads(stripped, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError("bad JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _xml_value(element: ElementTree.Element) -> Any:
    children = list(element)
    if not children:
        return (element.text or "").strip()
    out: dict[str, Any] = {}
    for child in children:
        tag = child.tag.split("}", 1)[-1]
        value = _xml_value(child)
        if tag in out:
            if not isinstance(out[tag], list):
                out[tag] = [out[tag]]
            out[tag].append(value)
        else:
            out[tag] = value
    return out


def read_bounded(
    response: Any,
    limit: int,
    *,
    mutating: bool = False,
    expires: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    per_read: float | None = None,
) -> bytes:
    """Read at most ``limit`` bytes before ``expires``.

    The socket timeout applies to each receive, so before every receive it is cut to
    the time left until ``expires``: a server that trickles its answer cannot keep
    the step past its own time limit.
    """
    buffer = bytearray()
    sock = _socket_of(response)
    while True:
        if expires is not None:
            left = expires - clock()
            if left <= 0:
                _late(mutating)
            if sock is not None:
                try:
                    sock.settimeout(max(0.5, min(left, per_read or left)))
                except OSError:  # pragma: no cover - a closed socket fails the read below
                    pass
        chunk = response.read(min(CHUNK_BYTES, limit + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if expires is not None and clock() > expires:
            _late(mutating)
        if len(buffer) > limit:
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN if mutating else errors.RESPONSE_TOO_LARGE,
                "Finvalda's answer was too large to read"
                + (
                    "; check in Finvalda whether the change was saved"
                    if mutating
                    else "; narrow it with filters"
                ),
                definitely_no_external_effect=not mutating,
                external_effect_status="timeout_unknown" if mutating else "failed",
            )
    return bytes(buffer)


def _late(mutating: bool) -> None:
    if not mutating:
        raise DeadlineReached
    raise ExtensionError(
        errors.TIMEOUT_UNKNOWN,
        "Finvalda's answer did not arrive in time; check in Finvalda whether the change was saved",
        failure_class="transient",
        retryable=False,
        definitely_no_external_effect=False,
        external_effect_status="timeout_unknown",
    )


def _socket_of(response: Any) -> Any:
    """The socket under an http.client response, when it can be reached."""
    for path in (("fp", "raw", "_sock"), ("fp", "fp", "raw", "_sock"), ("_sock",)):
        node = response
        for name in path:
            node = getattr(node, name, None)
        if node is not None and hasattr(node, "settimeout"):
            return node
    return None


def _transport_failure_facts(*, mutating: bool) -> dict[str, Any]:
    return {
        "failure_class": "transient",
        "retryable": not mutating,
        "definitely_no_external_effect": not mutating,
        "external_effect_status": "timeout_unknown" if mutating else "failed",
    }


__all__ = [
    "AMBIGUOUS_RESULT_CODES",
    "DUPLICATE_RESULT_CODES",
    "DeadlineReached",
    "FinvaldaTransport",
    "confirm_write",
    "dumps_json",
    "nested_result",
    "Reply",
    "error_text",
    "meaning",
    "parse_body",
    "read_bounded",
    "refusal",
    "result_code",
]
