"""A fake Finvalda web service and invocation helpers for the tests."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

from runtime.operations import handle_runtime

BUNDLE = Path(__file__).resolve().parents[1]
PASSWORD = "fixture-Password-123"  # pragma: allowlist secret
BASE_URL = "https://fvs.example.lt/FvsServicePure.svc"
OK: dict[str, Any] = {"AccessResult": "Success", "error": ""}


def saved(
    journal: str = "PARD", number: int = 61, series: str = "AB", document: str = "S-1"
) -> dict[str, Any]:
    return {
        "AccessResult": "Success",
        "error": f"<OP_DUOMENYS><SERIJA>{series}</SERIJA><DOKUMENTAS>{document}</DOKUMENTAS>"
        f"<ZURNALAS>{journal}</ZURNALAS><NUMERIS>{number}</NUMERIS></OP_DUOMENYS>",
        "OP_DUOMENYS": {
            "SERIJA": series,
            "DOKUMENTAS": document,
            "ZURNALAS": journal,
            "NUMERIS": str(number),
        },
        "nResult": 0,
    }


def refused(code: int, text: str = "refused") -> dict[str, Any]:
    return {"AccessResult": "Fail", "error": text, "nResult": code}


def items(*rows: dict[str, Any]) -> dict[str, Any]:
    return {**OK, "items": list(rows)}


class Call:
    def __init__(self, request: Any) -> None:
        parts = urlsplit(request.full_url)
        self.url = request.full_url
        self.method = parts.path.rsplit("/", 1)[-1]
        self.verb = request.get_method()
        self.query = dict(parse_qsl(parts.query))
        self.headers = {k.lower(): v for k, v in request.header_items()}
        self.raw_body = request.data or b""
        self.body = json.loads(request.data.decode()) if request.data else None

    def inner(self) -> Any:
        """The decoded xmlstring of a write body."""
        return (
            json.loads(self.body["xmlstring"]) if self.body and "xmlstring" in self.body else None
        )

    def read_params(self) -> dict[str, Any]:
        assert self.body is not None
        return dict(self.body["readParams"])

    def op_params(self) -> dict[str, Any]:
        assert self.body is not None
        return dict(self.body["opReadParams"])


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = Message()

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = (
            (self._body, b"") if size < 0 else (self._body[:size], self._body[size:])
        )
        return chunk

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


Answer = Any


class FakeFinvalda:
    """Answers by method name; a list of answers is used in order."""

    def __init__(self, **routes: Answer) -> None:
        self.routes: dict[str, Any] = {
            k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()
        }
        self.calls: list[Call] = []

    def __call__(self, request: Any, *, timeout_seconds: float, purpose: str) -> Response:
        call = Call(request)
        self.calls.append(call)
        route = self.routes.get(call.method)
        if route is None:
            raise AssertionError(f"unexpected call to {call.method}")
        answer = route.pop(0) if isinstance(route, list) else route
        if isinstance(answer, Callable) and not isinstance(answer, BaseException):  # type: ignore[arg-type]
            answer = answer(call)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 300:
            raise HTTPError(request.full_url, status, "error", Message(), io.BytesIO(raw))
        return Response(status, raw)

    def methods(self) -> list[str]:
        return [c.method for c in self.calls]

    def writes(self) -> list[Call]:
        return [c for c in self.calls if c.method in WRITE_METHODS]


WRITE_METHODS = {
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
    "InsertDocument",
    "DeleteDocument",
    "AttachDocument",
}


def payload(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    test_mode: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
            "test_mode": test_mode,
        },
        "action": {
            "action_id": operation_id,
            "input": {"connection_ref": "c1", **(inputs or {})},
            "target": {
                "connection": {
                    "connection_id": "c1",
                    "connection_type_id": "finvalda",
                    "config": {
                        "base_url": BASE_URL,
                        "user_name": "WEB",
                        "company_id": "IMONE",
                        "operation_parameter": "PARAM_PARD",
                        "item_parameter": "PARAM_APR",
                        "fin_user": "BUHALT",
                        **(config or {}),
                    },
                    "secrets": {"password": PASSWORD},
                }
            },
        },
    }


class Files:
    """An in-memory artifact plane."""

    def __init__(self, content: bytes = b"%PDF-1.4 test") -> None:
        self.content = content
        self.written: list[dict[str, Any]] = []

    def reader(self, payload: Any, *, artifact_id: str, timeout_seconds: float) -> bytes:
        return self.content

    def writer(self, payload: Any, chunks: list[bytes], **kw: Any) -> dict[str, Any]:
        self.written.append({"data": b"".join(chunks), **kw})
        return {"artifact_handle": f"art_{len(self.written)}"}


def run(
    fake: FakeFinvalda,
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    files: Files | None = None,
    **kw: Any,
) -> dict[str, Any]:
    from runtime.transport import FinvaldaTransport

    files = files or Files()
    return handle_runtime(
        payload(operation_id, inputs, **kw),
        transport_factory=lambda c: FinvaldaTransport(c, opener=fake, sleeper=lambda _s: None),
        artifact_reader=files.reader,
        artifact_writer=files.writer,
    )
