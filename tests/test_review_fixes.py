"""Regression tests for the findings of the 0.1.0 review (924ad47)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from support import OK, FakeFinvalda, Files, items, run, saved

from runtime import errors
from runtime.connection import base_url_of, connection_from_payload
from runtime.errors import ExtensionError
from runtime.transport import DeadlineReached, dumps_json, parse_body, read_bounded

NESTED_RESULT: dict[str, Any] = {"errorText": "", "errorCode": 0, "accessResult": 2}
NESTED_OK: dict[str, Any] = {"AccessResult": "Success", "error": "", "result": NESTED_RESULT}


def nested(code: int, text: str = "") -> dict[str, Any]:
    """The answer form of the newer methods (manual 3.79-3.84)."""
    return {
        "AccessResult": "Success",
        "error": "",
        "result": {"errorText": text, "errorCode": code, "accessResult": 2},
    }


# -- B1: the newer methods' own result object -------------------------------------


def test_attach_document_nested_error_is_an_error() -> None:
    fake = FakeFinvalda(AttachDocument=nested(2, "nežinomas Finvaldos darbuotojas"))
    response = run(
        fake,
        "attach_document",
        {"entity_type": 3, "entity_id1": "PARD", "entity_id2": "42", "file_name": "a.pdf"},
    )
    assert response["ok"] is False
    assert response["error_code"] == "upstream_validation_failed"
    assert response["errors"][0]["finvalda_code"] == 2
    assert "nežinomas" in response["error"]
    assert response["definitely_no_external_effect"] is True


def test_attach_document_nested_already_attached() -> None:
    fake = FakeFinvalda(AttachDocument=nested(1, "already"))
    response = run(
        fake, "attach_document", {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"}
    )
    assert response["ok"] is True and response["result"]["already_attached"] is True


def test_xml_form_of_the_nested_result() -> None:
    data = parse_body(
        b"<root><AccessResult>Success</AccessResult><error/><AttachDocumentOut><errorText>x</errorText><errorCode>2</errorCode><accessResult>2</accessResult></AttachDocumentOut></root>"
    )
    from runtime.transport import nested_result

    assert nested_result(data) == (2, "x")


def test_insert_document_nested_error_is_not_reported_as_stored() -> None:
    fake = FakeFinvalda(InsertDocument=nested(3, "no content"))
    response = run(
        fake, "insert_document", {"document_file": "a", "file_name": "a.pdf"}, files=Files(b"x")
    )
    assert response["ok"] is False and response["errors"][0]["finvalda_code"] == 3


def test_recommended_price_nested_error() -> None:
    fake = FakeFinvalda(GetRecommendedPrice=nested(1, "nežinomas aprašymas"))
    response = run(
        fake,
        "recommended_price",
        {
            "invoice_type": 0,
            "invoice_date": "2026-11-10",
            "item_type": 1,
            "item_code": "X",
            "quantity": "1.5",
        },
    )
    assert response["error_code"] == "upstream_validation_failed"


def test_recommended_price_sends_the_quantity_exactly() -> None:
    fake = FakeFinvalda(
        GetRecommendedPrice={
            **NESTED_OK,
            "result": {**NESTED_RESULT, "price": 10.1, "vat": 2.12, "discount": 0},
        }
    )
    response = run(
        fake,
        "recommended_price",
        {
            "invoice_type": 0,
            "invoice_date": "2026-11-10",
            "item_type": 1,
            "item_code": "X",
            "quantity": "1.10",
        },
    )
    assert response["result"]["price"] == "10.1" and response["result"]["vat"] == "2.12"
    assert b'"itemAmount":1.10' in fake.calls[0].raw_body


def test_finvalda_employee_is_never_the_web_service_user() -> None:
    fake = FakeFinvalda()
    for operation_id, inputs in (
        ("attach_document", {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"}),
        ("insert_document", {"document_file": "a", "file_name": "a.pdf"}),
        ("user_permissions", {}),
    ):
        response = run(fake, operation_id, inputs, config={"fin_user": ""})
        assert response["error_code"] == "invalid_payload" and "fin_user" in response["error"], (
            operation_id
        )
    assert fake.calls == []
    fake = FakeFinvalda(GetRecommendedPrice=NESTED_OK)
    run(
        fake,
        "recommended_price",
        {
            "invoice_type": 0,
            "invoice_date": "2026-11-10",
            "item_type": 1,
            "item_code": "X",
            "quantity": 1,
        },
        config={"fin_user": ""},
    )
    assert fake.calls[0].body["inParams"]["finUser"] == ""


def test_invoice_with_a_file_needs_the_employee_before_anything_is_written() -> None:
    fake = FakeFinvalda(GetOperations=items())
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "lines": [{"code": "P1", "quantity": 1, "price": 1, "warehouse": "W"}],
        "document_file": "a",
    }
    response = run(fake, "create_sales_invoice", inputs, config={"fin_user": ""})
    assert response["error_code"] == "invalid_payload" and fake.writes() == []


# -- B2: service quantities -------------------------------------------------------


def test_service_quantity_is_sent_times_100() -> None:
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved())
    lines = [
        {"kind": "service", "code": "S1", "quantity": "2", "price": "10"},
        {"kind": "service", "code": "S2", "quantity": "0.5", "price": "10"},
    ]
    response = run(
        fake, "create_sales_invoice", {"client_code": "K1", "document": "S-1", "lines": lines}
    )
    assert response["ok"] is True, response
    services = fake.writes()[0].inner()["PardDok"]["PardDokPaslaugaDetEil"]
    assert [s["nKiekis"] for s in services] == ["200", "50"]
    assert [s["dSumaV"] for s in services] == ["20.00", "5.00"]


def test_service_quantity_with_more_than_two_decimals_is_refused() -> None:
    fake = FakeFinvalda()
    lines = [{"kind": "service", "code": "S1", "quantity": "0.125", "price": "10"}]
    assert (
        run(fake, "create_sales_invoice", {"client_code": "K1", "document": "S-1", "lines": lines})[
            "error_code"
        ]
        == "invalid_payload"
    )
    assert fake.calls == []


# -- B3: purchase numbers are the supplier's ---------------------------------------


def test_purchase_number_of_another_supplier_is_not_a_conflict() -> None:
    def operations(call):
        flt = call.op_params()["filter"]
        rows = [{"order_number": "123", "client": "SUPA", "journal": "PIRK", "op_number": 1}]
        return items(*[r for r in rows if not flt.get("Client") or r["client"] == flt["Client"]])

    fake = FakeFinvalda(GetOperations=operations, InsertNewOperation=saved("PIRK", 2))
    inputs = {
        "client_code": "SUPB",
        "document": "123",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": "1", "price": "5"}],
    }
    response = run(fake, "create_purchase_invoice", inputs)
    assert response["ok"] is True and response["result"]["created"] is True, response
    assert fake.calls[0].op_params()["filter"] == {"OrderNumber": "123", "Client": "SUPB"}


def test_sales_number_of_another_client_is_still_a_conflict() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"order_number": "S-1", "client": "K9", "journal": "PARD", "op_number": 1}
        )
    )
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
    }
    assert run(fake, "create_sales_invoice", inputs)["error_code"] == "conflict"


# -- B4: several payments on one bank statement ------------------------------------

PAYMENT = {"direction": "incoming", "client_code": "K1", "document": "BANK-55", "amount": "100.00"}
STATEMENT_ROW = {
    "order_number": "BANK-55",
    "client": "K1",
    "journal": "IPL",
    "op_number": 5,
    "amount": 100,
}


@pytest.mark.parametrize(
    "inputs",
    [
        PAYMENT,
        {**PAYMENT, "amount": "7.00"},
        {**PAYMENT, "invoice_document": "S-1"},
        {**PAYMENT, "currency": "USD"},
    ],
)
def test_any_payment_with_the_document_stops_without_a_write(inputs: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetOperations=items(STATEMENT_ROW))
    response = run(fake, "register_payment", inputs)
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "document number of its own" in response["error"]


def test_a_new_document_number_is_registered() -> None:
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved("IPL", 6))
    response = run(fake, "register_payment", {**PAYMENT, "invoice_document": "S-1"})
    assert response["ok"] is True and response["result"]["op_number"] == 6, response


# -- M5: barcodes -----------------------------------------------------------------


def test_barcode_rows_without_a_barcode_column_do_not_create_a_product() -> None:
    fake = FakeFinvalda(GetDescriptions=[items(), items({"title": "Pieštukas"})])
    response = run(
        fake, "find_or_create_product", {"code": "P1", "barcode": "4770001", "name": "X"}
    )
    assert response["error_code"] == "conflict" and fake.writes() == []


def test_barcode_row_of_another_barcode_is_ignored() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), items({"code": "P7", "barcode": "4770002"})],
        InsertNewItem={**OK, "nResult": 0},
    )
    fake.routes["GetDescriptions"].append(items({"product": "P1"}))
    response = run(
        fake, "find_or_create_product", {"code": "P1", "barcode": "4770001", "name": "X"}
    )
    assert response["result"]["created"] is True, response


def test_a_lookup_that_matches_too_many_is_an_error() -> None:
    full = items(*({"code": f"K{i}", "email": "k@k.lt"} for i in range(100)))
    fake = FakeFinvalda(GetDescriptions=[full] * 6)
    response = run(fake, "find_or_create_client", {"email": "k@k.lt"})
    assert response["error_code"] == "conflict"


# -- M6: a trickling answer cannot outlive the step --------------------------------


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _Trickle:
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self._sock = _Socket()

    def read(self, size: int) -> bytes:
        self.clock[0] += 299
        return b"x"


def test_trickling_answer_is_cut_at_the_deadline() -> None:
    now = [0.0]
    response = _Trickle(now)
    with pytest.raises(DeadlineReached):
        read_bounded(response, 10_000, expires=585.0, clock=lambda: now[0], per_read=300.0)
    assert response._sock.timeouts[0] == 300.0 and response._sock.timeouts[-1] < 300.0
    now[0] = 0.0
    with pytest.raises(ExtensionError) as exc:
        read_bounded(_Trickle(now), 10_000, mutating=True, expires=585.0, clock=lambda: now[0])
    assert exc.value.code == errors.TIMEOUT_UNKNOWN


# -- M7: precision and scale of the manual's numbers --------------------------------


def test_numbers_beyond_the_manuals_precision_are_refused() -> None:
    fake = FakeFinvalda()
    base = {"sKlientas": "K1", "sDokumentas": "S-1", "sValiuta": "EUR", "tData": "2026-11-10"}
    line = {"sKodas": "P1", "sSandelis": "W", "dSumaV": "10.123", "dSumaL": "10", "nKiekis": 1}
    for bad in (
        line,
        {**line, "dSumaV": "10", "dPVM_Procentas": 100},
        {**line, "dSumaV": "1" * 13},
    ):
        response = run(
            fake,
            "insert_operation",
            {"op_class": "PardDok", "operation": {**base, "PardDokPrekeDetEil": [bad]}},
        )
        assert response["error_code"] == "invalid_payload", bad
    assert fake.calls == []
    lines = [{"code": "P1", "quantity": 1, "price": 1, "warehouse": "W", "vat_percent": 100}]
    assert (
        run(fake, "create_sales_invoice", {"client_code": "K1", "document": "S", "lines": lines})[
            "error_code"
        ]
        == "invalid_payload"
    )


# -- M8: exact decimals ------------------------------------------------------------


def test_numbers_are_read_and_written_as_decimals() -> None:
    data = parse_body(b'{"AccessResult":"Success","amount":0.1}')
    assert data["amount"] == Decimal("0.1")
    assert dumps_json({"a": Decimal("1.10"), "b": "@x@"}) == '{"a":1.10,"b":"@x@"}'
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "P1", "quantity": 0.1, "primary_cost": 12345678.905})
    )
    result = run(fake, "stock_levels", {})["result"]
    assert result["items"][0]["quantity"] == "0.1" and result["items"][0]["cost"] == "12345678.905"


# -- smaller findings --------------------------------------------------------------


def test_failure_without_a_result_code_on_a_write_is_unknown() -> None:
    fake = FakeFinvalda(ChangeJournal={"AccessResult": "Fail", "error": "klaida"})
    response = run(fake, "change_journal", {"journal": "A", "op_number": 1, "new_journal": "B"})
    assert (
        response["error_code"] == "timeout_unknown"
        and response["definitely_no_external_effect"] is False
    )


@pytest.mark.parametrize("due", ["2026.10.01", "/Date(1759276800000+0300)/", "2026-10-01T00:00:00"])
def test_debt_reads_finvaldas_date_forms(due: str) -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K", "debt": 5}),
        GetNeapmoketiKliDok=items({"dokumentas": "A", "apmokejimo_data": due, "likusi_skola": 5}),
    )
    result = run(fake, "client_debt", {"client_code": "K1", "as_of": "2026-11-10"})["result"]
    assert result["documents"][0]["overdue"] is True and result["overdue_total"] == "5.00"


def test_unreadable_due_date_is_not_counted_as_overdue() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K", "debt": 5}),
        GetNeapmoketiKliDok=items(
            {"dokumentas": "A", "apmokejimo_data": "soon", "likusi_skola": 5}
        ),
    )
    result = run(fake, "client_debt", {"client_code": "K1"})["result"]
    assert result["documents"][0]["overdue"] is None and result["overdue_total"] == "0.00"


def test_large_file_is_refused_before_the_invoice_is_written() -> None:
    fake = FakeFinvalda(GetOperations=items())
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "lines": [{"code": "P1", "quantity": 1, "price": 1, "warehouse": "W"}],
        "document_file": "a",
    }
    response = run(fake, "create_sales_invoice", inputs, files=Files(b"x" * (8 * 1024 * 1024 + 1)))
    assert response["error_code"] == "invalid_payload" and fake.writes() == []


def test_attachment_names_keep_sales_and_purchases_apart() -> None:
    answers = dict(
        GetOperations=[items(), items()],
        InsertNewOperation=saved("PIRK", 9, "", "AB1"),
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=NESTED_OK,
        AttachDocument=NESTED_OK,
    )
    fake = FakeFinvalda(**answers)
    inputs = {
        "client_code": "SUP",
        "document": "AB1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
        "document_file": "a",
    }
    response = run(fake, "create_purchase_invoice", inputs, files=Files(b"%PDF"))
    from runtime.everyday.common import own_file_name

    name = response["result"]["attachment_name"]
    assert name == own_file_name("purchase", "SUP", "", "AB1") and name.startswith(
        "purchase-SUP-AB1-"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://127.1/FvsServicePure.svc",
        "https://0x7f.1/FvsServicePure.svc",
        "https://10.0.0.1.5/FvsServicePure.svc",
    ],
)
def test_numeric_host_forms_are_refused(url: str) -> None:
    with pytest.raises(ExtensionError):
        base_url_of(url)


def test_short_password_is_refused_and_xml_escaped_echo_is_redacted() -> None:
    payload = {
        "action": {
            "target": {
                "connection": {
                    "connection_id": "c1",
                    "config": {
                        "base_url": "https://fvs.example.lt/FvsServicePure.svc",
                        "user_name": "W",
                    },
                    "secrets": {"password": "abc"},
                }
            }
        }
    }
    with pytest.raises(ExtensionError):
        connection_from_payload(payload, connection_ref="c1")
    secret = "pa<ss&wo'rd"
    fake = FakeFinvalda(
        GetSandelius=b"<root><AccessResult>Success</AccessResult><echo>pa&lt;ss&amp;wo&apos;rd</echo></root>"
    )
    from runtime.connection import Connection
    from runtime.transport import FinvaldaTransport

    connection = Connection(
        "c1", "https://fvs.example.lt/FvsServicePure.svc", "W", "", "", "", "", "lt", secret
    )
    reply = FinvaldaTransport(connection, opener=fake).request(
        "GetSandelius", verb="GET", mutating=False
    )
    assert "rd" not in json.dumps(reply.data) and "[redacted]" in json.dumps(reply.data)


def test_api_page_ids_are_plain() -> None:
    import yaml

    from support import BUNDLE

    page = yaml.safe_load((BUNDLE / "ui/pages/finvalda-api.yaml").read_text("utf-8"))
    for component in page["components"]:
        assert component["component_id"].replace("_", "").isalnum(), component["component_id"]


# -- second review (d21138d) ------------------------------------------------------


def test_export_with_decimal_numbers() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "debt": 12.5}, {"code": "K2", "debt": 0.1})
    )
    files = Files()
    response = run(fake, "export_list", {"list_type": "Clients"}, files=files)
    assert response["ok"] is True, response
    assert files.written[0]["data"].decode().splitlines() == [
        '{"code":"K1","debt":12.5}',
        '{"code":"K2","debt":0.1}',
    ]


def test_report_filter_with_a_decimal_number() -> None:
    import base64

    fake = FakeFinvalda(MakeReport={**OK, "fileContents": base64.b64encode(b"%PDF").decode()})
    response = run(
        fake, "make_report", {"report_code": "PELN_01", "filter": {"IncludeReturns": 1.0}}
    )
    assert response["ok"] is True, response
    assert fake.calls[0].query["sParam"] == '{"Code":"PELN_01","IncludeReturns":1.0}'


def test_related_invoices_of_other_clients_are_not_passed_on() -> None:
    fake = FakeFinvalda(
        GetInvoicesRelatedToCustomer=items(
            {"client": "K1", "doc": "A"}, {"client": "K2", "doc": "B"}
        )
    )
    response = run(fake, "invoices_related_to_customer", {"client_code": "K1"})
    assert response["error_code"] == "invalid_response"
    fake = FakeFinvalda(GetInvoicesRelatedToCustomer=items({"client": "K1", "doc": "A"}))
    assert run(fake, "invoices_related_to_customer", {"client_code": "K1"})["result"]["count"] == 1
    fake = FakeFinvalda(GetInvoicesRelatedToCustomer=items({"doc": "A"}))
    assert (
        run(fake, "invoices_related_to_customer", {"client_code": "K1"})["error_code"]
        == "invalid_response"
    )


def test_exactly_the_page_limit_is_not_a_conflict() -> None:
    from runtime.everyday import common as friendly
    from runtime.connection import Connection
    from runtime.context import Context
    from runtime.transport import FinvaldaTransport

    connection = Connection(
        "c1", "https://fvs.example.lt/FvsServicePure.svc", "W", "", "", "", "", "lt", "secret-pw"
    )

    def lookup(fake: FakeFinvalda) -> list[dict[str, Any]]:
        transport = FinvaldaTransport(connection, opener=fake)
        context = Context(
            payload={},
            operation_input={},
            transport=transport,
            artifact_reader=Files().reader,
            artifact_writer=Files().writer,
        )
        return friendly.describe(
            context, "Clients", {"Email": "k@k.lt"}, ["code"], limit=2, max_pages=2
        )

    exact = FakeFinvalda(
        GetDescriptions=[
            items({"code": "A"}, {"code": "B"}),
            items({"code": "C"}, {"code": "D"}),
            items(),
        ]
    )
    assert [r["code"] for r in lookup(exact)] == ["A", "B", "C", "D"]
    more = FakeFinvalda(
        GetDescriptions=[
            items({"code": "A"}, {"code": "B"}),
            items({"code": "C"}, {"code": "D"}),
            items({"code": "E"}),
        ]
    )
    with pytest.raises(ExtensionError) as exc:
        lookup(more)
    assert exc.value.code == errors.CONFLICT


def test_a_server_that_stalls_in_its_headers_is_cut_at_the_deadline() -> None:
    import time as real_time

    from runtime.connection import Connection
    from runtime.transport import FinvaldaTransport, MIN_CALL_WINDOW_SECONDS

    def stalling(request, *, timeout_seconds, purpose):
        real_time.sleep(3)  # never answers within the step
        raise AssertionError("not reached in time")

    connection = Connection(
        "c1", "https://fvs.example.lt/FvsServicePure.svc", "W", "", "", "", "", "lt", "secret-pw"
    )
    expectations: tuple[tuple[bool, type[BaseException]], ...] = (
        (False, DeadlineReached),
        (True, ExtensionError),
    )
    for mutating, expected in expectations:
        transport = FinvaldaTransport(connection, opener=stalling)
        # Room to start the call; the exchange itself must end within 0.3 s.
        transport.deadline = transport.clock() + MIN_CALL_WINDOW_SECONDS + 5
        started = real_time.monotonic()
        expiry = transport.clock() + 0.3

        def read_expiry(at: float = expiry) -> float:
            return at

        transport._read_expiry = read_expiry  # type: ignore[method-assign]
        with pytest.raises(expected) as exc:
            transport.request(
                "GetSandelius" if not mutating else "InsertNewItem",
                verb="POST",
                body={},
                mutating=mutating,
            )
        assert real_time.monotonic() - started < 2
        if mutating:
            assert isinstance(exc.value, ExtensionError)
            assert exc.value.code == errors.TIMEOUT_UNKNOWN


# -- code review (1c859a1) --------------------------------------------------------


def test_a_thousand_stock_rows_are_read_page_by_page() -> None:
    def stock(call):
        params = call.read_params()
        start = (params["page"] - 1) * params["limit"]
        rows = [
            {
                "code": f"P{n}",
                "title": f"Prekė {n}",
                "warehouse": "W",
                "warehouse_title": "Sandėlis",
                "quantity": 1,
                "quantity_with_reserved": 1,
                "measure_unit": "VNT",
                "primary_cost": 1.5,
            }
            for n in range(start, min(start + params["limit"], 1200))
        ]
        return items(*rows)

    fake = FakeFinvalda(GetDescriptions=stock)
    first = run(fake, "stock_levels", {"limit": 500})
    assert first["ok"] is True, first
    assert first["result"]["count"] == 500 and first["result"]["next_page"] == 2
    last = run(fake, "stock_levels", {"limit": 500, "page": 3})
    assert last["result"]["count"] == 200 and last["result"]["has_more"] is False


def test_password_digits_inside_numbers_and_dates_are_left_alone() -> None:
    from runtime.connection import Connection
    from runtime.transport import FinvaldaTransport

    fake = FakeFinvalda(
        GetSandelius=items({"amount": 12024.50, "date": "2024-11-10", "note": "pw 2024"})
    )
    connection = Connection(
        "c1", "https://fvs.example.lt/FvsServicePure.svc", "W", "", "", "", "", "lt", "2024"
    )
    reply = FinvaldaTransport(connection, opener=fake).request(
        "GetSandelius", verb="GET", mutating=False
    )
    row = reply.data["items"][0]
    assert row["amount"] == Decimal("12024.50")
    assert row["date"] == "[redacted]-11-10" and row["note"] == "pw [redacted]"


def test_an_answer_of_unknown_form_is_not_taken_for_no_rows() -> None:
    fake = FakeFinvalda(GetDescriptions={**OK, "Klientai": {"a": 1}, "Kita": {"b": 2}})
    response = run(fake, "find_or_create_client", {"code": "K1", "name": "X"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


def test_a_refused_store_is_never_taken_for_a_stored_file() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=nested(4, "exists"),
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "document_file": "a",
            "file_name": "S.pdf",
        },
    )
    assert response["errors"][0]["finvalda_code"] == 4
    assert [c.method for c in fake.calls if c.method == "AttachDocument"] == []


def test_store_refused_and_not_stored_before_reports_the_refusal() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=nested(3, "no content"),
        AttachDocument=nested(5, "no such document"),
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "document_file": "a",
            "file_name": "S.pdf",
        },
    )
    assert response["errors"][0]["finvalda_code"] == 3


def test_attachment_uses_the_read_back_operation_when_the_answer_names_none() -> None:
    sale = {
        "journal": "PARD",
        "op_number": 77,
        "order_number": "S-1",
        "client": "K1",
        "currency": "EUR",
        "amount": 1,
    }
    fake = FakeFinvalda(
        GetOperations=[items(), items(sale)],
        InsertNewOperation={**OK, "nResult": 0},
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=NESTED_OK,
        AttachDocument=NESTED_OK,
    )
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
        "document_file": "a",
    }
    response = run(fake, "create_sales_invoice", inputs, files=Files(b"%PDF"))
    assert response["ok"] is True, response
    assert response["result"]["journal"] == "PARD" and response["result"]["op_number"] == 77
    attach = [c for c in fake.calls if c.method == "AttachDocument"][0]
    assert attach.query["id1"] == "PARD" and attach.query["id2"] == "77"


def test_a_lookup_cut_by_the_clock_is_a_timeout() -> None:
    from runtime.everyday import common as friendly
    from runtime.connection import Connection
    from runtime.context import Context
    from runtime.transport import FinvaldaTransport

    connection = Connection(
        "c1", "https://fvs.example.lt/FvsServicePure.svc", "W", "", "", "", "", "lt", "secret-pw"
    )
    page = items({"code": "A"}, {"code": "B"})
    fake = FakeFinvalda(GetDescriptions=[page, DeadlineReached()])
    transport = FinvaldaTransport(connection, opener=fake)
    context = Context(
        payload={},
        operation_input={},
        transport=transport,
        artifact_reader=Files().reader,
        artifact_writer=Files().writer,
    )
    with pytest.raises(DeadlineReached):
        friendly.describe(context, "Clients", {"Email": "x"}, ["code"], limit=2, max_pages=3)


# -- second code review (1611add) -------------------------------------------------


@pytest.mark.parametrize(
    "due, overdue",
    [
        # 2026-11-10 00:30 at +0200 is 2026-11-09 22:30 UTC: due today, not yesterday.
        ("/Date(1794263400000+0200)/", False),
        # 2026-11-10 00:30 at +0300 is 2026-11-09 21:30 UTC.
        ("/Date(1794259800000+0300)/", False),
        # No offset: Lithuanian time (EET, +0200 in November) — the same 00:30.
        ("/Date(1794263400000)/", False),
    ],
)
def test_dotnet_dates_keep_their_offset(due: str, overdue: bool) -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K", "debt": 5}),
        GetNeapmoketiKliDok=items({"dokumentas": "A", "apmokejimo_data": due, "likusi_skola": 5}),
    )
    result = run(fake, "client_debt", {"client_code": "K1", "as_of": "2026-11-10"})["result"]
    assert result["documents"][0]["overdue"] is overdue


def test_dotnet_edit_times_compare_in_local_time() -> None:
    # 10:30 local at +0300 is 07:30 UTC; since 09:00 local must still see it.
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "A", "date_edited": "/Date(1794295800000+0300)/"})
    )
    result = run(fake, "changes_since", {"entity": "clients", "since": "2026-11-10T09:00:00"})[
        "result"
    ]
    assert [r["code"] for r in result["records"]] == ["A"]
    assert result["latest_edited"] == "2026-11-10T10:30:00+02:00"


def test_only_the_documented_empty_list_means_no_rows() -> None:
    # Every read example of Finvalda's Postman collection ("Finvaldos webservisas V2")
    # answers {"AccessResult": "Success", "error": "", "items": [...]}.
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
    }
    fake = FakeFinvalda(GetOperations=[{**OK, "items": []}, items()], InsertNewOperation=saved())
    assert run(fake, "create_sales_invoice", inputs)["result"]["created"] is True
    # No data key at all — the empty answer the collection shows for
    # GetInvoicesRelatedToCustomer ({"AccessResult": "Success", "error": "", "nResult": 0})
    # — and items: null are no rows too.
    for empty in ({**OK, "nResult": 0}, {**OK, "items": None}):
        fake = FakeFinvalda(GetOperations=[empty, items()], InsertNewOperation=saved())
        assert run(fake, "create_sales_invoice", inputs)["result"]["created"] is True, empty
    # Another data key is no answer to trust: no write.
    for answer in ({**OK, "Operations": None}, {**OK, "Operations": []}):
        fake = FakeFinvalda(GetOperations=answer)
        response = run(fake, "create_sales_invoice", inputs)
        assert response["error_code"] == "invalid_response" and fake.writes() == [], answer


def test_test_mode_checks_the_same_code_limit_as_the_step() -> None:
    codes = [f"P{n}" for n in range(300)]
    response = run(FakeFinvalda(), "product_prices", {"product_codes": codes})
    assert response["error_code"] == "invalid_payload"
    from runtime import everyday as friendly

    with pytest.raises(ExtensionError):
        friendly.validate("product_prices", {"product_codes": codes})


def test_eu_rule_without_time_zone_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta, timezone

    from runtime import values as friendly

    monkeypatch.setattr(friendly, "_zone_offset", lambda moment: None)
    assert friendly._vilnius_offset(datetime(2026, 7, 1, tzinfo=timezone.utc)) == timedelta(hours=3)
    assert friendly._vilnius_offset(datetime(2026, 11, 10, tzinfo=timezone.utc)) == timedelta(
        hours=2
    )
    # 2026: summer time from 29 March 01:00 UTC to 25 October 01:00 UTC.
    assert friendly._vilnius_offset(datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)) == timedelta(
        hours=2
    )
    assert friendly._vilnius_offset(datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)) == timedelta(
        hours=3
    )
    assert friendly._vilnius_offset(datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)) == timedelta(
        hours=2
    )


def test_invoice_rerun_does_not_take_a_refused_store_for_its_file() -> None:
    sale = {
        "journal": "PARD",
        "op_number": 77,
        "order_number": "S-1",
        "client": "K1",
        "currency": "EUR",
        "amount": 1,
    }
    fake = FakeFinvalda(
        GetOperations=items(sale),
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=nested(2, "unknown employee"),
        AttachDocument=NESTED_OK,
    )
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
        "document_file": "a",
    }
    response = run(fake, "create_sales_invoice", inputs, files=Files(b"%PDF"))
    assert response["ok"] is False
    assert "the Attach a document step" in response["error"]
    assert not [c for c in fake.calls if c.method == "AttachDocument"]


def test_attach_file_refused_store_says_how_to_finish() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=nested(4, "already there"),
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "document_file": "a",
            "file_name": "x.pdf",
        },
    )
    assert response["error_code"] == "upstream_validation_failed"
    assert "Attach a document" in response["error"]
    assert not [c for c in fake.calls if c.method == "AttachDocument"]


# -- fourth code review (dca3ebb) -------------------------------------------------


def test_client_debt_without_unpaid_documents() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K", "debt": 0}),
        GetNeapmoketiKliDok={**OK, "nResult": 0},
    )
    result = run(fake, "client_debt", {"client_code": "K1"})["result"]
    assert result["documents"] == [] and result["unpaid_total"] == "0.00"


@pytest.mark.parametrize(
    "first, second",
    [(("A1", "23"), ("A", "123")), (("AB", "12"), ("", "AB12")), (("", "AB/12"), ("", "AB 12"))],
)
def test_own_file_names_never_meet(first: tuple[str, str], second: tuple[str, str]) -> None:
    from runtime.everyday.common import own_file_name

    assert own_file_name("sale", "K1", *first) != own_file_name("sale", "K1", *second)


def test_too_long_own_file_name_is_refused() -> None:
    from runtime.everyday.common import own_file_name

    with pytest.raises(ExtensionError):
        own_file_name("purchase", "K" * 15, "S" * 10, "D" * 200)


def test_a_network_failure_on_store_is_not_taken_for_a_refusal() -> None:
    sale = {
        "journal": "PARD",
        "op_number": 77,
        "order_number": "S-1",
        "client": "K1",
        "currency": "EUR",
        "amount": 1,
    }
    fake = FakeFinvalda(
        GetOperations=items(sale),
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=OSError("connection reset"),
    )
    inputs = {
        "client_code": "K1",
        "document": "S-1",
        "warehouse": "W",
        "lines": [{"code": "P1", "quantity": 1, "price": 1}],
        "document_file": "a",
    }
    response = run(fake, "create_sales_invoice", inputs, files=Files(b"%PDF"))
    assert response["error_code"] == "timeout_unknown"
    assert not [c for c in fake.calls if c.method == "AttachDocument"]


def test_attach_file_keeps_the_refusals_facts() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument=(400, b"bad"),
    )
    response = run(
        fake,
        "attach_file",
        {
            "entity_type": 3,
            "entity_id1": "PARD",
            "entity_id2": "1",
            "document_file": "a",
            "file_name": "x.pdf",
        },
    )
    # HTTP 400 on a write is an unknown outcome: passed on as it is, no hint, no attach.
    assert response["error_code"] == "timeout_unknown"
    assert not [c for c in fake.calls if c.method == "AttachDocument"]


def test_payment_lookup_row_without_its_numbers_is_invalid() -> None:
    fake = FakeFinvalda(GetOperations=items({"order_number": "B", "client": "K1"}))
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": 1},
    )
    assert response["error_code"] == "invalid_response" and fake.writes() == []
    assert "None" not in response["error"]
