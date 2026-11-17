"""Review 2026-11-17, item 15: an operation search that returns only other documents,
series or clients shows that the filter was not applied, not that the document is
missing; nothing is written after it."""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeFinvalda, items, run, saved

INVOICE = {
    "client_code": "K1",
    "document": "S-1001",
    "series": "AB",
    "date": "2026-11-10",
    "lines": [{"kind": "service", "code": "S1", "quantity": 1, "price": 100, "vat_percent": 21}],
}
PAYMENT = {"direction": "outgoing", "client_code": "K1", "document": "B-2", "amount": 5}


def _row(**changes: Any) -> dict[str, Any]:
    row = {"journal": "PARD", "op_number": 7, "series": "AB", "order_number": "S-1001"}
    row["client"] = "K1"
    row.update(changes)
    return row


@pytest.mark.parametrize(
    "row",
    [_row(order_number="S-9"), _row(series="ZZ")],
    ids=["other-number", "other-series"],
)
def test_15_a_sales_search_with_only_other_documents_writes_nothing(row: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetOperations=items(row), InsertNewOperation=saved())
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["error_code"] == "invalid_response", response
    assert fake.writes() == []


def test_15_a_purchase_search_with_only_other_suppliers_writes_nothing() -> None:
    purchase = {k: v for k, v in INVOICE.items() if k != "series"}  # PirkDok has no series
    row = _row(journal="PIRK", series="", client="K9")
    fake = FakeFinvalda(GetOperations=items(row), InsertNewOperation=saved())
    response = run(fake, "create_purchase_invoice", purchase)
    assert response["error_code"] == "invalid_response", response
    assert fake.writes() == []


@pytest.mark.parametrize(
    "row",
    [_row(order_number="B-9"), _row(order_number="B-2", client="K9")],
    ids=["other-number", "other-client"],
)
def test_15_a_payment_search_with_only_other_documents_writes_nothing(row: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetOperations=items(row), InsertNewOperation=saved("ISM", 3, "", "B-2"))
    response = run(fake, "register_payment", PAYMENT)
    assert response["error_code"] == "invalid_response", response
    assert fake.writes() == []
