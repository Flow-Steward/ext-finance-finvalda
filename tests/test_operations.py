"""Advanced steps through handle_runtime, against a fake Finvalda."""

from __future__ import annotations

import base64
import json

from support import OK, FakeFinvalda, Files, items, refused, run, saved


def test_older_read_sends_the_manuals_parameters() -> None:
    fake = FakeFinvalda(GetKlientusSet=items({"kodas": "K1", "pavadinimas": "UAB K"}))
    response = run(fake, "get_klientus_set", {"client_code": "K1", "edited_since": "2026-01-01"})
    assert response["ok"] is True, response
    call = fake.calls[0]
    assert call.verb == "GET"
    assert call.query == {"sKliKod": "K1", "tKoregavimoData": "2026-01-01"}
    assert response["result"]["records"] == [{"kodas": "K1", "pavadinimas": "UAB K"}]
    assert response["result"]["count"] == 1


def test_reported_parameter_names_are_sent_too() -> None:
    fake = FakeFinvalda(GetPrekesSandelyje=items())
    run(fake, "get_prekes_sandelyje", {"warehouse_code": "CENTR"})
    assert fake.calls[0].query == {"sSanKod": "CENTR", "sSandKod": "CENTR"}


def test_unknown_input_and_bad_date_are_refused_before_any_call() -> None:
    fake = FakeFinvalda()
    for inputs in ({"client_code": "K1", "nope": 1}, {"edited_since": "01.01.2026"}):
        response = run(fake, "get_klientus_set", inputs)
        assert response["ok"] is False and response["error_code"] == "invalid_payload"
        assert response["definitely_no_external_effect"] is True
    assert fake.calls == []


def test_rows_inside_a_named_list_are_found() -> None:
    fake = FakeFinvalda(
        GetSandelius={**OK, "Sandeliai": {"Eil": [{"kodas": "S1"}, {"kodas": "S2"}]}}
    )
    response = run(fake, "get_sandelius")
    assert response["result"]["count"] == 2


def test_descriptions_body_and_pages() -> None:
    pages = [items(*({"code": f"K{i}"} for i in range(2))), items({"code": "K9"})]
    fake = FakeFinvalda(GetDescriptions=pages)
    response = run(
        fake,
        "descriptions_clients",
        {
            "filter": {"codes": ["K1"], "EditedFrom": "2026-01-01"},
            "columns": ["code"],
            "limit": 2,
            "fetch_all_pages": True,
        },
    )
    assert response["ok"] is True, response
    first = fake.calls[0].read_params()
    assert first == {
        "type": "Clients",
        "page": 1,
        "limit": 2,
        "Clients": {"Codes": ["K1"], "EditedFrom": "2026-01-01"},
        "columns": ["code"],
    }
    assert fake.calls[1].read_params()["page"] == 2
    assert response["result"]["count"] == 3 and response["result"]["has_more"] is False


def test_descriptions_refuse_an_undocumented_filter() -> None:
    fake = FakeFinvalda()
    response = run(fake, "descriptions_clients", {"filter": {"Colour": "red"}})
    assert response["error_code"] == "invalid_payload" and "Colour" in response["error"]
    response = run(fake, "descriptions_vehicles", {"filter": {"Codes": ["A"]}})
    assert response["error_code"] == "invalid_payload"
    assert fake.calls == []


def test_descriptions_with_two_filter_groups_take_nested_objects() -> None:
    fake = FakeFinvalda(GetDescriptions=items())
    response = run(
        fake,
        "descriptions_address",
        {"filter": {"Clients": {"Codes": ["K1"]}, "Address": {"Tag1": "X"}}},
    )
    assert response["ok"] is True, response
    params = fake.calls[0].read_params()
    assert params["Clients"] == {"Codes": ["K1"]} and params["Address"] == {"Tag1": "X"}


def test_operations_filters_follow_the_class() -> None:
    fake = FakeFinvalda(GetOperations=items({"op_number": 1}))
    response = run(
        fake,
        "operations_sales",
        {
            "filter": {"OpDateFrom": "2026-01-01", "Client": "K1"},
            "full_op": True,
            "columns_det": ["code"],
        },
    )
    assert response["ok"] is True, response
    assert fake.calls[0].op_params() == {
        "OpClass": "Sales",
        "filter": {"OpDateFrom": "2026-01-01", "Client": "K1"},
        "fullOp": True,
        "columnsDet": {"column": ["code"]},
    }
    response = run(fake, "operations_sales", {"filter": {"Warehouse": "S1"}})
    assert response["error_code"] == "invalid_payload"
    response = run(fake, "operations_sales_det", {"filter": {"Warehouse": "S1"}})
    assert response["ok"] is True
    response = run(fake, "operations_sales_det", {"full_op": True})
    assert response["error_code"] == "invalid_payload"


def test_insert_item_checks_fields_and_adds_the_item_parameter() -> None:
    fake = FakeFinvalda(InsertNewItem={**OK, "nResult": 0})
    response = run(
        fake,
        "insert_item",
        {
            "item_class": "Fvs.Klientas",
            "fields": {"sKodas": "K1", "sPavadinimas": "UAB K", "nTipas": 0},
        },
    )
    assert response["ok"] is True, response
    body = fake.calls[0].body
    assert body["ItemClassName"] == "Fvs.Klientas"
    assert json.loads(body["xmlstring"]) == {
        "Fvs.Klientas": {
            "sFvsImportoParametras": "PARAM_APR",
            "sKodas": "K1",
            "sPavadinimas": "UAB K",
            "nTipas": 0,
        }
    }
    assert response["external_effect_status"] == "succeeded"


def test_insert_item_refusals_before_sending() -> None:
    fake = FakeFinvalda()
    cases = [
        {
            "item_class": "Fvs.Klientas",
            "fields": {"sKodas": "K1", "sPavadinimas": "X", "sSpalva": "red"},
        },
        {"item_class": "Fvs.Klientas", "fields": {"sKodas": "K" * 16, "sPavadinimas": "X"}},
        {"item_class": "Fvs.Klientas", "fields": {"sKodas": "K1"}},
        {
            "item_class": "Fvs.Klientas",
            "fields": {"sKodas": "K1", "sPavadinimas": "X", "nTipas": "one"},
        },
        {"item_class": "Fvs.Nothing", "fields": {"sKodas": "K1"}},
    ]
    for inputs in cases:
        response = run(fake, "insert_item", inputs)
        assert response["error_code"] == "invalid_payload", inputs
        assert response["definitely_no_external_effect"] is True
    assert fake.calls == []


def test_client_individual_card_from_the_postman_example() -> None:
    fake = FakeFinvalda(InsertNewItem={**OK, "nResult": 0})
    response = run(
        fake,
        "insert_item",
        {"item_class": "Fvs.KlientoIndividKort", "fields": {"code": "C2", "client": "ABRO"}},
    )
    assert response["ok"] is True, response
    assert json.loads(fake.calls[0].body["xmlstring"]) == {"code": "C2", "client": "ABRO"}


def test_edit_item_needs_a_code() -> None:
    fake = FakeFinvalda(EditItem={**OK, "nResult": 0})
    assert (
        run(fake, "edit_item", {"item_class": "Fvs.Preke", "fields": {"sPavadinimas": "X"}})[
            "error_code"
        ]
        == "invalid_payload"
    )
    response = run(
        fake,
        "edit_item",
        {"item_class": "Fvs.Preke", "item_code": "P1", "fields": {"sPavadinimas": "X"}},
    )
    assert response["ok"] is True and fake.calls[0].body["sItemCode"] == "P1"


def test_insert_operation_with_lines() -> None:
    fake = FakeFinvalda(InsertNewOperation=saved("PARD", 61, "AB", "S-1"))
    operation = {
        "sKlientas": "K1",
        "sDokumentas": "S-1",
        "sValiuta": "EUR",
        "tData": "2026-11-10",
        "PardDokPrekeDetEil": {
            "sKodas": "P1",
            "sSandelis": "CENTR",
            "dSumaV": 10,
            "dSumaL": 10,
            "nKiekis": 1,
        },
    }
    response = run(fake, "insert_operation", {"op_class": "PardDok", "operation": operation})
    assert response["ok"] is True, response
    body = fake.calls[0].body
    assert body["sParametras"] == "PARAM_PARD"
    inner = json.loads(body["xmlstring"])["PardDok"]
    assert inner["PardDokPrekeDetEil"] == [
        {"sKodas": "P1", "sSandelis": "CENTR", "dSumaV": "10", "dSumaL": "10", "nKiekis": "1"}
    ]
    result = response["result"]
    assert (result["journal"], result["op_number"], result["series"], result["document"]) == (
        "PARD",
        61,
        "AB",
        "S-1",
    )


def test_insert_operation_refuses_lines_of_another_class_and_a_missing_parameter() -> None:
    fake = FakeFinvalda()
    operation = {
        "sKlientas": "K1",
        "sDokumentas": "S-1",
        "sValiuta": "EUR",
        "tData": "2026-11-10",
        "PirkDokPrekeDetEil": [{}],
    }
    assert (
        run(fake, "insert_operation", {"op_class": "PardDok", "operation": operation})["error_code"]
        == "invalid_payload"
    )
    good = {k: v for k, v in operation.items() if k != "PirkDokPrekeDetEil"}
    response = run(
        fake,
        "insert_operation",
        {"op_class": "PardDok", "operation": good},
        config={"operation_parameter": ""},
    )
    assert response["error_code"] == "invalid_payload" and "import_parameter" in response["error"]
    assert fake.calls == []


def test_finvalda_refusal_of_an_operation_has_no_effect() -> None:
    fake = FakeFinvalda(InsertNewOperation=refused(1036, "Parameter not found"))
    operation = {"sKlientas": "K1", "sDokumentas": "S-1", "sValiuta": "EUR", "tData": "2026-11-10"}
    response = run(fake, "insert_operation", {"op_class": "PardDok", "operation": operation})
    assert response["error_code"] == "upstream_validation_failed"
    assert response["definitely_no_external_effect"] is True
    assert response["errors"][0]["finvalda_code"] == 1036


def test_update_delete_lock_journal_copy() -> None:
    fake = FakeFinvalda(
        UpdateOperation=saved(),
        DeleteOperation=saved(),
        LockOperation={**OK, "nResult": 0},
        IsOperationLocked={**OK, "IsLocked": 2},
        ChangeJournal=saved("GAM2", 4),
        CopyOperation=saved("NEW", 5),
    )
    update = {
        "sZurnalas": "PARD",
        "nNumeris": 19,
        "DelPrekeDetEil": [{"sKodas": "P1", "sSandelis": "S1"}],
    }
    assert (
        run(fake, "update_operation", {"op_class": "KoregPardDok", "operation": update})["ok"]
        is True
    )
    assert json.loads(fake.calls[-1].body["xmlstring"]) == {"KoregPardDok": update}
    assert (
        run(
            fake, "delete_operation", {"op_class": "DelPardDok", "journal": "PARD", "op_number": 19}
        )["ok"]
        is True
    )
    assert json.loads(fake.calls[-1].body["xmlstring"]) == {
        "DelPardDok": {"sZurnalas": "PARD", "nNumeris": 19}
    }
    assert run(fake, "lock_operation", {"journal": "PARD", "op_number": 19})["ok"] is True
    locked = run(fake, "is_operation_locked", {"journal": "PARD", "op_number": 19})
    assert locked["result"] == {"locked": True, "lock_state": 2}
    moved = run(
        fake, "change_journal", {"journal": "GAMYBA", "op_number": 4, "new_journal": "GAM2"}
    )
    assert fake.calls[-1].body == {"sJournal": "GAMYBA", "nOpNumber": 4, "sJournalNew": "GAM2"}
    assert moved["result"]["journal"] == "GAM2"
    copied = run(
        fake,
        "copy_operation",
        {"journal": "PIR", "op_number": 13, "new_journal": "NEW", "keep_document": True},
    )
    assert fake.calls[-1].body["input"]["sParameter"] == "PARAM_PARD"
    assert copied["result"]["op_number"] == 5


def test_invoice_pdf_is_saved_as_a_file() -> None:
    pdf = b"%PDF-1.4 invoice"
    fake = FakeFinvalda(MakeInvoice={**OK, "fileContents": base64.b64encode(pdf).decode()})
    files = Files()
    response = run(
        fake,
        "make_invoice",
        {"invoice_form": "PARD_01", "journal": "PARD", "op_number": 45151},
        files=files,
    )
    assert response["ok"] is True, response
    assert json.loads(fake.calls[0].query["sParam"]) == {
        "FakturosKodas": "PARD_01",
        "sSerija": "",
        "sDokumentas": "",
        "sZurnalas": "PARD",
        "nNumeris": 45151,
    }
    assert files.written[0]["data"] == pdf
    assert response["result"]["size_bytes"] == len(pdf)
    assert run(fake, "make_invoice", {"invoice_form": "PARD_01"})["error_code"] == "invalid_payload"


def test_attached_documents_listing_and_saving() -> None:
    answer = {
        **OK,
        "enitityDocs": [{"idString1": "PARD", "docs": [{"name": "a.txt", "data": "4C6F72656D"}]}],
    }
    fake = FakeFinvalda(GetAttachedDocument=[answer, answer])
    files = Files()
    response = run(
        fake, "attached_documents", {"entity_type": 3, "entity_id1": "PARD", "entity_id2": "42"}
    )
    assert response["result"]["files"] == [{"name": "a.txt", "size_bytes": 5}]
    assert fake.calls[0].query == {"entityType": "3", "id1": "PARD", "id2": "42"}
    response = run(
        fake,
        "attached_documents",
        {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.txt"},
        files=files,
    )
    assert files.written[0]["data"] == b"Lorem"


def test_attach_document_already_attached_is_not_an_error() -> None:
    fake = FakeFinvalda(
        AttachDocument={"AccessResult": "Fail", "errorText": "already", "errorCode": 1}
    )
    response = run(
        fake,
        "attach_document",
        {"entity_type": 3, "entity_id1": "PARD", "entity_id2": "42", "file_name": "a.pdf"},
    )
    assert response["ok"] is True, response
    assert response["result"] == {
        "attached": False,
        "already_attached": True,
        "external_effect_status": "succeeded",
        "definitely_no_external_effect": False,
    }
    assert fake.calls[0].query["finUser"] == "WEB"


def test_insert_document_sends_hex() -> None:
    fake = FakeFinvalda(InsertDocument={"AccessResult": "Success", "error": ""})
    files = Files(b"Lorem")
    response = run(
        fake,
        "insert_document",
        {"document_file": "art_1", "file_name": "a.txt", "composition_date": "2026-11-10"},
        files=files,
    )
    assert response["ok"] is True, response
    params = fake.calls[0].body["inParams"]
    assert params["content"] == "4C6F72656D"
    assert params["compositionDate"] == {"year": 2026, "month": 11, "day": 10}
    assert (
        run(fake, "insert_document", {"document_file": "art_1", "file_name": "../x"})["error_code"]
        == "invalid_payload"
    )


def test_call_method_is_limited_to_documented_methods() -> None:
    fake = FakeFinvalda(GetUzsakytasPrekes=items(), InsertNewItem=(500, b"boom"))
    assert (
        run(fake, "call_method", {"method": "GetFvsUser", "verb": "GET"})["error_code"]
        == "invalid_payload"
    )
    assert (
        run(fake, "call_method", {"method": "DropDatabase", "verb": "POST"})["error_code"]
        == "invalid_payload"
    )
    ok = run(
        fake,
        "call_method",
        {"method": "GetUzsakytasPrekes", "verb": "GET", "query": {"sPrekesKodas": "P1"}},
    )
    assert ok["ok"] is True and fake.calls[0].query == {"sPrekesKodas": "P1"}
    failed = run(fake, "call_method", {"method": "InsertNewItem", "verb": "POST", "body": {}})
    assert (
        failed["error_code"] == "timeout_unknown"
        and failed["definitely_no_external_effect"] is False
    )


def test_test_mode_writes_send_nothing() -> None:
    fake = FakeFinvalda()
    operation = {"sKlientas": "K1", "sDokumentas": "S-1", "sValiuta": "EUR", "tData": "2026-11-10"}
    response = run(
        fake, "insert_operation", {"op_class": "PardDok", "operation": operation}, test_mode=True
    )
    assert response["ok"] is True and response["definitely_no_external_effect"] is True
    bad = run(
        fake, "insert_operation", {"op_class": "PardDok", "operation": {"sNope": 1}}, test_mode=True
    )
    assert bad["error_code"] == "invalid_payload"
    assert fake.calls == []


def test_export_writes_every_page_as_json_lines() -> None:
    full = items(*({"code": f"K{i}"} for i in range(1000)))
    fake = FakeFinvalda(GetDescriptions=[full, items({"code": "LAST"})])
    files = Files()
    response = run(fake, "export_list", {"list_type": "Clients", "columns": ["code"]}, files=files)
    assert response["ok"] is True, response
    result = response["result"]
    assert (
        result["record_count"] == 1001
        and result["complete"] is True
        and result["pages_fetched"] == 2
    )
    lines = files.written[0]["data"].decode().splitlines()
    assert json.loads(lines[-1]) == {"code": "LAST"}


def test_unknown_operation_is_refused() -> None:
    response = run(FakeFinvalda(), "drop_everything")
    assert response["error_code"] == "unsupported_operation"
