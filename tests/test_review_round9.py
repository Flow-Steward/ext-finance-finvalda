"""The ninth review (M1-M8): huge exponents, hex with spaces inside a byte, a
confirmed create that cannot be read back, one reading of whole numbers, short
identities, other codes next to one's own, client prices of other products."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest
from support import OK, FakeFinvalda, items, run, saved

from runtime.advanced.files import attached_content, attached_size, same_content
from runtime.everyday.common import _op_number
from runtime.rows import op_result
from runtime.shapes import to_kind
from runtime.values import whole_of

# -- M1: a huge exponent is no whole number, and is refused at once ----------------------


@pytest.mark.parametrize("value", ["1E+50000000", Decimal("1E+50000000"), "1e400", "9" * 25])
def test_huge_numbers_are_not_whole_and_return_at_once(value: Any) -> None:
    started = time.monotonic()
    assert whole_of(value) is None
    assert _op_number({"op_number": value}) is None
    assert to_kind("integer", value) in {str(value), "1E+50000000", "1E+400"}
    assert time.monotonic() - started < 1


def test_largest_whole_numbers_still_read() -> None:
    assert whole_of("9" * 19) == int("9" * 19)
    assert whole_of(Decimal("1E+18")) == 10**18


def test_huge_result_code_does_not_hang() -> None:
    from runtime.transport import result_code

    started = time.monotonic()
    assert result_code({"nResult": "9" * 5000}) == -1
    assert time.monotonic() - started < 1


def test_huge_whole_input_is_refused() -> None:
    from runtime.errors import ExtensionError
    from runtime.inputs import whole_input

    with pytest.raises(ExtensionError):
        whole_input("page", Decimal("1E+50000000"), minimum=1, maximum=10)


# -- M2: hex with a space inside a byte ---------------------------------------------------


def test_space_inside_a_byte_is_read_like_everywhere_else() -> None:
    attached = {"name": "a", "data": "4 1\n4 2"}
    assert attached_content(attached) == b"AB"
    assert attached_size(attached) == 2
    assert same_content(attached, b"AB") is True


# -- M3: a confirmed create stays a success when its read-back does not show it ----------


@pytest.mark.parametrize(
    "operation_id, answer",
    [
        ("find_or_create_product", [items(), items({"product": "P9", "title": "Other"})]),
        ("find_or_create_service", [items(), items({"code": "P1"}, {"code": "P1"})]),
        ("find_or_create_client", [items(), items({"code": "P9", "title": "Other"})]),
    ],
)
def test_confirmed_create_with_a_wrong_read_back_is_ok(
    operation_id: str, answer: list[Any]
) -> None:
    fake = FakeFinvalda(GetDescriptions=answer, InsertNewItem={**OK, "nResult": 0})
    response = run(fake, operation_id, {"code": "P1", "name": "New"})
    assert response["ok"] is True, response
    result = response["result"]
    assert result["created"] is True
    record = result.get("product", result.get("service", result.get("client")))
    assert record is None
    assert any(
        "was saved, but reading it back failed: invalid_response" in w for w in result["warnings"]
    )


# -- M4: one reading of whole numbers --------------------------------------------------------


def test_saved_operation_number_reads_like_any_other() -> None:
    saved_answer = {**OK, "OP_DUOMENYS": {"ZURNALAS": "IPL", "NUMERIS": "61.0"}, "nResult": 0}
    assert op_result(saved_answer)["op_number"] == 61
    assert op_result({**saved_answer, "OP_DUOMENYS": {"NUMERIS": "61.5"}})["op_number"] is None


def test_payment_read_back_matches_a_number_sent_as_61_0() -> None:
    fake = FakeFinvalda(
        GetOperations=[
            items(),
            items(
                {
                    "order_number": "B",
                    "client": "K1",
                    "journal": "IPL",
                    "op_number": 61,
                    "amount": 10,
                }
            ),
        ],
        InsertNewOperation={
            **saved(journal="IPL", number=61),
            "OP_DUOMENYS": {"ZURNALAS": "IPL", "NUMERIS": "61.0"},
        },
    )
    result = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "10"},
    )["result"]
    assert result["payment"]["op_number"] == 61


# -- M5: identities in a conflict text are short ---------------------------------------------


def test_long_odd_number_keeps_the_hint() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"order_number": "B", "client": "K1", "journal": "IPL", "op_number": "X" * 500}
        )
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
    )
    assert "X" * 21 not in response["error"]
    assert "give it a document number of its own" in response["error"]


# -- M6: rows of other codes next to one's own are warned ------------------------------------


def test_other_codes_next_to_the_own_row_are_warned() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"product": "P9"}, {"product": "P1", "title": "Mine"})
    )
    result = run(fake, "find_or_create_product", {"code": "P1"})["result"]
    assert result["product"]["name"] == "Mine"
    assert result["warnings"] == [
        "Finvalda's Products answer for P1 holds 1 row(s) of other codes; they are left out"
    ]


def test_only_other_codes_are_invalid() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "S9"}), InsertNewItem={**OK, "nResult": 0})
    response = run(fake, "find_or_create_service", {"code": "S1", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


# -- M7: client prices of products not asked for are warned ----------------------------------


def test_client_prices_of_other_products_are_warned() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1"}),
            items(
                {"client": "K1", "code": "P1", "price": 1},
                {"client": "K1", "code": "P7", "price": 7},
            ),
        ]
    )
    result = run(fake, "product_prices", {"product_codes": ["P1"], "client_code": "K1"})["result"]
    assert [p["code"] for p in result["client_prices"]] == ["P1"]
    assert result["warnings"] == [
        "Finvalda returned 1 price row(s) of products not asked for; they are left out"
    ]


# -- M8: rows equal in the key are ordered by their JSON ---------------------------------------


def test_rows_equal_in_the_key_keep_one_order() -> None:
    from runtime.everyday.reads import _fixed_order

    rows = [{"title": "b"}, {"title": "a"}, {"code": "C"}]
    assert _fixed_order(list(rows), "Clients") == _fixed_order(list(reversed(rows)), "Clients")


def test_huge_number_in_an_answer_or_an_input_returns_at_once() -> None:
    from runtime.errors import ExtensionError
    from runtime.inputs import decimal_input
    from runtime.values import exact

    started = time.monotonic()
    fake = FakeFinvalda(
        GetKlientusSet=(
            200,
            b'{"AccessResult":"Success","items":[{"kodas":"K1","skola":1e50000000}]}',
        )
    )
    result = run(fake, "get_klientus_set")["result"]
    assert result["records"][0]["skola"] == "1E+50000000"
    assert exact(Decimal("1E+50000000")) == "1E+50000000"
    with pytest.raises(ExtensionError):
        decimal_input("amount", Decimal("1E+50000000"))
    assert time.monotonic() - started < 2
