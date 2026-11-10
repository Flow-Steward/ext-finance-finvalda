"""One invocation's ports: its input, its Finvalda transport and the artifact plane."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from .connection import Connection
from .transport import FinvaldaTransport, Reply

TransportFactory = Callable[[Connection], FinvaldaTransport]

ArtifactWriter = Callable[..., Mapping[str, Any]]


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
