"""Review 2026-11-17, item 7: a document found by number is this request only when its
date, currency and amount match; its lines cannot be read from the operation list, so
a match says that they were not compared."""

from __future__ import annotations

from typing import Any

from support import FakeFinvalda, items, run

LINES = [{"kind": "service", "code": "S1", "quantity": 1, "price": 100, "vat_percent": 21}]
INVOICE = {"client_code": "K1", "document": "S-1001", "date": "2026-11-10", "lines": LINES}


def _existing(**changes: Any) -> dict[str, Any]:
    row = {
        "journal": "PARD",
        "op_number": 61,
        "series": "",
        "order_number": "S-1001",
        "client": "K1",
        "currency": "EUR",
        "amount": "121.00",
        "op_date": "2026-11-10T00:00:00",
    }
    row.update(changes)
    return {k: v for k, v in row.items() if v is not None}


def test_7_the_same_number_and_amount_of_another_year_is_not_this_invoice() -> None:
    fake = FakeFinvalda(GetOperations=items(_existing(op_date="2025-11-10T00:00:00")))
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["error_code"] == "conflict", response
    assert "2025-11-10" in response["error"]
    assert fake.writes() == []


def test_7_a_document_without_a_date_cannot_be_taken_for_this_invoice() -> None:
    fake = FakeFinvalda(GetOperations=items(_existing(op_date=None)))
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["error_code"] == "conflict", response
    assert fake.writes() == []


def test_7_a_purchase_of_another_date_is_not_this_purchase() -> None:
    row = _existing(journal="PIRK", op_date="2026-01-03")
    fake = FakeFinvalda(GetOperations=items(row))
    response = run(fake, "create_purchase_invoice", INVOICE)
    assert response["error_code"] == "conflict", response
    assert fake.writes() == []


def test_7_a_matching_document_is_returned_and_says_its_lines_were_not_compared() -> None:
    fake = FakeFinvalda(GetOperations=items(_existing()))
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["ok"] is True, response
    assert response["result"]["created"] is False
    assert any("lines" in w and "not compared" in w for w in response["result"]["warnings"])
    assert fake.writes() == []
