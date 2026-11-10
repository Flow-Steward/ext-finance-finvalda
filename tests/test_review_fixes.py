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

PAYMENT = {"direction": "incoming", "client_code": "K1", "document": "BANK-7", "amount": "99.00"}


def test_other_payment_on_the_same_statement_is_registered() -> None:
    fake = FakeFinvalda(
        GetOperations=[
            items(
                {
                    "order_number": "BANK-7",
                    "client": "K1",
                    "journal": "IPL",
                    "op_number": 5,
                    "amount": 10,
                }
            ),
            items(),
        ],
        InsertNewOperation=saved("IPL", 6),
    )
    response = run(fake, "register_payment", {**PAYMENT, "invoice_document": "S-2"})
    assert response["ok"] is True and response["result"]["created"] is True, response


def test_same_payment_again_is_a_rerun() -> None:
    row = {"order_number": "BANK-7", "client": "K1", "journal": "IPL", "op_number": 5, "amount": 99}
    fake = FakeFinvalda(
        GetOperations=[
            items(row),
            items({"journal": "IPL", "op_number": 5, "order_number": "S-2", "amount": 99}),
        ]
    )
    response = run(fake, "register_payment", {**PAYMENT, "invoice_document": "S-2"})
    assert response["ok"] is True and response["result"]["created"] is False, response
    assert fake.calls[1].op_params() == {
        "OpClass": "InflowsDet",
        "fullOp": False,
        "filter": {"Journal": "IPL", "OpNumber": 5},
    }
    assert fake.writes() == []


def test_same_amount_for_another_invoice_is_registered() -> None:
    row = {"order_number": "BANK-7", "client": "K1", "journal": "IPL", "op_number": 5, "amount": 99}
    fake = FakeFinvalda(
        GetOperations=[
            items(row),
            items({"journal": "IPL", "op_number": 5, "order_number": "S-1"}),
            items(),
        ],
        InsertNewOperation=saved("IPL", 6),
    )
    response = run(fake, "register_payment", {**PAYMENT, "invoice_document": "S-2"})
    assert response["result"]["created"] is True, response


def test_undecidable_payment_stops_the_step() -> None:
    row = {"order_number": "BANK-7", "client": "K1", "journal": "IPL", "op_number": 5, "amount": 99}
    fake = FakeFinvalda(GetOperations=[items(row), items()])
    response = run(fake, "register_payment", {**PAYMENT, "invoice_document": "S-2"})
    assert response["error_code"] == "conflict" and fake.writes() == []
    fake = FakeFinvalda(GetOperations=items(row, {**row, "op_number": 6}))
    assert run(fake, "register_payment", PAYMENT)["error_code"] == "conflict"
    fake = FakeFinvalda(GetOperations=items({k: v for k, v in row.items() if k != "amount"}))
    assert run(fake, "register_payment", PAYMENT)["error_code"] == "conflict"


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
    fake = FakeFinvalda(GetDescriptions=[full] * 5)
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
    assert (
        result["items"][0]["quantity"] == "0.1"
        and result["items"][0]["primary_cost"] == "12345678.905"
    )


# -- smaller findings --------------------------------------------------------------


def test_failure_without_a_result_code_on_a_write_is_unknown() -> None:
    fake = FakeFinvalda(ChangeJournal={"AccessResult": "Fail", "error": "klaida"})
    response = run(fake, "change_journal", {"journal": "A", "op_number": 1, "new_journal": "B"})
    assert (
        response["error_code"] == "timeout_unknown"
        and response["definitely_no_external_effect"] is False
    )


def test_changes_since_with_a_time_filters_the_day() -> None:
    rows = [
        {"code": "A", "date_edited": "2026-11-01T09:00:00"},
        {"code": "B", "date_edited": "2026-11-01T11:00:00"},
    ]
    fake = FakeFinvalda(GetDescriptions=items(*rows))
    response = run(fake, "changes_since", {"entity": "clients", "since": "2026-11-01T10:00:00"})
    assert [r["code"] for r in response["result"]["records"]] == ["B"]
    assert fake.calls[0].read_params()["Clients"] == {"EditedFrom": "2026-11-01"}


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
    assert response["result"]["attachment_name"] == "purchase-SUP-AB1.pdf", response


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
