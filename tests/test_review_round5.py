"""The fifth review (G1-G7): changes by date in plain pages, the XML result element
of every newer method, whole cents written with more zeros, product codes in
either column, the result key in any case, the password checked in the parsed
answer, and the hint for a file stored by an earlier run."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any

import pytest
import yaml
from support import BUNDLE, OK, FakeFinvalda, Files, items, run, saved

from runtime.inputs import decimal_input
from runtime.rows import rest_of


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    data = yaml.safe_load((BUNDLE / "contracts/operation_manifest.yaml").read_text("utf-8"))
    return {op["operation_id"]: op for op in data["operations"]}


# -- G1: changes by date, in plain pages -------------------------------------------------


def _changes(rows: list[dict[str, Any]], **inputs: Any) -> tuple[dict[str, Any], FakeFinvalda]:
    fake = FakeFinvalda(GetDescriptions=lambda call: items(*rows))
    response = run(fake, "changes_since", {"entity": "clients", **inputs})
    assert response["ok"] is True, response
    return response["result"], fake


def test_changes_filter_by_date_only_and_keep_every_row() -> None:
    rows = [
        {"code": "B", "date_edited": "2026-11-10"},
        {"code": "A", "date_edited": "2026-11-10T08:00:00"},
        {"code": "C", "date_edited": "no time"},
    ]
    result, fake = _changes(rows, since="2026-11-10")
    assert fake.calls[0].read_params()["Clients"] == {"EditedFrom": "2026-11-10"}
    assert [r["code"] for r in result["records"]] == ["A", "B", "C"]
    assert result["total"] == 3 and result["has_more"] is False and result["next_page"] is None
    assert result["latest_edited"] == "2026-11-10T08:00:00+02:00"


def test_changes_since_a_time_is_its_lithuanian_date() -> None:
    _, fake = _changes([], since="2026-11-09T23:30:00Z")
    assert fake.calls[0].read_params()["Clients"] == {"EditedFrom": "2026-11-10"}


def test_changes_pages_cover_every_row_once() -> None:
    rows = [{"code": f"K{n:03d}", "date_edited": "2026-11-10"} for n in range(450)]
    seen: list[str] = []
    page: int | None = 1
    while page:
        result, _ = _changes(rows, since="2026-11-10", limit=200, page=page)
        seen += [r["code"] for r in result["records"]]
        page = result["next_page"]
    assert seen == sorted(r["code"] for r in rows)


def test_operation_changes_are_ordered_by_journal_and_number() -> None:
    rows = [
        {"journal": "PARD", "op_number": 10, "date_edited": "2026-11-10"},
        {"journal": "PARD", "op_number": 2, "date_edited": "2026-11-10"},
    ]
    fake = FakeFinvalda(GetOperations=items(*rows))
    result = run(fake, "changes_since", {"entity": "sales", "since": "2026-11-10"})["result"]
    assert [r["op_number"] for r in result["records"]] == [2, 10]
    assert fake.calls[0].op_params()["filter"] == {"DateEditedFrom": "2026-11-10"}


def test_changes_contract_has_no_cursor() -> None:
    op = _manifest()["changes_since"]
    inputs = {i["name"] for i in op["inputs"]}
    outputs = {o["name"] for o in op["outputs"]}
    assert {"page", "limit", "since"} <= inputs and "skip_group" not in inputs
    assert {"next_page", "has_more", "total", "latest_edited"} <= outputs
    assert not {"next_since", "undated", "undated_count"} & outputs
    assert "skip them by code; nothing is lost" in op["description"]


# -- G2: the XML result element of the newer methods --------------------------------------


def test_recommended_price_xml_answer() -> None:
    xml = (
        b"<GetRecommendedPriceOut><errorCode>0</errorCode><errorText/>"
        b"<price>10.50</price><vat>2.21</vat><discount>0</discount></GetRecommendedPriceOut>"
    )
    fake = FakeFinvalda(GetRecommendedPrice=(200, xml))
    result = run(
        fake,
        "recommended_price",
        {
            "invoice_type": 0,
            "invoice_date": "2026-11-10",
            "item_type": 1,
            "item_code": "X",
            "quantity": "1",
        },
    )["result"]
    assert result["price"] == "10.50" and result["vat"] == "2.21" and result["response"] is None


def test_recommended_price_xml_refusal() -> None:
    xml = b"<GetRecommendedPriceOut><errorCode>5</errorCode><errorText>no</errorText></GetRecommendedPriceOut>"
    response = run(
        FakeFinvalda(GetRecommendedPrice=(200, xml)),
        "recommended_price",
        {
            "invoice_type": 0,
            "invoice_date": "2026-11-10",
            "item_type": 1,
            "item_code": "X",
            "quantity": "1",
        },
    )
    assert response["error_code"] == "upstream_validation_failed"


@pytest.mark.parametrize("count", [1, 2])
def test_user_permissions_xml_answer(count: int) -> None:
    entry = b"<perrmissions><perrmisionClass>6</perrmisionClass><perrmisionCode>A</perrmisionCode></perrmissions>"
    xml = (
        b"<GetUserPermissionsOut><errorCode>0</errorCode>"
        + entry * count
        + b"</GetUserPermissionsOut>"
    )
    result = run(FakeFinvalda(GetUserPermissions=(200, xml)), "user_permissions")["result"]
    assert result["count"] == count
    assert result["permissions"][0]["perrmisionCode"] == "A"


# -- G3: whole cents written with more zeros ----------------------------------------------


@pytest.mark.parametrize("value", ["10.000", Decimal("10.0000"), "10.10", "10"])
def test_trailing_zeros_are_whole_cents(value: Any) -> None:
    number = decimal_input("amount", value, places=2)
    assert number == Decimal(str(value)) and -number.as_tuple().exponent <= 2  # type: ignore[operator]


@pytest.mark.parametrize("value", ["10.005", "10.0001"])
def test_a_fraction_of_a_cent_is_refused(value: str) -> None:
    from runtime.errors import ExtensionError

    with pytest.raises(ExtensionError):
        decimal_input("amount", value, places=2)


def test_payment_of_ten_with_three_zeros() -> None:
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved())
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "10.000"},
    )
    assert response["ok"] is True, response
    assert fake.writes()[0].inner()["IplDok"]["IplDokDetEil"][0]["dSumaV"] == "10.00"


# -- G4: the product code in either column --------------------------------------------------


def test_prices_take_the_code_column_too() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "P1", "sell_price1": 1}))
    result = run(fake, "product_prices", {"product_codes": ["P1"]})["result"]
    assert [i["code"] for i in result["items"]] == ["P1"]


# -- G5: the result key in any case ----------------------------------------------------------


def test_rest_of_drops_status_of_result_in_any_case() -> None:
    data = {"AccessResult": "Success", "Result": {"ErrorCode": 0, "errortext": ""}}
    assert rest_of(data, []) is None
    data = {"RESULT": {"errorCode": 0, "note": "x"}}
    assert rest_of(data, []) == {"RESULT": {"note": "x"}}


# -- G6: the password is checked in the parsed answer ---------------------------------------


@pytest.mark.parametrize(
    "secret, body",
    [
        ("a&b>c", b"<root><note>x a&amp;b>c y</note></root>"),
        ("a&b>c", b"<root><note>x a&amp;b&gt;c y</note></root>"),
        ("Pa55", b'{"AccessResult":"Success","note":"x\\u0050a55 y"}'),
        ("Pa55", b'{"AccessResult":"Success","Pa55":"key"}'),
    ],
)
def test_password_is_removed_however_it_is_written(secret: str, body: bytes) -> None:
    from test_transport import transport

    fake = FakeFinvalda(GetSandelius=(200, body))
    reply = transport(fake, _password=secret).request("GetSandelius", verb="GET", mutating=False)
    assert secret not in str(reply.data) and "[redacted]" in str(reply.data)


# -- G7: the hint for a file stored by an earlier run ---------------------------------------

HINT = "If an earlier run already stored the file in the register, attach it with Advanced · Attach a document."


@pytest.mark.parametrize(
    "operation_id", ["create_sales_invoice", "create_purchase_invoice", "attach_file"]
)
def test_steps_say_how_to_attach_a_file_stored_earlier(operation_id: str) -> None:
    assert HINT in _manifest()[operation_id]["description"]


def test_refused_store_names_the_way_to_attach() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument={**OK, "result": {"errorCode": 3, "errorText": "no content"}},
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "file_name": "a.pdf",
            "document_file": "art_1",
        },
        files=Files(),
    )
    assert "attach it with Advanced · Attach a document" in response["error"]


# -- P1: a lookup answer of unknown form never leads to a new record ------------------------


@pytest.mark.parametrize("row", [None, {}, {"title": "Existing client"}])
def test_client_lookup_with_a_row_of_unknown_form_writes_nothing(row: Any) -> None:
    fake = FakeFinvalda(GetDescriptions={**OK, "items": [row]}, InsertNewItem={**OK, "nResult": 0})
    response = run(fake, "find_or_create_client", {"code": "NEW", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


@pytest.mark.parametrize(
    "operation_id, row",
    [
        ("find_or_create_product", {"title": "Existing product"}),
        ("find_or_create_product", None),
        ("find_or_create_service", {"title": "Existing service"}),
        ("find_or_create_service", {}),
    ],
)
def test_product_and_service_lookups_check_their_rows(operation_id: str, row: Any) -> None:
    fake = FakeFinvalda(GetDescriptions={**OK, "items": [row]}, InsertNewItem={**OK, "nResult": 0})
    response = run(fake, operation_id, {"code": "NEW", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


def test_operation_lookup_with_a_row_without_its_journal_writes_nothing() -> None:
    fake = FakeFinvalda(GetOperations=items({"order_number": "S-1", "client": "K1"}))
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S-1",
            "warehouse": "W",
            "lines": [{"code": "P1", "quantity": "1", "price": "1"}],
        },
    )
    assert response["error_code"] == "invalid_response" and fake.writes() == []


# -- P2: an attached file of the same name is the same file only with the same content ------

_ATTACH = {
    "entity_type": 3,
    "entity_id1": "PARD",
    "entity_id2": "1",
    "file_name": "a.pdf",
    "document_file": "art_1",
}


def _attached(data: str) -> dict[str, Any]:
    return {**OK, "result": {"errorCode": 0, "enitityDocs": [{"name": "a.pdf", "data": data}]}}


def test_same_name_and_same_bytes_is_already_attached() -> None:
    fake = FakeFinvalda(GetAttachedDocument=_attached(b"%PDF".hex().upper()))
    result = run(fake, "attach_file", _ATTACH, files=Files(b"%PDF"))["result"]
    assert (result["file_name"], result["stored"], result["attached"]) == ("a.pdf", False, False)
    assert fake.writes() == []


def test_same_name_and_other_bytes_is_a_conflict() -> None:
    fake = FakeFinvalda(GetAttachedDocument=_attached(b"%PDF old".hex()))
    response = run(fake, "attach_file", _ATTACH, files=Files(b"%PDF new"))
    assert response["error_code"] == "conflict" and fake.writes() == []


def test_attached_file_with_broken_hex_is_invalid() -> None:
    fake = FakeFinvalda(GetAttachedDocument=_attached("not hex"))
    response = run(fake, "attach_file", _ATTACH, files=Files(b"%PDF"))
    assert response["error_code"] == "invalid_response" and fake.writes() == []
