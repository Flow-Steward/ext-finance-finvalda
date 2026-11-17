"""Review 2026-11-17, item 12: VAT that Finvalda takes from the product's or service's
card is unknown to the step, so total_vat is null with a warning, never a made-up 0."""

from __future__ import annotations

from typing import Any

from support import FakeFinvalda, items, run, saved


def _created(lines: list[dict[str, Any]]) -> dict[str, Any]:
    fake = FakeFinvalda(
        GetOperations=[items(), items()], InsertNewOperation=saved("PARD", 61, "", "S-1")
    )
    inputs = {"client_code": "K1", "document": "S-1", "date": "2026-11-10", "lines": lines}
    response = run(fake, "create_sales_invoice", inputs)
    assert response["ok"] is True, response
    return response["result"]


def test_12_vat_of_the_cards_is_null_with_a_warning() -> None:
    result = _created([{"kind": "service", "code": "S1", "quantity": 1, "price": 100}])
    assert result["total_without_vat"] == "100.00"
    assert result["total_vat"] is None
    assert any("card" in w and "VAT" in w for w in result["warnings"])


def test_12_mixed_lines_give_null_and_say_the_known_part() -> None:
    result = _created(
        [
            {"kind": "service", "code": "S1", "quantity": 1, "price": 100, "vat_percent": 21},
            {"kind": "service", "code": "S2", "quantity": 1, "price": 50},
        ]
    )
    assert result["total_vat"] is None
    assert any("21.00" in w for w in result["warnings"])


def test_12_an_explicit_zero_rate_stays_zero() -> None:
    result = _created(
        [{"kind": "service", "code": "S1", "quantity": 1, "price": 100, "vat_percent": 0}]
    )
    assert result["total_vat"] == "0.00"
    assert result["warnings"] == []
