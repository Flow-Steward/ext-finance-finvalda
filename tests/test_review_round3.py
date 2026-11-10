"""The third code review (N1-N11): reads through Call a method, the XML form of the
newer writes, invoice reruns without VAT rates, dates near midnight, language,
values that do not fit their type, the rest of an answer, column case, and the
described results of the older methods and list types."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest
import yaml
from support import BUNDLE, OK, FakeFinvalda, Files, items, run

from runtime.errors import ExtensionError
from runtime.inputs import date_input


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    data = yaml.safe_load((BUNDLE / "contracts/operation_manifest.yaml").read_text("utf-8"))
    return {op["operation_id"]: op for op in data["operations"]}


def _output(operation_id: str, name: str) -> dict[str, Any]:
    return next(o for o in _manifest()[operation_id]["outputs"] if o["name"] == name)["schema"]


# -- N1: Call a method reads as a read ------------------------------------------------


def test_call_method_read_without_access_result_is_a_read() -> None:
    fake = FakeFinvalda(GetSandelius={"items": [{"kodas": "W1"}]})
    response = run(fake, "call_method", {"method": "GetSandelius", "verb": "GET"})
    assert response["ok"] is True, response
    assert response["result"]["response"] == {"items": [{"kodas": "W1"}]}


def test_call_method_write_still_needs_a_confirmation() -> None:
    fake = FakeFinvalda(InsertNewItem={"AccessResult": "Success"})
    response = run(fake, "call_method", {"method": "InsertNewItem", "verb": "POST", "body": {}})
    assert response["error_code"] == "timeout_unknown"


# -- N2: the XML form of the newer writes ----------------------------------------------


def test_insert_document_xml_form_is_confirmed() -> None:
    fake = FakeFinvalda(InsertDocument={"InsertDocumentOut": {"errorCode": 0, "errorText": ""}})
    response = run(
        fake,
        "insert_document",
        {"document_file": "art_1", "file_name": "a.txt", "composition_date": "2026-11-10"},
        files=Files(b"Lorem"),
    )
    assert response["ok"] is True, response


def test_insert_document_xml_form_with_an_odd_access_result_is_unknown() -> None:
    fake = FakeFinvalda(
        InsertDocument={"AccessResult": "Odd", "InsertDocumentOut": {"errorCode": 0}}
    )
    response = run(
        fake,
        "insert_document",
        {"document_file": "art_1", "file_name": "a.txt"},
        files=Files(b"Lorem"),
    )
    assert response["error_code"] == "timeout_unknown"


# -- N3: an invoice rerun when the VAT comes from the cards -----------------------------

_LINE = {"code": "P1", "quantity": "1", "price": "100"}


def _invoice(existing: dict[str, Any], **inputs: Any) -> dict[str, Any]:
    row = {"order_number": "S-1", "client": "K1", "journal": "PARD", "op_number": 5, **existing}
    fake = FakeFinvalda(GetOperations=items(row))
    response = run(
        fake,
        "create_sales_invoice",
        {"client_code": "K1", "document": "S-1", "warehouse": "W", "lines": [_LINE], **inputs},
    )
    assert fake.writes() == []
    return response


@pytest.mark.parametrize("amount", ["100.00", "121.00", "109.00", "105"])
def test_rerun_without_vat_percent_accepts_the_cards_vat(amount: str) -> None:
    response = _invoice({"amount": amount, "currency": "EUR"})
    assert response["ok"] is True and response["result"]["created"] is False, response


@pytest.mark.parametrize("amount", ["99.99", "200.00", "250.00"])
def test_rerun_with_another_amount_is_a_conflict_without_new_number_advice(amount: str) -> None:
    response = _invoice({"amount": amount, "currency": "EUR"})
    assert response["error_code"] == "conflict"
    assert "number of its own" not in response["error"]
    assert "nothing more needs to be done" in response["error"]


def test_rerun_with_vat_percent_is_exact() -> None:
    line = {**_LINE, "vat_percent": 21}
    assert _invoice({"amount": "121.00", "currency": "EUR"}, lines=[line])["ok"] is True
    assert _invoice({"amount": "109.00", "currency": "EUR"}, lines=[line])["error_code"] == (
        "conflict"
    )


def test_rerun_of_a_foreign_currency_invoice_may_show_eur() -> None:
    line = {**_LINE, "amount_eur": "92.00", "vat_percent": 21}
    for amount in ("121.00", "111.32", "92.00"):
        response = _invoice({"amount": amount, "currency": "USD"}, currency="USD", lines=[line])
        assert response["ok"] is True, (amount, response)


def test_rerun_reads_the_initial_debt_too() -> None:
    response = _invoice({"amount": None, "initial_debt": "121.00", "currency": "EUR"})
    assert response["ok"] is True


# -- N4: a date from a time with an offset is the Lithuanian date -----------------------


@pytest.mark.parametrize(
    "value, day",
    [
        ("2024-03-31T22:30:00Z", "2024-04-01"),
        ("2024-01-15T22:30:00Z", "2024-01-16"),
        ("2024-01-15T21:59:59Z", "2024-01-15"),
        ("2024-01-15T23:30:00+02:00", "2024-01-15"),
        ("2024-03-31T23:30:00", "2024-03-31"),
        ("2024-03-31", "2024-03-31"),
    ],
)
def test_date_input_near_midnight(value: str, day: str) -> None:
    assert date_input("date", value) == day


def test_date_input_refuses_an_impossible_time() -> None:
    with pytest.raises(ExtensionError):
        date_input("date", "2024-02-30T10:00:00Z")


# -- N5: typed older reads ask for Lithuanian names -------------------------------------


def test_typed_read_asks_for_lithuanian_names_whatever_the_language() -> None:
    fake = FakeFinvalda(GetKlientusSet=items({"kodas": "K1", "skola": 1.5}))
    response = run(fake, "get_klientus_set", config={"language": "en"})
    assert fake.calls[0].headers["language"] == "0"
    assert response["result"]["records"][0]["skola"] == "1.5"
    description = _output("get_klientus_set", "records")["description"]
    assert "Lithuanian column names whatever the connection's language" in description


def test_untyped_read_keeps_the_connections_language() -> None:
    fake = FakeFinvalda(GetUzsakytasPrekes=items({"a": 1}))
    run(
        fake,
        "call_method",
        {"method": "GetUzsakytasPrekes", "verb": "GET"},
        config={"language": "en"},
    )
    assert fake.calls[0].headers["language"] == "1"


# -- N6: a value that does not fit its type is kept -------------------------------------


def test_unfit_values_are_returned_as_sent_with_a_warning() -> None:
    row = {"kodas": "K1", "atsiskaityti_per": 2.5, "kurimo_data": "0000-00-00", "skola": "n/a"}
    result = run(FakeFinvalda(GetKlientusSet=items(row)), "get_klientus_set")["result"]
    record = result["records"][0]
    assert record["atsiskaityti_per"] == "2.5"
    assert record["kurimo_data"] == "0000-00-00"
    assert record["skola"] == "n/a"
    assert len(result["warnings"]) == 3 and "not a integer" in " ".join(result["warnings"])


def test_everyday_record_keeps_an_unfit_value() -> None:
    row = {"code": "K1", "title": "K", "debt": "n/a", "date_edited": "0000-00-00"}
    result = run(FakeFinvalda(GetDescriptions=items(row)), "find_or_create_client", {"code": "K1"})[
        "result"
    ]
    assert result["client"]["debt"] == "n/a" and result["client"]["edited_at"] == "0000-00-00"
    assert len(result["warnings"]) == 2


def test_schemas_allow_the_text_of_an_unfit_value() -> None:
    debt = _output("find_or_create_client", "client")["properties"]["debt"]
    typed, as_sent = debt["anyOf"]
    assert typed["pattern"] and as_sent == {
        "type": "string",
        "description": "A value that does not fit the type, as Finvalda sent it (see warnings).",
    }


# -- N7: only documented date columns are rewritten -------------------------------------


def test_text_that_looks_like_a_date_is_left_alone() -> None:
    row = {"kodas": "2024.01.15", "pastabos": "2024.01.15", "undocumented": "2024.01.15"}
    record = run(FakeFinvalda(GetKlientusSet=items(row)), "get_klientus_set")["result"]["records"][
        0
    ]
    assert record == row


def test_list_rows_are_returned_as_sent() -> None:
    row = {"code": "2024.01.15", "date_edited": "2024.01.15 10:00"}
    fake = FakeFinvalda(GetDescriptions=items(row))
    assert run(fake, "descriptions_clients")["result"]["records"] == [row]


# -- N8: the rest of an answer is never dropped -----------------------------------------


def test_typed_read_returns_the_rest_of_the_answer() -> None:
    fake = FakeFinvalda(GetKlientusSet={**items({"kodas": "K1"}), "total": 1, "note": "x"})
    result = run(fake, "get_klientus_set")["result"]
    assert result["response"] == {"total": 1, "note": "x"}
    assert (
        run(FakeFinvalda(GetKlientusSet=items()), "get_klientus_set")["result"]["response"] is None
    )


def test_recommended_price_returns_the_rest() -> None:
    fake = FakeFinvalda(
        GetRecommendedPrice={
            **OK,
            "result": {
                "errorCode": 0,
                "errorText": "",
                "price": 1,
                "vat": 0,
                "discount": 0,
                "rule": "R",
            },
        }
    )
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
    assert result["response"] == {"rule": "R"} and result["price"] == "1"


def test_related_invoices_and_permissions_return_the_rest() -> None:
    fake = FakeFinvalda(GetInvoicesRelatedToCustomer={**items({"client": "K1"}), "sum": 5})
    assert run(fake, "invoices_related_to_customer", {"client_code": "K1"})["result"][
        "response"
    ] == {"sum": 5}
    rows = [{"perrmisionClass": 6, "perrmisionCode": "A"}]
    fake = FakeFinvalda(
        GetUserPermissions={**OK, "result": {"errorCode": 0, "perrmissions": rows, "extra": 1}}
    )
    result = run(fake, "user_permissions")["result"]
    assert result["permissions"] == rows and result["response"] == {"result": {"extra": 1}}


# -- N9: column names in any case ---------------------------------------------------------


def test_columns_match_without_case_and_are_sent_as_documented() -> None:
    fake = FakeFinvalda(GetDescriptions=items())
    assert run(fake, "descriptions_clients", {"columns": ["Code", "TITLE"]})["ok"] is True
    assert fake.calls[0].read_params()["columns"] == ["code", "title"]


# -- N11: described results -------------------------------------------------------------


@pytest.mark.parametrize(
    "operation_id, columns",
    [
        ("descriptions_document_series", {"code", "title", "number"}),
        ("descriptions_count_sales", {"quantity", "sales_sum", "settled_sum"}),
        ("descriptions_bar_codes", {"code", "barcode"}),
        ("descriptions_currency_rates", {"code", "date", "rate"}),
        ("descriptions_vehicles", {"code", "info1"}),
    ],
)
def test_list_types_describe_what_they_return(operation_id: str, columns: set[str]) -> None:
    assert set(_output(operation_id, "records")["items"]["properties"]) == columns


@pytest.mark.parametrize(
    "operation_id",
    ["descriptions_accounts", "descriptions_report_list", "descriptions_operation_statuses"],
)
def test_undocumented_list_types_say_so(operation_id: str) -> None:
    schema = _output(operation_id, "records")
    assert not schema["items"]["properties"]
    assert "Columns not documented by Finvalda" in schema["description"]


def test_class_records_are_described_by_their_fields() -> None:
    for operation_id in (
        "get_klientas",
        "get_klientus",
        "get_objektas1",
        "get_preke",
        "get_prekes",
    ):
        properties = _output(operation_id, "records")["items"]["properties"]
        assert "sKodas" in properties and "FvsDatabase" not in properties, operation_id
    klientas = _output("get_klientas", "records")["items"]["properties"]
    assert klientas["nKreditas"]["anyOf"][0]["type"] == ["integer", "null"]
    assert klientas["tKurimoData"]["anyOf"][0]["pattern"]


def test_class_record_answer_becomes_a_typed_record() -> None:
    record = {"sKodas": "K1", "nKreditas": "5", "tKurimoData": "2010-01-05 00:00:00"}
    fake = FakeFinvalda(GetKlientas={**OK, "Fvs.Klientas": record})
    result = run(fake, "get_klientas", {"client_code": "K1"})["result"]
    assert result["records"] == [
        {"sKodas": "K1", "nKreditas": 5, "tKurimoData": "2010-01-05T00:00:00+02:00"}
    ]
    assert result["response"] is None
    fake = FakeFinvalda(GetKlientus={**OK, "Klientai": {"Fvs.Klientas": [record, record]}})
    assert run(fake, "get_klientus")["result"]["count"] == 2


def test_class_record_as_xml_text_stays_in_response() -> None:
    fake = FakeFinvalda(
        GetKlientas={**OK, "Xml": "<Fvs.Klientas><sKodas>K1</sKodas></Fvs.Klientas>"}
    )
    result = run(fake, "get_klientas", {"client_code": "K1"})["result"]
    assert result["records"] == [] and result["response"]["Xml"].startswith("<Fvs.Klientas>")


def test_plain_lists_have_item_schemas() -> None:
    assert _output("auto_reports_list", "names")["items"] == {"type": "string"}
    files = _output("attached_documents", "files")["items"]
    assert set(files["properties"]) == {"name", "size_bytes"}


def test_changes_since_records_are_one_of_the_record_forms() -> None:
    schema = _output("changes_since", "records")
    assert len(schema["items"]["anyOf"]) == 4
