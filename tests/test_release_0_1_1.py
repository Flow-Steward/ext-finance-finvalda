"""0.1.1: the small items of the last review of 0.1.0."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from support import OK, FakeFinvalda, Files, items, run

from runtime.advanced.files import attached_size
from runtime.errors import ExtensionError
from runtime.inputs import decimal_input
from runtime.transport import result_code
from runtime.values import as_decimal


# 1: an empty read-back after a create is said so
@pytest.mark.parametrize(
    "operation_id, key",
    [
        ("find_or_create_product", "product"),
        ("find_or_create_service", "service"),
        ("find_or_create_client", "client"),
    ],
)
def test_empty_read_back_is_warned(operation_id: str, key: str) -> None:
    fake = FakeFinvalda(GetDescriptions=[items(), items()], InsertNewItem={**OK, "nResult": 0})
    result = run(fake, operation_id, {"code": "P1", "name": "New"})["result"]
    assert result["created"] is True and result[key] is None
    assert result["warnings"] == [f"{key} P1 was saved, but reading it back found no record"]


# 2: a lookup answer with only other values is invalid for clients too
def test_client_lookup_with_only_other_codes_writes_nothing() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "C10"}), InsertNewItem={**OK, "nResult": 0})
    response = run(fake, "find_or_create_client", {"code": "C1", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


# 3: a result code is read; a code that cannot be read is never taken for success
# (R1 of the next review: unknown after a write, invalid after a read)
@pytest.mark.parametrize(
    "data, code",
    [
        ({"nResult": "0"}, 0),
        ({"nResult": "12"}, 12),
        ({"nResult": Decimal("0.0")}, 0),
        ({"errorCode": "3"}, 3),
        ({"nResult": "", "errorCode": "3"}, 3),
    ],
)
def test_result_codes(data: dict[str, Any], code: Any) -> None:
    assert result_code(data) == code


@pytest.mark.parametrize("value", ["9" * 30, "١", "x", "0.5"])
def test_unreadable_result_codes_raise(value: str) -> None:
    from runtime.transport import UnreadableCode

    with pytest.raises(UnreadableCode):
        result_code({"nResult": value})


def test_unreadable_code_on_a_read_is_a_failure() -> None:
    fake = FakeFinvalda(GetSandelius={"AccessResult": "Success", "nResult": "9" * 30, "items": []})
    assert run(fake, "get_sandelius")["ok"] is False


# 4: very large amounts are kept exactly
def test_large_amounts_are_kept() -> None:
    assert as_decimal("1E+50") == "1E+50"
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K"}),
        GetNeapmoketiKliDok={
            **OK,
            "nResult": 0,
            "items": [{"dokumentas": "S1", "likusi_skola": "1E+50"}],
        },
    )
    result = run(fake, "client_debt", {"client_code": "K1"})["result"]
    # Kept exactly in the document; too large to be a debt, so not in the totals.
    assert result["count"] == 1 and result["documents"][0]["remaining"] == "1E+50"
    assert result["unpaid_total"] == "0.00" and result["warnings"]


# 5: tiny and huge input numbers get the right message
def test_tiny_and_huge_input_numbers() -> None:
    assert decimal_input("x", Decimal("0E-45")) == 0
    with pytest.raises(ExtensionError, match="decimal places"):
        decimal_input("x", Decimal("1e-45"))
    with pytest.raises(ExtensionError, match="decimal places"):
        decimal_input("x", 1e-45)
    with pytest.raises(ExtensionError, match="too large"):
        decimal_input("x", Decimal("1E+50"))


# 6/7: sizes through the same hex reading; too large before decoding
def test_attached_size_of_broken_hex_is_invalid() -> None:
    with pytest.raises(ExtensionError):
        attached_size({"name": "a", "data": "zz"})


def test_attached_file_too_large_is_refused_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.advanced import files

    monkeypatch.setattr(files, "MAX_DOWNLOAD_BYTES", 2)
    fake = FakeFinvalda(
        GetAttachedDocument={
            **OK,
            "result": {"errorCode": 0, "enitityDocs": [{"name": "a.pdf", "data": "00 01 02"}]},
        }
    )
    response = run(
        fake,
        "attached_documents",
        {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"},
        files=Files(),
    )
    assert response["error_code"] == "response_too_large"


# 10: no '#None'
def test_payment_conflict_without_a_number_shows_a_question_mark() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"order_number": "B", "client": "K1", "journal": "IPL", "op_number": " "}
        )
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
    )
    assert "None" not in response["error"]


def test_cents_of_any_amount() -> None:
    from runtime.values import cents

    assert cents(Decimal("1E+30")) == Decimal("1E+30")
    assert (
        str(cents(Decimal("12345678901234567890123456789.005")))
        == "12345678901234567890123456789.01"
    )
    assert cents(Decimal("1E+60")) == Decimal("1E+60")
