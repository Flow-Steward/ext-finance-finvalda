"""The fourth code review (F1-F10): another client's values, Lithuanian dates, the
changes cursor, wrappers of rows, XML result roots, refused file stores, cents,
password scrubbing and price rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from support import OK, FakeFinvalda, Files, items, run, saved

from runtime import values
from runtime.rows import rest_of
from runtime.transport import parse_body

CLIENT = {"code": "C1", "title": "UAB C1", "company_code": "300000001", "email": "c@c.lt"}


# -- F1: a client found by another value is not the one with the code given ------------


@pytest.mark.parametrize(
    "inputs",
    [
        {"code": "C2", "name": "X", "company_code": "300000001"},
        {"code": "C2", "name": "X", "email": "c@c.lt"},
    ],
)
def test_other_client_by_company_code_or_email_is_a_conflict(inputs: dict[str, Any]) -> None:
    fake = FakeFinvalda(GetDescriptions=[items(), items(CLIENT)])
    response = run(fake, "find_or_create_client", inputs)
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "No Finvalda client has code C2" in response["error"]


def test_same_client_by_code_is_returned() -> None:
    fake = FakeFinvalda(GetDescriptions=items(CLIENT))
    result = run(fake, "find_or_create_client", {"code": "C1", "company_code": "300000001"})[
        "result"
    ]
    assert result["client_code"] == "C1" and result["matched_by"] == "code"


# -- F2: today is the Lithuanian date ---------------------------------------------------


def _at(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr(values, "_utc_now", lambda: moment)


@pytest.mark.parametrize(
    "moment, day",
    [
        (datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc), "2026-02-01"),
        (datetime(2026, 6, 30, 21, 30, tzinfo=timezone.utc), "2026-07-01"),
        (datetime(2026, 6, 30, 20, 30, tzinfo=timezone.utc), "2026-06-30"),
    ],
)
def test_today_is_lithuanian(monkeypatch: pytest.MonkeyPatch, moment: datetime, day: str) -> None:
    _at(monkeypatch, moment)
    assert values.lithuanian_today().isoformat() == day


def test_payment_without_date_is_dated_in_lithuania(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc))
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved())
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "10.00"},
    )
    assert response["ok"] is True, response
    assert fake.writes()[0].inner()["IplDok"]["tData"] == "2026-02-01"


def test_insert_document_composition_date_is_lithuanian(monkeypatch: pytest.MonkeyPatch) -> None:
    _at(monkeypatch, datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc))
    fake = FakeFinvalda(InsertDocument={**OK, "result": {"errorCode": 0, "errorText": ""}})
    run(
        fake,
        "insert_document",
        {"document_file": "art_1", "file_name": "a.txt"},
        files=Files(b"x"),
    )
    assert fake.calls[0].body["inParams"]["compositionDate"] == {
        "year": 2026,
        "month": 2,
        "day": 1,
    }


# -- F3/F8: the changes cursor never moves past a group ---------------------------------


def _changes(rows: list[dict[str, Any]], **inputs: Any) -> dict[str, Any]:
    fake = FakeFinvalda(GetDescriptions=lambda call: items(*rows))
    response = run(fake, "changes_since", {"entity": "clients", **inputs})
    assert response["ok"] is True, response
    return response["result"]


# -- F4: the wrapper of the rows is not returned again --------------------------------


def test_rest_of_drops_the_wrapper_of_the_rows() -> None:
    rows = [{"sKodas": "K1"}, {"sKodas": "K2"}]
    data = {"AccessResult": "Success", "Klientai": {"Fvs.Klientas": rows}, "total": 2}
    assert rest_of(data, rows) == {"total": 2}
    assert rest_of(data, list(rows)) == {"total": 2}


def test_class_list_response_holds_no_copy_of_the_records() -> None:
    record = {"sKodas": "K1", "sPavadinimas": "X" * 50}
    fake = FakeFinvalda(GetKlientus={**OK, "Klientai": {"Fvs.Klientas": [record] * 3}})
    result = run(fake, "get_klientus")["result"]
    assert result["count"] == 3 and result["response"] is None


# -- F5: an XML answer that is the result element itself --------------------------------


def test_xml_out_root_keeps_its_name() -> None:
    raw = b"<InsertDocumentOut><errorCode>0</errorCode><errorText/></InsertDocumentOut>"
    assert parse_body(raw) == {"result": {"errorCode": "0", "errorText": ""}}


def test_bare_xml_out_answer_confirms_the_write() -> None:
    fake = FakeFinvalda(
        InsertDocument=(200, b"<InsertDocumentOut><errorCode>0</errorCode></InsertDocumentOut>")
    )
    response = run(
        fake, "insert_document", {"document_file": "art_1", "file_name": "a.txt"}, files=Files()
    )
    assert response["ok"] is True, response


def test_bare_xml_out_refusal_is_the_methods_own_code() -> None:
    fake = FakeFinvalda(
        InsertDocument=(
            200,
            b"<InsertDocumentOut><errorCode>2</errorCode><errorText>no user</errorText>"
            b"</InsertDocumentOut>",
        )
    )
    response = run(
        fake, "insert_document", {"document_file": "art_1", "file_name": "a.txt"}, files=Files()
    )
    assert response["error_code"] == "upstream_validation_failed"
    assert response["definitely_no_external_effect"] is True


# -- F6: a refused store is never taken for an earlier one --------------------------------


@pytest.mark.parametrize("code", [2, 3, 4])
def test_attach_file_refused_store_is_reported(code: int) -> None:
    fake = FakeFinvalda(
        GetAttachedDocument={**OK, "result": {"enitityDocs": [], "errorCode": 0}},
        InsertDocument={**OK, "result": {"errorCode": code, "errorText": "refused"}},
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
    assert response["error_code"] == "upstream_validation_failed"
    assert "the Attach a document step" in response["error"]
    assert [c.method for c in fake.calls] == ["GetAttachedDocument", "InsertDocument"]


# -- F7: a payment's amount has cents at most --------------------------------------------


@pytest.mark.parametrize("amount", ["10.005", "10.0001", 10.125])
def test_payment_amount_with_more_than_cents_is_refused(amount: Any) -> None:
    fake = FakeFinvalda()
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": amount},
    )
    assert response["error_code"] == "invalid_payload" and fake.calls == []


def test_invoice_amount_eur_with_more_than_cents_is_refused() -> None:
    line = {"code": "P1", "quantity": "1", "price": "10", "amount_eur": "9.205"}
    response = run(
        FakeFinvalda(),
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S",
            "warehouse": "W",
            "currency": "USD",
            "lines": [line],
        },
    )
    assert response["error_code"] == "invalid_payload"


# -- F9: no deep copy when the password cannot be in the answer --------------------------


def test_answer_without_the_password_is_not_copied() -> None:
    from test_transport import transport

    seen: list[Any] = []
    fake = FakeFinvalda(GetSandelius=(200, b'{"AccessResult":"Success","items":[{"a":"b"}]}'))
    t = transport(fake)
    original = t.scrub

    def watched(value: Any) -> Any:
        seen.append(value)
        return original(value)

    t.scrub = watched  # type: ignore[method-assign]
    t.request("GetSandelius", verb="GET", mutating=False)
    assert seen == []


def test_password_spelled_with_escapes_is_still_removed() -> None:
    from test_transport import transport

    secret = "Pa55"
    escaped = "".join(f"\\u{ord(c):04x}" for c in secret).encode()
    fake = FakeFinvalda(
        GetSandelius=(200, b'{"AccessResult":"Success","note":"x' + escaped + b'y"}')
    )
    reply = transport(fake, _password=secret).request("GetSandelius", verb="GET", mutating=False)
    assert secret not in str(reply.data) and "[redacted]" in reply.data["note"]


# -- F10: only the products asked for -------------------------------------------------------


def test_prices_keep_only_the_codes_asked_for() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[
            items({"product": "P1", "sell_price1": 1}, {"product": "P9", "sell_price1": 9}),
            items(
                {"client": "K1", "code": "P1", "price": 1},
                {"client": "K1", "code": "P9", "price": 9},
                {"client": "K2", "code": "P1", "price": 2},
            ),
        ]
    )
    result = run(fake, "product_prices", {"product_codes": ["P1", "P2"], "client_code": "K1"})[
        "result"
    ]
    assert [i["code"] for i in result["items"]] == ["P1"]
    assert [(p["client_code"], p["code"]) for p in result["client_prices"]] == [("K1", "P1")]


def test_prices_too_many_rows_stop_before_the_prices_read() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"product": "P1"}, {"product": "p1"}),
    )
    response = run(fake, "product_prices", {"product_codes": ["P1"], "client_code": "K1"})
    assert response["error_code"] == "invalid_response" and len(fake.calls) == 1
