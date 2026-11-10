"""Everyday steps through handle_runtime, against a fake Finvalda."""

from __future__ import annotations

import base64
import json
from typing import Any

from support import OK, FakeFinvalda, Files, items, refused, run, saved

CLIENT = {
    "code": "K1",
    "title": "UAB K",
    "company_code": "300000001",
    "vat_payer_code": "LT1",
    "email": "k@k.lt",
}


def by_type(**answers: Any):
    """A GetDescriptions route answering by list type (a list is used in order)."""
    queues = {k: (list(v) if isinstance(v, list) else v) for k, v in answers.items()}

    def answer(call):
        kind = call.read_params()["type"]
        value = queues[kind]
        return value.pop(0) if isinstance(value, list) else value

    return answer


def test_test_connection() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "K1"}))
    response = run(fake, "test_connection")
    assert response["ok"] is True
    assert (
        response["result"]["clients_visible"] is True
        and response["result"]["company_id"] == "IMONE"
    )
    assert fake.calls[0].read_params()["limit"] == 1


# -- clients ---------------------------------------------------------------------


def test_client_found_by_code() -> None:
    fake = FakeFinvalda(GetDescriptions=items(CLIENT))
    response = run(fake, "find_or_create_client", {"code": "K1"})
    assert response["result"]["client_code"] == "K1" and response["result"]["created"] is False
    assert response["result"]["matched_by"] == "code"
    assert fake.writes() == []


def test_client_found_by_company_code_after_code_misses() -> None:
    # No client has code K2; K1 has the company code: another client, not this one.
    fake = FakeFinvalda(GetDescriptions=[items(), items(CLIENT)])
    response = run(fake, "find_or_create_client", {"code": "K2", "company_code": "300000001"})
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "K1" in response["error"] and "K2" in response["error"]
    assert fake.calls[1].read_params()["Clients"] == {"CompanyCode": "300000001"}


def test_client_found_by_company_code_without_a_code() -> None:
    fake = FakeFinvalda(GetDescriptions=items(CLIENT))
    response = run(fake, "find_or_create_client", {"company_code": "300000001"})
    assert response["result"]["matched_by"] == "company_code"


def test_client_with_conflicting_company_code_is_an_error() -> None:
    fake = FakeFinvalda(GetDescriptions=items(CLIENT))
    response = run(fake, "find_or_create_client", {"code": "K1", "company_code": "399999999"})
    assert response["error_code"] == "conflict" and fake.writes() == []


def test_several_clients_by_email_is_an_error() -> None:
    fake = FakeFinvalda(GetDescriptions=items(CLIENT, {**CLIENT, "code": "K9"}))
    response = run(fake, "find_or_create_client", {"email": "k@k.lt"})
    assert response["error_code"] == "conflict"


def test_client_created_when_none_matches() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), items(), items({"code": "NEW1", "title": "UAB Naujas"})],
        InsertNewItem={**OK, "nResult": 0},
    )
    response = run(
        fake,
        "find_or_create_client",
        {
            "code": "NEW1",
            "company_code": "300000002",
            "name": "UAB Naujas",
            "country_code": "lt",
            "is_person": False,
        },
    )
    assert response["ok"] is True, response
    assert response["result"]["created"] is True and response["result"]["client"]["code"] == "NEW1"
    fields = json.loads(fake.writes()[0].body["xmlstring"])["Fvs.Klientas"]
    assert fields == {
        "sFvsImportoParametras": "PARAM_APR",
        "sKodas": "NEW1",
        "sPavadinimas": "UAB Naujas",
        "sImKodas": "300000002",
        "sValstybeKodas": "LT",
    }


def test_client_inputs_are_checked_before_any_call() -> None:
    fake = FakeFinvalda()
    for inputs in ({}, {"code": "lower"}, {"email": "not-an-email"}):
        response = run(fake, "find_or_create_client", inputs)
        assert response["error_code"] == "invalid_payload", inputs
    assert fake.calls == []


def test_client_created_then_reread_fails_is_still_a_success() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), (500, b"down")], InsertNewItem={**OK, "nResult": 0}
    )
    response = run(fake, "find_or_create_client", {"code": "NEW1", "name": "UAB"})
    assert response["ok"] is True and response["result"]["created"] is True
    assert response["result"]["client"] is None
    assert response["result"]["warnings"] == [
        "client NEW1 was saved, but reading it back failed: upstream_failure"
    ]


# -- products and services -------------------------------------------------------


def test_product_found_by_barcode() -> None:
    fake = FakeFinvalda(
        GetDescriptions=by_type(
            Products=[items(), items({"product": "P7", "title": "Pieštukas"})],
            BarCodes=items({"code": "P7", "barcode": "4770001"}),
        )
    )
    response = run(fake, "find_or_create_product", {"code": "P8", "barcode": "4770001"})
    assert response["error_code"] == "conflict"
    fake = FakeFinvalda(
        GetDescriptions=by_type(
            BarCodes=items({"code": "P7", "barcode": "4770001"}),
            Products=items({"product": "P7", "title": "Pieštukas"}),
        )
    )
    response = run(fake, "find_or_create_product", {"barcode": "4770001"})
    assert (
        response["result"]["product_code"] == "P7" and response["result"]["matched_by"] == "barcode"
    )


def test_product_created_with_prices() -> None:
    fake = FakeFinvalda(GetDescriptions=items(), InsertNewItem={**OK, "nResult": 0})
    response = run(
        fake,
        "find_or_create_product",
        {"code": "P1", "name": "Prekė", "unit": "VNT", "price": "12.50"},
    )
    assert response["ok"] is True, response
    fields = json.loads(fake.writes()[0].body["xmlstring"])["Fvs.Preke"]
    assert fields["dKaina1"] == "12.50" and fields["sMatavimoVnt"] == "VNT"


def test_service_found_or_created() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "S1", "title": "Konsultacija"}))
    assert run(fake, "find_or_create_service", {"code": "S1"})["result"]["created"] is False
    fake = FakeFinvalda(GetDescriptions=items(), InsertNewItem={**OK, "nResult": 0})
    response = run(fake, "find_or_create_service", {"code": "S2", "name": "Darbas", "price": 30})
    fields = json.loads(fake.writes()[0].body["xmlstring"])["Fvs.Paslauga"]
    assert fields["dPardavimoKaina"] == "30" and response["result"]["created"] is True


# -- invoices --------------------------------------------------------------------

LINES = [
    {
        "code": "P1",
        "quantity": 3,
        "price": "12.10",
        "discount_percent": 10,
        "vat_percent": 21,
        "warehouse": "CENTR",
    },
    {"kind": "service", "code": "S1", "quantity": 1, "price": 50, "vat_percent": 21},
]
INVOICE = {"client_code": "K1", "document": "S-1001", "date": "2026-11-10", "lines": LINES}


def test_sales_invoice_amounts_and_vat() -> None:
    fake = FakeFinvalda(
        GetOperations=[
            items(),
            items({"journal": "PARD", "op_number": 61, "order_number": "S-1001", "client": "K1"}),
        ],
        InsertNewOperation=saved("PARD", 61, "", "S-1001"),
    )
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["ok"] is True, response
    body = fake.writes()[0].body
    assert body["ItemClassName"] == "PardDok" and body["sParametras"] == "PARAM_PARD"
    sale = json.loads(body["xmlstring"])["PardDok"]
    assert sale["sKlientas"] == "K1" and sale["sValiuta"] == "EUR" and sale["tData"] == "2026-11-10"
    product = sale["PardDokPrekeDetEil"][0]
    assert product == {
        "sKodas": "P1",
        "nKiekis": "3",
        "dSumaV": "36.30",
        "dSumaL": "36.30",
        "sSandelis": "CENTR",
        "nPirmasMat": 1,
        "dNlProc": "10",
        "dSumaNV": "3.63",
        "dSumaNL": "3.63",
        "dPVM_Procentas": "21",
        "dSumaPVMV": "6.86",
        "dSumaPVML": "6.86",
    }
    service = sale["PardDokPaslaugaDetEil"][0]
    assert service["dSumaV"] == "50" or service["dSumaV"] == "50.00"
    assert "sSandelis" not in service
    result = response["result"]
    assert result["total_without_vat"] == "82.67" and result["total_vat"] == "17.36"
    assert result["created"] is True and result["journal"] == "PARD" and result["op_number"] == 61
    assert fake.calls[0].op_params()["filter"] == {"OrderNumber": "S-1001"}


def test_sales_invoice_rerun_returns_the_existing_one() -> None:
    existing = {
        "journal": "PARD",
        "op_number": 61,
        "series": "",
        "order_number": "S-1001",
        "client": "K1",
        "currency": "EUR",
        "amount": 100.03,
    }
    fake = FakeFinvalda(GetOperations=items(existing))
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["ok"] is True and response["result"]["created"] is False
    assert response["result"]["op_number"] == 61 and fake.writes() == []
    assert response["result"]["amount"] == "100.03" and response["result"]["total_vat"] is None
    assert response["result"]["invoice"]["document"] == "S-1001"


def test_document_number_of_another_client_is_a_conflict() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"journal": "PARD", "op_number": 5, "order_number": "S-1001", "client": "K9"}
        )
    )
    response = run(fake, "create_sales_invoice", INVOICE)
    assert response["error_code"] == "conflict" and fake.writes() == []


def test_invoice_inputs_checked_before_any_call() -> None:
    fake = FakeFinvalda()
    cases = [
        {**INVOICE, "lines": []},
        {**INVOICE, "currency": "USD"},
        {**INVOICE, "lines": [{"code": "P1", "quantity": 1, "price": 1}]},
        {**INVOICE, "lines": [{"code": "P1", "quantity": -1, "price": 1, "warehouse": "W"}]},
        {**INVOICE, "lines": [{"code": "P1", "quantity": 1, "price": "1,5", "warehouse": "W"}]},
        {**INVOICE, "date": "10/11/2026"},
        {key: value for key, value in INVOICE.items() if key != "client_code"},
    ]
    for inputs in cases:
        response = run(fake, "create_sales_invoice", inputs)
        assert response["error_code"] == "invalid_payload", inputs
    response = run(fake, "create_sales_invoice", INVOICE, config={"operation_parameter": ""})
    assert response["error_code"] == "invalid_payload"
    assert fake.calls == []


def test_foreign_currency_needs_eur_amounts() -> None:
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved())
    lines = [{"code": "P1", "quantity": 2, "price": 10, "warehouse": "W", "amount_eur": "18.40"}]
    response = run(fake, "create_purchase_invoice", {**INVOICE, "currency": "USD", "lines": lines})
    assert response["ok"] is True, response
    line = json.loads(fake.writes()[0].body["xmlstring"])["PirkDok"]["PirkDokPrekeDetEil"][0]
    assert line["dSumaV"] == "20.00" and line["dSumaL"] == "18.40"


def test_client_looked_up_by_company_code() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items(CLIENT),
        GetOperations=[items(), items()],
        InsertNewOperation=saved(),
    )
    inputs = {k: v for k, v in INVOICE.items() if k != "client_code"}
    response = run(fake, "create_sales_invoice", {**inputs, "client_company_code": "300000001"})
    assert response["result"]["client_code"] == "K1"


def test_invoice_saved_then_attaching_fails_names_what_was_saved() -> None:
    fake = FakeFinvalda(
        GetOperations=[items(), items()],
        InsertNewOperation=saved("PARD", 61, "", "S-1001"),
        GetAttachedDocument={**OK, "enitityDocs": []},
        InsertDocument=refused(3, "no content"),
    )
    response = run(
        fake, "create_sales_invoice", {**INVOICE, "document_file": "art_1"}, files=Files()
    )
    assert response["ok"] is False
    assert response["definitely_no_external_effect"] is False
    assert response["external_effect_status"] == "timeout_unknown"
    assert "PardDok PARD #61" in response["error"]


def test_invoice_with_file_is_attached() -> None:
    fake = FakeFinvalda(
        GetOperations=[items(), items()],
        InsertNewOperation=saved("PARD", 61, "AB", "S-1001"),
        GetAttachedDocument={**OK, "enitityDocs": []},
        InsertDocument={
            "AccessResult": "Success",
            "error": "",
            "result": {"errorText": "", "errorCode": 0, "accessResult": 2},
        },
        AttachDocument={
            "AccessResult": "Success",
            "error": "",
            "result": {"errorText": "", "errorCode": 0, "accessResult": 2},
        },
    )
    response = run(
        fake, "create_sales_invoice", {**INVOICE, "document_file": "art_1"}, files=Files(b"PDF")
    )
    assert response["ok"] is True, response
    from runtime.everyday.common import own_file_name

    assert response["result"]["attachment_name"] == own_file_name("sale", "K1", "", "S-1001")
    attach = [c for c in fake.calls if c.method == "AttachDocument"][0]
    assert (
        attach.query["entityType"] == "3"
        and attach.query["id1"] == "PARD"
        and attach.query["id2"] == "61"
    )


# -- payments --------------------------------------------------------------------


def test_incoming_payment_for_an_invoice() -> None:
    fake = FakeFinvalda(
        GetOperations=[items(), items()], InsertNewOperation=saved("IPL", 7, "", "B-1")
    )
    response = run(
        fake,
        "register_payment",
        {
            "direction": "incoming",
            "client_code": "K1",
            "document": "B-1",
            "amount": "100.00",
            "invoice_document": "S-1001",
            "date": "2026-11-10",
        },
    )
    assert response["ok"] is True, response
    body = fake.writes()[0].body
    inflow = json.loads(body["xmlstring"])["IplDok"]
    assert inflow["nTipas"] == 3 and inflow["sDokumentas"] == "B-1"
    assert inflow["IplDokDetEil"] == [{"dSumaV": "100.00", "sDokumentas": "S-1001"}]
    assert fake.calls[0].op_params()["OpClass"] == "Inflows"


def test_payment_with_an_existing_document_stops() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"journal": "ISM", "op_number": 3, "order_number": "B-2", "client": "K1", "amount": 5}
        )
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "outgoing", "client_code": "K1", "document": "B-2", "amount": 5},
    )
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "nothing more is needed" in response["error"]
    assert fake.calls[0].op_params()["OpClass"] == "Disbursement"
    assert fake.calls[0].op_params()["filter"] == {"OrderNumber": "B-2", "Client": "K1"}


def test_payment_inputs_checked() -> None:
    fake = FakeFinvalda()
    for inputs in (
        {"direction": "sideways", "client_code": "K1", "document": "B", "amount": 1},
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": 0},
        {"direction": "incoming", "client_code": "K1", "amount": 1},
        {
            "direction": "incoming",
            "client_code": "K1",
            "document": "B",
            "amount": 1,
            "invoice_series": "AB",
        },
    ):
        assert run(fake, "register_payment", inputs)["error_code"] == "invalid_payload", inputs
    assert fake.calls == []


# -- files -----------------------------------------------------------------------


def test_attach_file_already_attached_is_not_stored_again() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={
            **OK,
            "enitityDocs": [{"docs": [{"name": "S-1.pdf", "data": Files().content.hex()}]}],
        }
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "61",
            "document_file": "a",
            "file_name": "S-1.pdf",
        },
    )
    assert response["result"]["stored"] is False and response["result"]["attached"] is False
    assert fake.writes() == []


def test_download_invoice_pdf() -> None:
    fake = FakeFinvalda(MakeInvoice={**OK, "fileContents": base64.b64encode(b"%PDF").decode()})
    files = Files()
    response = run(
        fake,
        "download_invoice_pdf",
        {"invoice_form": "PARD_01", "series": "AB", "document": "1"},
        files=files,
    )
    assert response["ok"] is True and files.written[0]["data"] == b"%PDF"
    assert response["result"]["file_name"] == "AB1.pdf"


def test_invoice_pdf_without_content_is_an_error() -> None:
    fake = FakeFinvalda(MakeInvoice={**OK, "fileContents": ""})
    response = run(
        fake, "download_invoice_pdf", {"invoice_form": "PARD_01", "journal": "PARD", "op_number": 1}
    )
    assert response["error_code"] == "invalid_response"


# -- reads -----------------------------------------------------------------------


def test_client_debt_with_overdue_documents() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items(
            {"code": "K1", "title": "UAB K", "debt": 150.5, "current_debt": 150.5}
        ),
        GetNeapmoketiKliDok=items(
            {
                "dokumentas": "AB1",
                "apmokejimo_data": "2026-10-01T00:00:00",
                "likusi_skola": 100.5,
                "pradine_suma": 100.5,
                "valiuta": "EUR",
            },
            {
                "dokumentas": "AB2",
                "apmokejimo_data": "2026-12-01T00:00:00",
                "likusi_skola": 50,
                "pradine_suma": 60,
                "valiuta": "EUR",
            },
        ),
    )
    response = run(fake, "client_debt", {"client_code": "K1", "as_of": "2026-11-10"})
    result = response["result"]
    assert (
        result["debt"] == "150.5"
        and result["unpaid_total"] == "150.50"
        and result["overdue_total"] == "100.50"
    )
    assert [d["overdue"] for d in result["documents"]] == [True, False]
    assert fake.calls[1].headers["language"] == "0"


def test_client_debt_of_an_unknown_client() -> None:
    fake = FakeFinvalda(GetDescriptions=items())
    assert run(fake, "client_debt", {"client_code": "K1"})["error_code"] == "not_found"


def test_stock_levels() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items(
            {
                "code": "P1",
                "title": "A",
                "warehouse": "W",
                "quantity": 4,
                "quantity_with_reserved": 3,
                "measure_unit": "VNT",
                "primary_cost": 1.25,
            }
        )
    )
    response = run(fake, "stock_levels", {"product_codes": ["P1"], "warehouse": "W"})
    assert (
        response["result"]["items"][0]["quantity"] == "4"
        and response["result"]["has_more"] is False
    )
    params = fake.calls[0].read_params()
    assert params["type"] == "CurrentStock" and params["Products"] == {
        "Codes": ["P1"],
        "Warehouse": "W",
    }


def test_product_prices_with_client_prices() -> None:
    fake = FakeFinvalda(
        GetDescriptions=by_type(
            Products=items(
                {"product": "P1", "title": "A", "sell_price1": 9.99, "sell_currency": "EUR"}
            ),
            Prices=items({"client": "K1", "code": "P1", "price": 8.5}),
        )
    )
    response = run(fake, "product_prices", {"product_codes": ["P1"], "client_code": "K1"})
    assert response["result"]["items"][0]["price1"] == "9.99"
    assert response["result"]["client_prices"][0]["price"] == "8.5"
    assert response["result"]["client_prices"][0]["client_code"] == "K1"
    assert fake.calls[1].read_params()["Prices"] == {"Type": 1, "Client": "K1", "Codes": ["P1"]}


def test_changes_since_for_operations() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"journal": "PARD", "op_number": 1, "date_edited": "2026-11-02T08:00:00"}
        )
    )
    response = run(fake, "changes_since", {"entity": "sales", "since": "2026-11-01T00:00:00"})
    assert response["result"]["count"] == 1 and response["result"]["has_more"] is False
    assert fake.calls[0].op_params()["filter"] == {"DateEditedFrom": "2026-11-01"}


def test_everyday_writes_in_test_mode_send_nothing() -> None:
    fake = FakeFinvalda()
    for operation_id, inputs in (
        ("find_or_create_client", {"code": "K1", "name": "X"}),
        ("create_sales_invoice", INVOICE),
        (
            "register_payment",
            {"direction": "incoming", "client_code": "K1", "document": "B", "amount": 1},
        ),
    ):
        response = run(fake, operation_id, inputs, test_mode=True)
        assert response["ok"] is True and response["definitely_no_external_effect"] is True, (
            operation_id
        )
    assert fake.calls == []
