"""The sixth review (H1-H7): attachment failures after an invoice was saved, the
product code read where it was checked, blank codes, strict lookups only before
writes, identity columns always asked for."""

from __future__ import annotations

from typing import Any

import pytest
from support import OK, FakeFinvalda, Files, items, run, saved

from runtime.everyday.common import checked_rows, row_code

_LINE = {"code": "P1", "quantity": "1", "price": "1"}


def _attached(data: str, name: str) -> dict[str, Any]:
    return {**OK, "result": {"errorCode": 0, "enitityDocs": [{"name": name, "data": data}]}}


def _own_name() -> str:
    from runtime.everyday.common import own_file_name

    return own_file_name("sale", "K1", "", "S-1")


# -- H1/H6: a file check failing after the invoice was saved names the invoice ----------


@pytest.mark.parametrize("data", ["6f6c64", "not hex"])
def test_attachment_failure_after_a_new_invoice_reports_the_invoice(data: str) -> None:
    fake = FakeFinvalda(
        GetOperations=items(),
        InsertNewOperation=saved(),
        GetAttachedDocument=_attached(data, _own_name()),
    )
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S-1",
            "warehouse": "W",
            "lines": [_LINE],
            "document_file": "a",
        },
        files=Files(b"%PDF new"),
    )
    assert response["ok"] is False
    assert response["definitely_no_external_effect"] is False
    assert "Already saved in Finvalda" in response["error"] and "PARD" in response["error"]
    assert "nothing was written" not in response["error"]
    assert [c.method for c in fake.writes()] == ["InsertNewOperation"]


def test_conflict_hint_of_an_invoice_file_needs_no_name_of_its_own() -> None:
    fake = FakeFinvalda(
        GetOperations=items(),
        InsertNewOperation=saved(),
        GetAttachedDocument=_attached(b"old".hex(), _own_name()),
    )
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S-1",
            "warehouse": "W",
            "lines": [_LINE],
            "document_file": "a",
        },
        files=Files(b"new"),
    )
    assert "replace it in Finvalda by hand" in response["error"]
    assert "Advanced · Attach a document" in response["error"]
    assert "give the new file another name" not in response["error"]


def test_attach_file_conflict_without_an_earlier_write_has_no_effect() -> None:
    fake = FakeFinvalda(GetAttachedDocument=_attached(b"old".hex(), "a.pdf"))
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "file_name": "a.pdf",
            "document_file": "x",
        },
        files=Files(b"new"),
    )
    assert response["error_code"] == "conflict"
    assert response["definitely_no_external_effect"] is True
    assert "give the new file another name" in response["error"]


# -- H2: the product code is read where its presence was checked -------------------------


def test_product_found_by_its_code_column_is_not_created_again() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "P1", "title": "Existing"}),
        InsertNewItem={**OK, "nResult": 0},
    )
    result = run(fake, "find_or_create_product", {"code": "P1", "name": "New"})["result"]
    assert result["created"] is False and fake.writes() == []


def test_row_code_reads_the_identity_columns() -> None:
    assert row_code({"product": " P1 "}, "Products") == "P1"
    assert row_code({"code": "P2"}, "Products") == "P2"
    assert row_code({"code": "   "}, "Clients") == ""


# -- H3: a blank code is no code ------------------------------------------------------------


@pytest.mark.parametrize(
    "operation_id, row",
    [
        ("find_or_create_client", {"code": "   ", "title": "X"}),
        ("find_or_create_product", {"product": " ", "title": "X"}),
        ("find_or_create_service", {"code": "\t", "title": "X"}),
    ],
)
def test_blank_codes_in_a_lookup_write_nothing(operation_id: str, row: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetDescriptions=items(row), InsertNewItem={**OK, "nResult": 0})
    response = run(fake, operation_id, {"code": "NEW", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


# -- H4: reads keep a row without its code, with a warning ---------------------------------


def test_reads_keep_rows_without_a_code() -> None:
    rows = [{"title": "no code"}, {"code": "K1", "title": "K"}]
    assert len(checked_rows(rows, "Clients", (("code",),), strict=False)) == 2
    with pytest.raises(Exception):
        checked_rows(rows, "Clients", (("code",),))


def test_product_prices_leave_out_a_row_without_code_with_a_warning() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"product": "P1"}, {"title": "no code"}))
    result = run(fake, "product_prices", {"product_codes": ["P1", "P2"]})["result"]
    assert result["count"] == 1
    assert any("1 row(s) without its product or code" in w for w in result["warnings"])


def test_changes_since_keeps_a_row_without_code_with_a_warning() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"title": "no code", "date_edited": "2026-11-10"}))
    result = run(fake, "changes_since", {"entity": "clients", "since": "2026-11-10"})["result"]
    assert result["count"] == 1 and result["warnings"]


# -- H5: the identity columns are always asked for -------------------------------------------


def test_lookups_ask_for_their_identity_columns() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"product": "P1"}))
    run(fake, "product_prices", {"product_codes": ["P1"]})
    assert "product" in fake.calls[0].read_params()["columns"]
    fake = FakeFinvalda(GetOperations=items())
    run(fake, "changes_since", {"entity": "sales", "since": "2026-11-10"})
    columns = fake.calls[0].op_params()["columns"]["column"]
    assert "journal" in columns and "op_number" in columns


# -- H7: lengths are compared before the content is decoded ---------------------------------


def test_a_longer_attached_file_differs_without_decoding() -> None:
    from runtime.advanced.files import same_content

    assert same_content({"name": "a", "data": "00" * 10}, b"abc") is False
    assert same_content({"name": "a", "data": b"abc".hex()}, b"abc") is True


def test_broken_hex_is_invalid_whatever_its_length() -> None:
    from runtime.advanced.files import same_content
    from runtime.errors import ExtensionError

    for data in ("zz" * 10, "abc", "zzzzzz"):
        with pytest.raises(ExtensionError) as caught:
            same_content({"name": "a", "data": data}, b"abc")
        assert caught.value.code == "invalid_response"
