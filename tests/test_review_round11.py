"""The review of 7e917ba (S1-S6): secondary client values compared as Finvalda keeps
them, access refusals first, warnings declared everywhere, codes that are flags or
objects, number texts."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from support import BUNDLE, OK, FakeFinvalda, items, run

from runtime.everyday.common import same_client_value
from runtime.inputs import decimal_input
from runtime.transport import result_code

NEW = {"code": "NEW", "name": "New"}


# -- S1: a doubtful secondary match is a conflict; normalised exact match is the client --


@pytest.mark.parametrize(
    "column, found, wanted, same",
    [
        ("email", "a@b.lt; c@d.lt", "A@B.LT", True),
        ("email", "a@b.lt, c@d.lt", "c@d.lt", True),
        ("email", "xa@b.lt", "a@b.lt", False),
        ("email", "", "a@b.lt", False),
        ("vat_payer_code", "LT100001", "100001", True),
        ("vat_payer_code", "LT 1000 01", "lt100001", True),
        ("vat_payer_code", "LT1000011", "LT100001", False),
        ("company_code", " 300 001 ", "300001", True),
        ("company_code", "3000011", "300001", False),
    ],
)
def test_client_values_are_compared_as_kept(
    column: str, found: str, wanted: str, same: bool
) -> None:
    assert same_client_value(column, found, wanted) is same


@pytest.mark.parametrize(
    "inputs, row",
    [
        ({"email": "a@b.lt"}, {"code": "K9", "email": "a@b.lt; c@d.lt"}),
        ({"vat_code": "100001"}, {"code": "K9", "vat_payer_code": "LT100001"}),
    ],
)
def test_normalised_match_is_the_client(inputs: dict[str, Any], row: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetDescriptions=items(row))
    result = run(fake, "find_or_create_client", inputs)["result"]
    assert result["client_code"] == "K9" and result["created"] is False and fake.writes() == []


@pytest.mark.parametrize(
    "inputs, row",
    [
        ({"email": "a@b.lt"}, {"code": "K9", "email": "xa@b.lt"}),
        ({"vat_code": "LT100001"}, {"code": "K9", "vat_payer_code": "LT1000011"}),
        ({"company_code": "300"}, {"code": "K9", "company_code": ""}),
    ],
)
def test_rows_without_an_exact_match_are_a_conflict(
    inputs: dict[str, Any], row: dict[str, Any]
) -> None:
    fake = FakeFinvalda(GetDescriptions=[items(), items(row)], InsertNewItem={**OK, "nResult": 0})
    response = run(fake, "find_or_create_client", {**NEW, **inputs})
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "K9" in response["error"] and "client's code" in response["error"]


def test_no_rows_for_any_value_creates_the_client() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items(),
            items(),
            items(),
            items(),
            items({"code": "NEW", "title": "New"}),
        ],
        InsertNewItem={**OK, "nResult": 0},
    )
    result = run(
        fake,
        "find_or_create_client",
        {**NEW, "company_code": "300", "vat_code": "LT1", "email": "a@b.lt"},
    )["result"]
    assert result["created"] is True and len(fake.writes()) == 1


def test_invoice_client_by_company_code_with_only_a_loose_row_is_a_conflict() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "K9", "company_code": "3001"}))
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_company_code": "300",
            "document": "S",
            "warehouse": "W",
            "lines": [{"code": "P1", "quantity": "1", "price": "1"}],
        },
    )
    assert response["error_code"] == "conflict" and fake.writes() == []


# -- S2: an access refusal is a refusal, whatever the code ---------------------------------


@pytest.mark.parametrize("mutating_step", [True, False])
def test_access_denied_comes_before_an_unreadable_code(mutating_step: bool) -> None:
    answer = {"AccessResult": "AccessDenied", "nResult": "0.5"}
    if mutating_step:
        fake = FakeFinvalda(InsertNewItem=answer)
        response = run(
            fake,
            "insert_item",
            {"item_class": "Fvs.Klientas", "fields": {"sKodas": "K1", "sPavadinimas": "X"}},
        )
    else:
        response = run(FakeFinvalda(GetSandelius=answer), "get_sandelius")
    assert response["error_code"] == "authentication_failed"
    assert response["definitely_no_external_effect"] is True


# -- S3: every step declares warnings --------------------------------------------------------


def test_every_step_declares_warnings() -> None:
    manifest = yaml.safe_load((BUNDLE / "contracts/operation_manifest.yaml").read_text("utf-8"))
    for op in manifest["operations"]:
        assert any(o["name"] == "warnings" for o in op["outputs"]), op["operation_id"]


# -- S4: flags and objects are no codes; the next key is read -------------------------------


@pytest.mark.parametrize(
    "data, code",
    [
        ({"nResult": False, "errorCode": "4"}, 4),
        ({"nResult": True}, None),
        ({"errorCode": {"x": 1}}, None),
        ({"nResult": [1], "errorCode": 0}, 0),
    ],
)
def test_flags_and_objects_are_skipped(data: dict[str, Any], code: Any) -> None:
    assert result_code(data) == code


# -- S6: long number texts ---------------------------------------------------------------------


def test_long_trailing_zeros_are_read() -> None:
    assert decimal_input("x", "1." + "0" * 61) == 1
