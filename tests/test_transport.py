"""Transport: headers, Finvalda's verdicts, outcomes of writes, redaction, XML, connection checks."""

from __future__ import annotations

import pytest
from support import BASE_URL, OK, PASSWORD, FakeFinvalda, refused, saved

from runtime import errors
from runtime.connection import Connection, base_url_of, connection_from_payload
from runtime.errors import ExtensionError
from runtime.transport import FinvaldaTransport, parse_body


def connection(**kw) -> Connection:
    values = {
        "connection_id": "c1",
        "base_url": BASE_URL,
        "user_name": "WEB",
        "company_id": "IMONE",
        "operation_parameter": "P",
        "item_parameter": "",
        "fin_user": "",
        "language": "lt",
        "_password": PASSWORD,
    }
    values.update(kw)
    return Connection(**values)


def transport(fake: FakeFinvalda, **kw) -> FinvaldaTransport:
    return FinvaldaTransport(connection(**kw), opener=fake, sleeper=lambda _s: None)


def test_headers_and_address() -> None:
    fake = FakeFinvalda(GetSandelius={**OK, "items": []})
    transport(fake).request("GetSandelius", verb="GET", mutating=False)
    call = fake.calls[0]
    assert call.url == f"{BASE_URL}/GetSandelius"
    assert call.headers["username"] == "WEB"
    assert call.headers["password"] == PASSWORD
    assert call.headers["companyid"] == "IMONE"
    assert call.headers["language"] == "0"
    assert call.headers["accept"] == "application/json"


def test_language_override() -> None:
    fake = FakeFinvalda(GetSandelius={**OK, "items": []})
    transport(fake, language="en").request(
        "GetSandelius", verb="GET", mutating=False, language="lt"
    )
    assert fake.calls[0].headers["language"] == "0"


def test_access_denied_is_authentication_failed_with_no_effect() -> None:
    fake = FakeFinvalda(InsertNewOperation={"AccessResult": "AccessDenied", "error": "denied"})
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("InsertNewOperation", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.AUTHENTICATION_FAILED
    assert exc.value.definitely_no_external_effect is True


def test_refusal_carries_code_and_meaning_and_no_effect() -> None:
    fake = FakeFinvalda(InsertNewOperation=refused(1001, "dokumentas naudojamas"))
    t = transport(fake)
    with pytest.raises(ExtensionError) as exc:
        t.request("InsertNewOperation", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.CONFLICT
    assert exc.value.finvalda_code == 1001
    assert "1001" in exc.value.message and "naudojamas pardavimo dokumentas" in exc.value.message
    assert exc.value.definitely_no_external_effect is True
    assert t.committed is False


@pytest.mark.parametrize("code", [2001, 2002, 2010, 2031])
def test_program_and_transaction_errors_on_writes_are_unknown(code: int) -> None:
    fake = FakeFinvalda(InsertNewOperation=refused(code))
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("InsertNewOperation", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.TIMEOUT_UNKNOWN
    assert exc.value.definitely_no_external_effect is False


def test_success_with_nonzero_result_is_a_refusal() -> None:
    fake = FakeFinvalda(EditItem={"AccessResult": "Success", "error": "x", "nResult": 3})
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("EditItem", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.CONFLICT


def test_saved_write_is_committed() -> None:
    fake = FakeFinvalda(InsertNewOperation=saved())
    t = transport(fake)
    reply = t.request("InsertNewOperation", verb="POST", body={}, mutating=True)
    assert reply.data["nResult"] == 0 and t.committed and t.mutated


@pytest.mark.parametrize("status", [400, 500, 502, 504])
def test_server_errors_on_writes_are_unknown(status: int) -> None:
    fake = FakeFinvalda(InsertNewItem=(status, b"<html>Request Error</html>"))
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("InsertNewItem", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.TIMEOUT_UNKNOWN
    assert exc.value.definitely_no_external_effect is False


def test_server_error_on_a_read_has_no_effect() -> None:
    fake = FakeFinvalda(GetSandelius=(500, b"oops"))
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("GetSandelius", verb="GET", mutating=False)
    assert exc.value.code == errors.UPSTREAM_FAILURE
    assert exc.value.definitely_no_external_effect is True


def test_missing_method_is_not_found() -> None:
    fake = FakeFinvalda(GetSandelius=(404, b"Endpoint not found."))
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("GetSandelius", verb="GET", mutating=False)
    assert exc.value.code == errors.NOT_FOUND


def test_401_is_authentication_failed() -> None:
    fake = FakeFinvalda(GetSandelius=(401, b""))
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("GetSandelius", verb="GET", mutating=False)
    assert exc.value.code == errors.AUTHENTICATION_FAILED


def test_timeouts() -> None:
    fake = FakeFinvalda(InsertNewItem=TimeoutError(), GetSandelius=TimeoutError())
    t = transport(fake)
    with pytest.raises(ExtensionError) as exc:
        t.request("InsertNewItem", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.TIMEOUT_UNKNOWN
    with pytest.raises(ExtensionError) as exc:
        t.request("GetSandelius", verb="GET", mutating=False)
    assert exc.value.code == errors.TIMEOUT and exc.value.definitely_no_external_effect is True


def test_unreadable_answer_to_a_write_is_unknown() -> None:
    fake = FakeFinvalda(InsertNewItem=b"not json")
    with pytest.raises(ExtensionError) as exc:
        transport(fake).request("InsertNewItem", verb="POST", body={}, mutating=True)
    assert exc.value.code == errors.TIMEOUT_UNKNOWN


def test_password_is_redacted_from_answers() -> None:
    fake = FakeFinvalda(GetSandelius={**OK, "items": [{"echo": f"pw={PASSWORD}"}]})
    reply = transport(fake).request("GetSandelius", verb="GET", mutating=False)
    assert PASSWORD not in str(reply.data)


def test_xml_answers_are_parsed_and_dtds_refused() -> None:
    data = parse_body(
        b"<root><AccessResult>Success</AccessResult><Eil><kodas>A</kodas></Eil><Eil><kodas>B</kodas></Eil></root>"
    )
    assert data["Eil"] == [{"kodas": "A"}, {"kodas": "B"}]
    with pytest.raises(ValueError):
        parse_body(b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')


def test_method_names_are_checked() -> None:
    fake = FakeFinvalda()
    with pytest.raises(ExtensionError):
        transport(fake).request("../admin", verb="GET", mutating=False)
    assert fake.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://fvs.example.lt/FvsServicePure.svc",
        "https://10.0.0.5/FvsServicePure.svc",
        "https://localhost/FvsServicePure.svc",
        "https://fvs.example.lt",
        "https://fvs.example.lt/FvsServicePure.svc?x=1",
        "https://user:pw@fvs.example.lt/FvsServicePure.svc",
        "https://fvs.example.lt/../FvsServicePure.svc",
    ],
)
def test_bad_addresses_are_refused(url: str) -> None:
    with pytest.raises(ExtensionError) as exc:
        base_url_of(url)
    assert exc.value.code == errors.INVALID_CONNECTION


def test_good_address_is_normalised() -> None:
    assert base_url_of("https://FVS.example.lt:8443/ws/FvsServicePure.svc/") == (
        "https://fvs.example.lt:8443/ws/FvsServicePure.svc"
    )


def _payload(config: dict, secrets: dict) -> dict:
    return {
        "action": {
            "target": {
                "connection": {
                    "connection_id": "c1",
                    "connection_type_id": "finvalda",
                    "config": config,
                    "secrets": secrets,
                }
            }
        }
    }


def test_connection_rules() -> None:
    good = {"base_url": BASE_URL, "user_name": "WEB"}
    conn = connection_from_payload(_payload(good, {"password": PASSWORD}), connection_ref="c1")
    assert conn.language == "lt" and PASSWORD not in repr(conn)
    for config, secrets in (
        ({"base_url": BASE_URL}, {"password": PASSWORD}),
        (good, {}),
        (good, {"password": "bad\r\nX-Injected: 1"}),
        ({**good, "user_name": "WEB\nX"}, {"password": PASSWORD}),
        (good, {"password": PASSWORD, "api_key": "x"}),
        ({**good, "language": "de"}, {"password": PASSWORD}),
    ):
        with pytest.raises(ExtensionError):
            connection_from_payload(_payload(config, secrets), connection_ref="c1")
