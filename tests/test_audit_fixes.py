"""The external audit (logs/finvalda-review/report.md, on 0.1.0 at 20672bf): each check
of its test_audit_observations.py turned into the behaviour that is now required,
plus the review's input and output requirements."""

from __future__ import annotations

import io
import sys
from decimal import Decimal
from pathlib import Path
from functools import lru_cache
from typing import Any

import pytest
import yaml
from support import OK, FakeFinvalda, items, run, saved

import main

BUNDLE = Path(__file__).resolve().parents[1]
DOC_OK = {"AccessResult": "Success", "error": "", "result": {"errorCode": 0, "errorText": ""}}


# -- audit №1: a write is accepted only on a positive confirmation -------------------


@pytest.mark.parametrize("answer", [{}, {"AccessResult": "Success"}, {"error": "x"}])
def test_delete_document_without_confirmation_is_unknown(answer: dict[str, Any]) -> None:
    response = run(FakeFinvalda(DeleteDocument=answer), "delete_document", {"file_name": "x.pdf"})
    assert response["ok"] is False and response["error_code"] == "timeout_unknown"
    assert response["definitely_no_external_effect"] is False


def test_delete_document_confirmed() -> None:
    fake = FakeFinvalda(DeleteDocument={"AccessResult": "Success", "error": ""})
    assert run(fake, "delete_document", {"file_name": "x.pdf"})["ok"] is True


@pytest.mark.parametrize(
    "operation_id, method, inputs",
    [
        (
            "insert_item",
            "InsertNewItem",
            {"item_class": "Fvs.Klientas", "fields": {"sKodas": "K1", "sPavadinimas": "X"}},
        ),
        ("delete_item", "DeleteItem", {"item_class": "Fvs.Klientas", "item_code": "K1"}),
        ("lock_operation", "LockOperation", {"journal": "PARD", "op_number": 1}),
        ("change_journal", "ChangeJournal", {"journal": "A", "op_number": 1, "new_journal": "B"}),
    ],
)
@pytest.mark.parametrize(
    "answer", [{}, {"AccessResult": "Success"}, {"AccessResult": "Odd", "nResult": 0}]
)
def test_result_code_writes_need_nresult_zero(
    operation_id: str, method: str, inputs: dict[str, Any], answer: dict[str, Any]
) -> None:
    response = run(FakeFinvalda(**{method: answer}), operation_id, inputs)
    assert response["error_code"] == "timeout_unknown", (method, answer)


@pytest.mark.parametrize(
    "answer", [{"AccessResult": "Success", "error": ""}, {"AccessResult": "Success", "result": {}}]
)
def test_document_writes_need_their_result_object(answer: dict[str, Any]) -> None:
    response = run(
        FakeFinvalda(AttachDocument=answer),
        "attach_document",
        {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"},
    )
    assert response["error_code"] == "timeout_unknown"
    fake = FakeFinvalda(AttachDocument=DOC_OK)
    assert run(
        fake, "attach_document", {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"}
    )["ok"]


# -- audit №2: an answer of unknown form is never "no rows" --------------------------


def test_unrecognized_lookup_shape_does_not_create_a_client() -> None:
    fake = FakeFinvalda(
        GetDescriptions={"AccessResult": "Success", "unexpected": "not a rowset"},
        InsertNewItem={"AccessResult": "Success", "nResult": 0},
    )
    response = run(fake, "find_or_create_client", {"code": "NEW", "name": "New"})
    assert response["error_code"] == "invalid_response" and fake.writes() == []


# -- audit №3: no guessing which invoice a payment settles ---------------------------


def test_invoice_number_in_an_unrelated_field_is_no_match() -> None:
    row = {"order_number": "BANK-7", "client": "K1", "journal": "IPL", "op_number": 5, "amount": 99}
    fake = FakeFinvalda(GetOperations=[items(row), items({"code": "S-2"})])
    response = run(
        fake,
        "register_payment",
        {
            "direction": "incoming",
            "client_code": "K1",
            "document": "BANK-7",
            "amount": "99.00",
            "invoice_document": "S-2",
        },
    )
    assert response["error_code"] == "conflict" and fake.writes() == []


# -- audit №4: the documented continuation always moves on ---------------------------


def test_changes_cursor_moves_on() -> None:
    rows = [
        {"code": "A", "date_edited": "2026-11-09T12:00:00"},
        {"code": "B", "date_edited": "2026-11-09T13:00:00"},
    ]
    fake = FakeFinvalda(GetDescriptions=lambda call: items(*rows))
    seen: list[str] = []
    since = "2026-11-09"
    for _ in range(4):
        result = run(fake, "changes_since", {"entity": "clients", "since": since, "limit": 1})[
            "result"
        ]
        seen += [r["code"] for r in result["records"]]
        since = result["next_since"]
        if not result["has_more"]:
            break
    assert "B" in seen and seen[-1] == "B"


# -- audit №5: an existing invoice is returned only when it is this one --------------


def test_different_existing_invoice_is_a_conflict() -> None:
    row = {
        "order_number": "S-1",
        "client": "K1",
        "journal": "PARD",
        "op_number": 5,
        "amount": "999.00",
        "currency": "USD",
    }
    fake = FakeFinvalda(GetOperations=items(row))
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S-1",
            "warehouse": "W",
            "currency": "EUR",
            "lines": [{"code": "P1", "quantity": "1", "price": "5"}],
        },
    )
    assert response["error_code"] == "conflict" and fake.writes() == []
    assert "999.00 USD" in response["error"]


def test_same_existing_invoice_reports_its_own_amount() -> None:
    row = {
        "order_number": "S-1",
        "client": "K1",
        "journal": "PARD",
        "op_number": 5,
        "amount": "6.05",
        "currency": "EUR",
    }
    fake = FakeFinvalda(GetOperations=items(row))
    response = run(
        fake,
        "create_sales_invoice",
        {
            "client_code": "K1",
            "document": "S-1",
            "warehouse": "W",
            "lines": [{"code": "P1", "quantity": "1", "price": "5", "vat_percent": 21}],
        },
    )
    result = response["result"]
    assert result["created"] is False and result["amount"] == "6.05" and result["currency"] == "EUR"
    assert result["total_without_vat"] is None and result["invoice"]["amount"] == "6.05"


# -- audit №6: input numbers stay exact ---------------------------------------------


def test_entrypoint_keeps_input_numbers_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        seen.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(main, "dispatch_runtime", dispatch)
    raw = b'{"amount":1234567890.12345678,"action":{"input":{"amount":0.10000000000000001}}}'
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO()))
    assert main.main() == 0
    assert seen["amount"] == Decimal("1234567890.12345678")
    assert seen["action"]["input"]["amount"] == Decimal("0.10000000000000001")


def test_exact_input_amount_reaches_finvalda() -> None:
    fake = FakeFinvalda(GetOperations=[items(), items()], InsertNewOperation=saved())
    response = run(
        fake,
        "register_payment",
        {
            "direction": "incoming",
            "client_code": "K1",
            "document": "B",
            "amount": Decimal("1234.56"),
        },
    )
    assert response["ok"] is True
    assert fake.writes()[0].inner()["IplDok"]["IplDokDetEil"][0]["dSumaV"] == "1234.56"


# -- audit №7: the password is removed from parsed text only -------------------------


def test_numeric_password_leaves_numbers_intact() -> None:
    from test_transport import transport

    fake = FakeFinvalda(
        GetSandelius=(200, b'{"AccessResult":"Success","items":[{"amount":1234.56}]}')
    )
    reply = transport(fake, _password="1234").request("GetSandelius", verb="GET", mutating=False)
    assert reply.data["items"][0]["amount"] == Decimal("1234.56")


# -- audit №8: the Finvalda employee ---------------------------------------------------


def test_connection_texts_name_no_fallback_employee() -> None:
    for path in (
        "ui/pages/connection.yaml",
        "contracts/connection_types.yaml",
        "ui/pages/setup-guide.yaml",
    ):
        text = (BUNDLE / path).read_text("utf-8").lower()
        assert "default the web service user" not in text, path
    assert "finvalda employee" in (BUNDLE / "ui/pages/setup-guide.yaml").read_text("utf-8").lower()


def test_test_connection_warns_without_an_employee() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "K1"}))
    result = run(fake, "test_connection", config={"fin_user": ""})["result"]
    assert any("Finvalda employee" in w for w in result["warnings"])


# -- architecture: diagnostics without data ------------------------------------------


def test_internal_error_names_type_and_place(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from runtime.everyday import reads

    def broken(*_a: Any, **_k: Any) -> Any:
        raise KeyError("secret-value")

    monkeypatch.setattr(reads, "read_list", broken)
    response = run(FakeFinvalda(), "test_connection")
    assert response["error_code"] == "internal_error"
    assert "KeyError at runtime/" in response["error"] and "secret-value" not in response["error"]
    assert "KeyError at runtime/" in capsys.readouterr().err


def test_a_failed_read_back_is_reported() -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), (500, b"down")], InsertNewItem={**OK, "nResult": 0}
    )
    result = run(fake, "find_or_create_service", {"code": "S1", "name": "X"})["result"]
    assert result["created"] is True and result["service"] is None and result["warnings"]


# -- normal inputs and outputs ---------------------------------------------------------


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    return {
        op["operation_id"]: op
        for op in yaml.safe_load((BUNDLE / "contracts/operation_manifest.yaml").read_text("utf-8"))[
            "operations"
        ]
    }


def test_everyday_records_are_described_and_closed() -> None:
    ops = _manifest()
    client = next(o for o in ops["find_or_create_client"]["outputs"] if o["name"] == "client")
    assert client["schema"]["additionalProperties"] is False
    assert client["schema"]["properties"]["debt"]["anyOf"][0]["pattern"]
    debt = next(o for o in ops["client_debt"]["outputs"] if o["name"] == "documents")
    due = debt["schema"]["items"]["properties"]["due_date"]["anyOf"][0]
    assert due["pattern"] == r"^\d{4}-\d{2}-\d{2}$"
    for op in ops.values():
        names = {o["name"] for o in op["outputs"]}
        assert "record" not in names and "finvalda_result" not in names, op["operation_id"]


def test_everyday_results_are_normalised() -> None:
    row = {"code": "K1", "title": "UAB K", "debt": 12.5, "date_edited": "2026.11.10 10:30"}
    result = run(FakeFinvalda(GetDescriptions=items(row)), "find_or_create_client", {"code": "K1"})[
        "result"
    ]
    assert result["client"]["name"] == "UAB K" and result["client"]["debt"] == "12.5"
    assert result["client"]["edited_at"] == "2026-11-10T10:30:00+02:00"
    assert "title" not in result["client"]


def test_advanced_filters_and_columns_are_closed_lists() -> None:
    ops = _manifest()
    clients = {i["name"]: i for i in ops["descriptions_clients"]["inputs"]}
    flt = clients["filter"]["schema"]
    assert flt["additionalProperties"] is False and "Codes" in flt["properties"]
    assert flt["properties"]["Codes"]["type"] == "array"
    assert "enum" in clients["columns"]["schema"]["items"]
    sales = {i["name"]: i for i in ops["operations_sales"]["inputs"]}
    assert "Warehouse" not in sales["filter"]["schema"]["properties"]
    assert "op_number" in sales["columns"]["schema"]["items"]["enum"]


def test_older_method_columns_have_the_manuals_types() -> None:
    ops = _manifest()
    records = next(o for o in ops["get_klientus_set"]["outputs"] if o["name"] == "records")
    columns = records["schema"]["items"]["properties"]
    assert columns["skola"]["anyOf"][0]["pattern"] and columns["kurimo_data"]["anyOf"][0]["pattern"]
    assert columns["atsiskaityti_per"]["anyOf"][0]["type"] == ["integer", "null"]


def test_advanced_rows_get_iso_times() -> None:
    fake = FakeFinvalda(
        GetKlientusSet=items(
            {"kodas": "K1", "skola": 1.5, "kurimo_data": "2022-04-11T14:05:17.561696"}
        )
    )
    row = run(fake, "get_klientus_set")["result"]["records"][0]
    assert row["skola"] == "1.5" and row["kurimo_data"] == "2022-04-11T14:05:17.561696+03:00"


def test_undocumented_column_is_refused() -> None:
    response = run(FakeFinvalda(), "descriptions_clients", {"columns": ["colour"]})
    assert response["error_code"] == "invalid_payload"


def test_api_page_shows_types_and_hides_plumbing() -> None:
    text = (BUNDLE / "ui/pages/finvalda-api.yaml").read_text("utf-8")
    assert "finvalda_result" not in text and "external_effect" not in text
    assert "- `client` (record): The client. Fields: `code`, `name`" in text
    assert "- `amount` (decimal, optional)" in text or "(decimal" in text


def test_dates_accept_iso() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K", "debt": 0}),
        GetNeapmoketiKliDok={**OK, "nResult": 0},
    )
    assert run(fake, "client_debt", {"client_code": "K1", "as_of": "2026-11-10T09:00:00+02:00"})[
        "ok"
    ]


def test_the_audit_observations_now_fail() -> None:
    """Every check of the audit's file asserts a defect; none may hold any more."""
    source = Path("/home/brizzl/www/agentic-team/logs/finvalda-review/test_audit_observations.py")
    if not source.exists():
        pytest.skip("the audit file is outside this repository")
    namespace: dict[str, Any] = {"__name__": "audit"}
    exec(compile(source.read_text("utf-8"), str(source), "exec"), namespace)  # noqa: S102
    for name, check in namespace.items():
        if not name.startswith("test_"):
            continue
        with pytest.raises((AssertionError, KeyError)):
            if "monkeypatch" in check.__code__.co_varnames:
                mp = pytest.MonkeyPatch()
                try:
                    check(mp)
                finally:
                    mp.undo()
            else:
                check()
