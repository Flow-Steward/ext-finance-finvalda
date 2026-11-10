"""Project-scoped Finvalda connection: one company database through one web service user.

A connection is built from what the host hydrates for one invocation and is
discarded when that invocation ends. The password lives in a private field of a
frozen dataclass whose ``repr`` never shows it, and it is never placed in a
result, a log line or an exception message.

The web service runs on the customer's own server (Finvalda installs it on IIS
next to the database). Its address must be https, a host name (no IP literal,
no localhost) and the path of the FvsServicePure interface, for example
``https://fvs.example.lt/FvsServicePure.svc``; the SDK's pinned opener
additionally refuses any host that resolves to a private or reserved address.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import errors
from .errors import ExtensionError

CONNECTION_TYPE_ID = "finvalda"
CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "label",
        "base_url",
        "user_name",
        "company_id",
        "operation_parameter",
        "item_parameter",
        "fin_user",
        "language",
    }
)
SECRET_FIELDS: frozenset[str] = frozenset({"password"})
#: Finvalda answers with Lithuanian (0) or English (1) field names on the older methods.
LANGUAGES: dict[str, str] = {"lt": "0", "en": "1"}

MAX_SECRET_LENGTH = 256
#: Shorter passwords could not be removed from answers without mangling them.
MIN_SECRET_LENGTH = 4
#: Printable ASCII, so a header can never be split (no CR, LF or control byte).
_HEADER_VALUE_RE = re.compile(r"^[\x20-\x7e]+$")
_CODE_RE = re.compile(r"^[\x21-\x7e]{1,64}$")
_PARAMETER_RE = re.compile(r"^[\x20-\x7e]{1,100}$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/[A-Za-z0-9._~-]+\.svc$")


@dataclass(frozen=True)
class Connection:
    """One validated Finvalda web service connection."""

    connection_id: str
    base_url: str
    user_name: str
    company_id: str
    operation_parameter: str
    item_parameter: str
    fin_user: str
    language: str
    _password: str = field(repr=False)

    def secret_fragments(self) -> tuple[str, ...]:
        """The password, for redacting text Finvalda might echo back."""
        return (self._password,)

    def headers(self) -> dict[str, str]:
        """The documented request headers (Postman collection variables)."""
        headers = {
            "UserName": self.user_name,
            "Password": self._password,
            "Language": LANGUAGES[self.language],
            "RemoveEmptyStringTags": "false",
            "RemoveZeroNumberTags": "false",
            "RemoveNewLines": "true",
        }
        if self.company_id:
            headers["CompanyID"] = self.company_id
        return headers

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"Connection(connection_id={self.connection_id!r})"

    __str__ = __repr__


def _invalid(message: str) -> ExtensionError:
    return errors.no_effect(errors.INVALID_CONNECTION, message)


def base_url_of(value: Any) -> str:
    """https://host[:port]/…/Name.svc, validated; anything else is refused before any request."""
    text = value.strip().rstrip("/") if isinstance(value, str) else ""
    if not text:
        raise _invalid(
            "Add the Finvalda web service address (for example "
            "https://fvs.example.lt/FvsServicePure.svc)"
        )
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise _invalid("The Finvalda web service address must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid("The address may not contain credentials, a query or a fragment")
    if not _PATH_RE.fullmatch(parts.path) or any(
        segment in {".", ".."} for segment in parts.path.split("/")
    ):
        raise _invalid(
            "The address must end with the web service interface, for example /FvsServicePure.svc"
        )
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _invalid("The Finvalda web service address must use a host name, not an IP address")
    labels = host.split(".")
    numeric = all(re.fullmatch(r"(0x[0-9a-f]*|[0-9]+)", label) for label in labels)
    if numeric or not re.search(r"[a-z]", labels[-1]) or labels[-1].startswith("0x"):
        raise _invalid("The Finvalda web service address must use a host name, not an IP address")
    if host == "localhost" or host.endswith((".localhost", ".local")) or not _HOST_RE.match(host):
        raise _invalid("The Finvalda web service address is not a public host name")
    try:
        port = parts.port
    except ValueError as exc:
        raise _invalid("The port in the address is not valid") from exc
    authority = host + (f":{port}" if port and port != 443 else "")
    return f"https://{authority}{parts.path}"


def _header_value(value: Any, what: str, *, required: bool, pattern: re.Pattern[str]) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        if required:
            raise _invalid(f"Add the {what} to this connection")
        return ""
    if not pattern.fullmatch(text):
        raise _invalid(f"The {what} may use printable ASCII characters only")
    return text


def _password(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    if not text:
        raise _invalid("Add the web service user's password to this connection")
    if (
        len(text) < MIN_SECRET_LENGTH
        or len(text) > MAX_SECRET_LENGTH
        or not _HEADER_VALUE_RE.fullmatch(text)
    ):
        raise _invalid(
            "The password is not usable: 4 to 256 printable ASCII characters "
            "(a web service open to the internet needs a real password)"
        )
    return text


def _language(value: Any) -> str:
    if value in (None, ""):
        return "lt"
    if isinstance(value, str) and value.strip().lower() in LANGUAGES:
        return value.strip().lower()
    raise _invalid("language must be lt or en")


def connection_from_payload(payload: Mapping[str, Any], *, connection_ref: str) -> Connection:
    """Validate the selected project connection before any outbound request."""
    action = payload.get("action")
    target = action.get("target") if isinstance(action, Mapping) else None
    connection = target.get("connection") if isinstance(target, Mapping) else None
    if not isinstance(connection, Mapping):
        raise _invalid("The selected connection is unavailable")
    connection_id = str(connection.get("connection_id") or "")
    if not connection_id or (connection_ref and connection_id != connection_ref):
        raise _invalid("The selected connection does not match connection_ref")
    connection_type = str(
        connection.get("connection_type_id") or connection.get("connection_type") or ""
    )
    if connection_type and connection_type != CONNECTION_TYPE_ID:
        raise _invalid("The selected connection is not supported")
    config = connection.get("config")
    config = config if isinstance(config, Mapping) else {}
    secrets = connection.get("secrets")
    secrets = secrets if isinstance(secrets, Mapping) else {}
    if set(secrets) - SECRET_FIELDS:
        raise _invalid("The connection carries unsupported secrets")
    return Connection(
        connection_id=connection_id,
        base_url=base_url_of(config.get("base_url")),
        user_name=_header_value(
            config.get("user_name"), "web service user name", required=True, pattern=_CODE_RE
        ),
        company_id=_header_value(
            config.get("company_id"), "company id", required=False, pattern=_CODE_RE
        ),
        operation_parameter=_header_value(
            config.get("operation_parameter"),
            "operation import parameter",
            required=False,
            pattern=_PARAMETER_RE,
        ),
        item_parameter=_header_value(
            config.get("item_parameter"),
            "item import parameter",
            required=False,
            pattern=_PARAMETER_RE,
        ),
        fin_user=_header_value(
            config.get("fin_user"), "Finvalda employee", required=False, pattern=_CODE_RE
        ),
        language=_language(config.get("language")),
        _password=_password(secrets.get("password")),
    )


__all__ = [
    "CONFIG_FIELDS",
    "CONNECTION_TYPE_ID",
    "LANGUAGES",
    "SECRET_FIELDS",
    "Connection",
    "base_url_of",
    "connection_from_payload",
]
