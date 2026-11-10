"""The eighth review (L1-L9): a fixed order for changes, client prices of this
client only, the read-back row of its own code, whole operation numbers, prices
with other products, attachment sizes."""

from __future__ import annotations

import random
from typing import Any

import pytest
from support import OK, FakeFinvalda, items, run, saved

from runtime.advanced.files import attached_size, same_content
from runtime.everyday.common import _op_number

# -- L1/L10: every row once, whatever order Finvalda answers in ---------------------------


def test_rows_without_a_code_are_read_once_in_any_order() -> None:
    rows = [{"title": f"t{n}", "date_edited": "2026-11-10"} for n in range(400)]

    def answer(call: Any) -> dict[str, Any]:
        shuffled = list(rows)
        random.shuffle(shuffled)
        return items(*shuffled)

    fake = FakeFinvalda(GetDescriptions=answer)
    seen: list[str] = []
    page: int | None = 1
    while page:
        result = run(
            fake,
            "changes_since",
            {"entity": "clients", "since": "2026-11-10", "limit": 200, "page": page},
        )["result"]
        seen += [r["name"] for r in result["records"]]
        page = result["next_page"]
    assert sorted(seen) == sorted(r["title"] for r in rows) and len(seen) == 400


def test_operations_with_odd_numbers_are_read_once_in_any_order() -> None:
    rows: list[dict[str, Any]] = [
        {"journal": "PARD", "op_number": f"{n}/A", "date_edited": "2026-11-10"} for n in range(30)
    ]
    rows += [
        {"journal": "PARD", "op_number": n + 0.5, "date_edited": "2026-11-10"} for n in range(30)
    ]

    def answer(call: Any) -> dict[str, Any]:
        shuffled = list(rows)
        random.shuffle(shuffled)
        return items(*shuffled)

    fake = FakeFinvalda(GetOperations=answer)
    first = run(fake, "changes_since", {"entity": "sales", "since": "2026-11-10", "limit": 30})[
        "result"
    ]
    second = run(
        fake, "changes_since", {"entity": "sales", "since": "2026-11-10", "limit": 30, "page": 2}
    )["result"]
    assert first["count"] + second["count"] == 60 and second["has_more"] is False


def test_changes_order_uses_the_entitys_own_code() -> None:
    from runtime.everyday.reads import _change_key

    assert _change_key("Clients")({"product": "B", "code": "A"})[0] == "A"
    assert _change_key("Products")({"product": "B", "code": "A"})[0] == "B"


# -- L2: special prices of this client only -----------------------------------------------


def test_client_prices_without_or_with_another_client_are_left_out() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1"}),
            items(
                {"client": "K1", "code": "P1", "price": 1},
                {"code": "P1", "price": 2},
                {"client": "", "code": "P1", "price": 3},
                {"client": "K2", "code": "P1", "price": 4},
            ),
        ]
    )
    result = run(fake, "product_prices", {"product_codes": ["P1"], "client_code": "K1"})["result"]
    assert [p["price"] for p in result["client_prices"]] == ["1"]
    assert result["warnings"] == [
        "Finvalda returned 3 price row(s) without client K1; they are left out"
    ]


# -- L3: the row of the code asked for -------------------------------------------------------


def test_barcode_path_returns_the_row_of_its_code() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1", "barcode": "4770000000001"}),
            items({"product": "P9", "title": "Other"}, {"product": "P1", "title": "Mine"}),
        ]
    )
    result = run(fake, "find_or_create_product", {"barcode": "4770000000001"})["result"]
    assert result["product_code"] == "P1" and result["product"]["name"] == "Mine"


def test_barcode_path_without_the_row_of_its_code_is_invalid() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1", "barcode": "4770000000001"}),
            items({"product": "P9", "title": "Other"}),
        ]
    )
    response = run(fake, "find_or_create_product", {"barcode": "4770000000001"})
    assert response["error_code"] == "invalid_response"


@pytest.mark.parametrize(
    "operation_id, answer",
    [
        ("find_or_create_product", [items(), items({"product": "P9", "title": "Other"})]),
        ("find_or_create_service", [items(), items({"code": "P9"}, {"code": "P9"})]),
        ("find_or_create_client", [items(), items({"code": "P9", "title": "Other"})]),
    ],
)
def test_read_back_of_another_record_names_what_was_saved(
    operation_id: str, answer: list[Any]
) -> None:
    fake = FakeFinvalda(GetDescriptions=answer, InsertNewItem={**OK, "nResult": 0})
    response = run(fake, operation_id, {"code": "P1", "name": "New"})
    assert response["ok"] is False and response["error_code"] == "invalid_response"
    assert "Already saved in Finvalda" in response["error"]
    assert response["definitely_no_external_effect"] is False


# -- L4: an operation number is never truncated -----------------------------------------------


@pytest.mark.parametrize(
    "value, number", [(12, 12), ("12", 12), (12.0, 12), (12.5, None), (12.7, None), ("12/A", None)]
)
def test_op_number_is_whole_or_none(value: Any, number: Any) -> None:
    assert _op_number({"op_number": value}) == number


def test_payment_read_back_matches_the_exact_number() -> None:
    fake = FakeFinvalda(
        GetOperations=[
            items(),
            items(
                {"order_number": "B", "client": "K1", "journal": "IPL", "op_number": 61.5},
                {
                    "order_number": "B",
                    "client": "K1",
                    "journal": "IPL",
                    "op_number": 61,
                    "amount": 10,
                },
            ),
        ],
        InsertNewOperation=saved(journal="IPL", number=61),
    )
    result = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "10"},
    )["result"]
    assert result["payment"]["op_number"] == 61 and result["payment"]["amount"] == "10"


# -- L5: a product not asked for is left out with a warning ---------------------------------


def test_prices_leave_out_another_product_with_a_warning() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"product": "P1"}, {"product": "P2"}))
    result = run(fake, "product_prices", {"product_codes": ["P1"]})["result"]
    assert [i["code"] for i in result["items"]] == ["P1"]
    assert result["warnings"] == [
        "Finvalda returned 1 row(s) of products not asked for; they are left out"
    ]


def test_prices_with_two_rows_of_one_code_are_invalid() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"product": "P1"}, {"product": "p1"}))
    assert (
        run(fake, "product_prices", {"product_codes": ["P1"]})["error_code"] == "invalid_response"
    )


# -- L6: a read's strict error does not talk about writes -----------------------------------


def test_client_debt_error_does_not_mention_writes() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"title": "K"}))
    response = run(fake, "client_debt", {"client_code": "K1"})
    assert response["error_code"] == "invalid_response" and "written" not in response["error"]


# -- L7/L8: sizes from hex without spaces, compared before decoding --------------------------


def test_attached_size_ignores_spaces() -> None:
    assert attached_size({"name": "a", "data": "25 50\n44 46"}) == 4


def test_different_size_is_not_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.advanced import files

    def boom(_attached: Any) -> bytes:
        raise AssertionError("decoded")

    monkeypatch.setattr(files, "attached_content", boom)
    assert same_content({"name": "a", "data": "00 " * 10}, b"abc") is False


# -- L9: one path for an operation's identity ----------------------------------------------


def test_payment_conflict_without_a_journal_shows_a_question_mark() -> None:
    fake = FakeFinvalda(
        GetOperations=items({"order_number": "B", "client": "K1", "journal": "IPL", "op_number": 5})
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
    )
    assert "IPL #5" in response["error"] and "None" not in response["error"]
