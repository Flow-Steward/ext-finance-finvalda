"""The seventh review (K1-K10): hex with spaces, rows without a code in prices and
debt, strict lookups by default, one warning per call, stripped codes, one code
rule everywhere, safe operation identities."""

from __future__ import annotations

from typing import Any

import pytest
from support import OK, FakeFinvalda, Files, items, run, saved

from runtime.advanced.files import same_content
from runtime.everyday import common

_ATTACH = {
    "entity_type": 3,
    "entity_id1": "PARD",
    "entity_id2": "1",
    "file_name": "a.pdf",
    "document_file": "art_1",
}


# -- K1: hex with spaces and line breaks is the same file ----------------------------------


@pytest.mark.parametrize("spaced", ["25 50 44 46", "2550\n4446", "25504446\r\n", " 2550 4446 "])
def test_hex_with_spaces_is_decoded(spaced: str) -> None:
    assert same_content({"name": "a", "data": spaced}, b"%PDF") is True


def test_attach_file_with_spaced_hex_is_already_attached() -> None:
    attached = {
        **OK,
        "result": {"errorCode": 0, "enitityDocs": [{"name": "a.pdf", "data": "25 50\n44 46"}]},
    }
    fake = FakeFinvalda(GetAttachedDocument=attached)
    result = run(fake, "attach_file", _ATTACH, files=Files(b"%PDF"))["result"]
    assert result["stored"] is False and fake.writes() == []


# -- K2: rows without a code are no prices of the products asked for --------------------


def test_prices_count_every_row_against_the_codes_asked_for() -> None:
    rows = [{"title": f"other {n}", "sell_price1": n} for n in range(5)]
    fake = FakeFinvalda(GetDescriptions=items({"product": "P1"}, *rows))
    response = run(fake, "product_prices", {"product_codes": ["P1"]})
    assert response["error_code"] == "invalid_response"


def test_client_prices_without_a_code_are_left_out_with_one_warning() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1"}),
            items(
                {"client": "K1", "code": "P1", "price": 1},
                {"client": "K1", "price": 2},
                {"client": "K1", "price": 3},
            ),
        ]
    )
    result = run(fake, "product_prices", {"product_codes": ["P1"], "client_code": "K1"})["result"]
    assert [p["code"] for p in result["client_prices"]] == ["P1"]
    assert result["warnings"] == ["Finvalda's Prices answer holds 2 row(s) without its code"]


# -- K3: client debt never says "no client" for a row without its code -------------------


def test_client_debt_with_a_row_without_code_is_invalid() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"title": "K", "debt": 5}))
    response = run(fake, "client_debt", {"client_code": "K1"})
    assert response["error_code"] == "invalid_response"


# -- K4: strict by default; reads say so ------------------------------------------------------


def test_lookups_are_strict_by_default() -> None:
    import inspect

    for function in (common.read_list, common.describe, common.operations_of):
        assert inspect.signature(function).parameters["strict"].default is True, function


@pytest.mark.parametrize(
    "operation_id, inputs, route",
    [
        ("find_or_create_client", {"code": "NEW", "name": "N"}, "GetDescriptions"),
        ("find_or_create_product", {"code": "NEW", "name": "N"}, "GetDescriptions"),
        ("find_or_create_service", {"code": "NEW", "name": "N"}, "GetDescriptions"),
        (
            "create_sales_invoice",
            {
                "client_code": "K1",
                "document": "S-1",
                "warehouse": "W",
                "lines": [{"code": "P1", "quantity": "1", "price": "1"}],
            },
            "GetOperations",
        ),
        (
            "register_payment",
            {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
            "GetOperations",
        ),
        (
            "create_sales_invoice",
            {
                "client_company_code": "300",
                "document": "S-1",
                "warehouse": "W",
                "lines": [{"code": "P1", "quantity": "1", "price": "1"}],
            },
            "GetDescriptions",
        ),
    ],
)
def test_every_lookup_before_a_write_is_strict(
    operation_id: str, inputs: dict[str, Any], route: str
) -> None:
    fake = FakeFinvalda(
        **{route: items({"title": "no code", "order_number": "S-1", "client": "K1"})},
        InsertNewItem={**OK, "nResult": 0},
        InsertNewOperation=saved(),
    )
    response = run(fake, operation_id, inputs)
    assert response["error_code"] == "invalid_response", response
    assert fake.writes() == []


# -- K5: one warning per call ------------------------------------------------------------------


def test_many_rows_without_a_code_make_one_warning() -> None:
    rows = [{"title": f"t{n}", "date_edited": "2026-11-10"} for n in range(40)]
    fake = FakeFinvalda(GetDescriptions=items(*rows))
    result = run(fake, "changes_since", {"entity": "clients", "since": "2026-11-10"})["result"]
    assert result["warnings"] == ["Finvalda's Clients answer holds 40 row(s) without its code"]


# -- K6: a client code found by company code is stripped -----------------------------------


def test_client_code_found_by_company_code_is_stripped() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "ABC   ", "company_code": "300"}),
        GetOperations=items(),
        InsertNewOperation=saved(),
    )
    run(
        fake,
        "create_sales_invoice",
        {
            "client_company_code": "300",
            "document": "S-1",
            "warehouse": "W",
            "lines": [{"code": "P1", "quantity": "1", "price": "1"}],
        },
    )
    assert fake.writes()[0].inner()["PardDok"]["sKlientas"] == "ABC"


# -- K7: product before code, everywhere -----------------------------------------------------


def test_barcode_owner_is_read_as_the_product_code() -> None:
    from runtime.everyday.parties import barcode_owners

    row = {"product": "P1", "code": "BAR-ROW-ID", "barcode": "4770000000001"}
    assert barcode_owners([row], "4770000000001") == ["P1"]


def test_changes_order_reads_the_product_first() -> None:
    from runtime.everyday.reads import _change_key

    assert _change_key({"product": "B", "code": "A"})[0] == "B"


# -- K8: an operation's identity is safe text -------------------------------------------------


def test_payment_conflict_shows_an_odd_number_as_sent() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"order_number": "B", "client": "K1", "journal": "IPL\x07", "op_number": "12/A"}
        )
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
    )
    assert response["error_code"] == "conflict"
    assert "#12/A" in response["error"] and "None" not in response["error"]
    assert "\x07" not in response["error"]


# -- K10: conflict texts show the code that was compared -----------------------------------


def test_client_conflict_names_the_stripped_code() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), items({"code": " C1 ", "company_code": "300000001"})]
    )
    response = run(
        fake, "find_or_create_client", {"code": "C2", "name": "X", "company_code": "300000001"}
    )
    assert "but client C1 has" in response["error"]
