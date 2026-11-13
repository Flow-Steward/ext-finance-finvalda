"""The review of 0.1.3 (R1-R8): unreadable result codes, old titles in texts, debt
totals, a broken file in a listing, secondary client lookups, the code rule,
operation identities, input numbers."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from support import BUNDLE, OK, FakeFinvalda, Files, items, run

from runtime.errors import ExtensionError
from runtime.inputs import decimal_input

ATTACH = {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"}


# -- R1: an unreadable result code is never a refusal ------------------------------------


@pytest.mark.parametrize("code", ["9" * 30, "0.5", "١", "x"])
def test_unreadable_code_after_a_write_is_unknown(code: str) -> None:
    fake = FakeFinvalda(InsertNewItem={**OK, "nResult": code})
    response = run(
        fake,
        "insert_item",
        {"item_class": "Fvs.Klientas", "fields": {"sKodas": "K1", "sPavadinimas": "X"}},
    )
    assert response["error_code"] == "timeout_unknown"
    assert response["definitely_no_external_effect"] is False
    assert "-1" not in response["error"]


@pytest.mark.parametrize("code", ["9" * 30, "0.5"])
def test_unreadable_nested_code_after_a_write_is_unknown(code: str) -> None:
    fake = FakeFinvalda(AttachDocument={**OK, "result": {"errorCode": code, "errorText": ""}})
    response = run(
        fake, "attach_document", {"entity_type": 3, "entity_id1": "PARD", "file_name": "a.pdf"}
    )
    assert response["error_code"] == "timeout_unknown"
    assert response["definitely_no_external_effect"] is False


def test_unreadable_code_on_a_read_is_invalid() -> None:
    fake = FakeFinvalda(GetSandelius={**OK, "nResult": "0.5", "items": []})
    response = run(fake, "get_sandelius")
    assert response["error_code"] == "invalid_response"


# -- R2: no old title anywhere; every step a text points to exists ----------------------


GENERATED = [
    "contracts/operation_manifest.yaml",
    "contracts/step_ui_manifest.yaml",
    "ui/actions/actions.yaml",
    "ui/pages/finvalda-api.yaml",
    "ui/pages/coverage.yaml",
    "ui/pages/setup-guide.yaml",
    "ui/pages/connection.yaml",
    "README.md",
    "docs/workflow-actions.md",
    "extension.yaml",
]


@pytest.mark.parametrize("relative", GENERATED)
def test_no_old_step_titles(relative: str) -> None:
    text = (Path(BUNDLE) / relative).read_text("utf-8")
    for old in ("Advanced ·", "List ·", "Operations ·", "Advanced \\u00b7"):
        assert old not in text, (relative, old)


def test_every_step_a_text_points_to_exists() -> None:
    manifest = yaml.safe_load(
        (Path(BUNDLE) / "contracts/operation_manifest.yaml").read_text("utf-8")
    )
    titles = {op["display_name"] for op in manifest["operations"]}
    for relative in GENERATED:
        text = (Path(BUNDLE) / relative).read_text("utf-8")
        for named in re.findall(r"\bthe ([A-Z][A-Za-z ,'-]+?) step\b", text):
            assert named in titles, (relative, named)
        for named in re.findall(r"\*\*([^*]+)\*\* \(`([a-z0-9_]+)`\)", text):
            assert named[0] in titles, (relative, named)


# -- R3: debt totals keep every digit and never fail --------------------------------------


def test_debt_totals_keep_every_cent() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K"}),
        GetNeapmoketiKliDok={
            **OK,
            "nResult": 0,
            "items": [
                {"dokumentas": "S1", "likusi_skola": "123456789012345678.01"},
                {"dokumentas": "S2", "likusi_skola": "0.01"},
            ],
        },
    )
    result = run(fake, "client_debt", {"client_code": "K1"})["result"]
    assert result["unpaid_total"] == "123456789012345678.02"


def test_a_debt_beyond_reason_is_warned_not_summed() -> None:
    fake = FakeFinvalda(
        GetDescriptions=items({"code": "K1", "title": "K"}),
        GetNeapmoketiKliDok={
            **OK,
            "nResult": 0,
            "items": [
                {"dokumentas": "S1", "likusi_skola": "1E+5000000"},
                {"dokumentas": "S2", "likusi_skola": "5"},
            ],
        },
    )
    result = run(fake, "client_debt", {"client_code": "K1"})["result"]
    assert result["count"] == 2 and result["unpaid_total"] == "5.00"
    assert result["warnings"] and "too large" in result["warnings"][0]


# -- R4: one broken file does not stop the listing -----------------------------------------


def _attached(*docs: dict[str, Any]) -> dict[str, Any]:
    return {**OK, "result": {"errorCode": 0, "enitityDocs": list(docs)}}


def test_a_broken_file_is_listed_without_a_size() -> None:
    fake = FakeFinvalda(
        GetAttachedDocument=_attached(
            {"name": "bad.pdf", "data": "zz"}, {"name": "a.pdf", "data": "2550"}
        )
    )
    result = run(fake, "attached_documents", ATTACH, files=Files())["result"]
    assert result["files"] == [
        {"name": "bad.pdf", "size_bytes": None},
        {"name": "a.pdf", "size_bytes": 2},
    ]
    assert result["file_name"] == "a.pdf" and result["warnings"]


def test_the_requested_broken_file_is_an_error() -> None:
    fake = FakeFinvalda(GetAttachedDocument=_attached({"name": "a.pdf", "data": "zz"}))
    response = run(fake, "attached_documents", ATTACH, files=Files())
    assert response["error_code"] == "invalid_response"


# -- R5/R6: secondary client values are matched exactly; codes strictly ------------------


@pytest.mark.parametrize(
    "inputs, row",
    [
        ({"email": "a@b.lt"}, {"code": "K9", "email": "xa@b.lt"}),
        ({"company_code": "300"}, {"code": "K9", "company_code": "3001"}),
        ({"vat_code": "LT100"}, {"code": "K9", "vat_payer_code": "LT1001"}),
    ],
)
def test_loose_secondary_matches_let_the_client_be_created(
    inputs: dict[str, Any], row: dict[str, Any]
) -> None:
    fake = FakeFinvalda(
        GetDescriptions=[items(), items(row), items({"code": "NEW", "title": "New"})],
        InsertNewItem={**OK, "nResult": 0},
    )
    result = run(fake, "find_or_create_client", {"code": "NEW", "name": "New", **inputs})["result"]
    assert result["created"] is True
    assert any("taken as no match" in w for w in result["warnings"])


def test_client_code_lookup_stays_strict() -> None:
    fake = FakeFinvalda(GetDescriptions=items({"code": "K10"}), InsertNewItem={**OK, "nResult": 0})
    assert (
        run(fake, "find_or_create_client", {"code": "K1", "name": "X"})["error_code"]
        == "invalid_response"
    )
    fake = FakeFinvalda(GetDescriptions=items({"code": "K10"}))
    assert run(fake, "client_debt", {"client_code": "K1"})["error_code"] == "invalid_response"


def test_client_by_company_code_for_an_invoice_takes_the_exact_one() -> None:
    from runtime.everyday import common

    calls: list[Any] = []

    def describe(
        context: Any, kind: str, conditions: Any, columns: Any, **kw: Any
    ) -> list[dict[str, Any]]:
        calls.append(conditions)
        return [{"code": "K9", "company_code": "3001"}, {"code": "K1", "company_code": "300"}]

    original = common.describe
    common.describe = describe  # type: ignore[assignment]
    try:
        assert common.client_code_of(None, {"client_company_code": "300"}) == "K1"
    finally:
        common.describe = original  # type: ignore[assignment]


# -- R7: one path for an operation's identity ------------------------------------------------


def test_identity_of_an_operation() -> None:
    from runtime.everyday.payments import _identity

    assert _identity({"journal": "IPL", "op_number": 5}) == "IPL #5"
    assert _identity({"journal": "IPL", "op_number": "12/A"}) == "IPL #12/A"
    assert _identity({"journal": None, "op_number": None}) == "? #?"
    assert _identity({"journal": "IPL", "op_number": "X" * 50}) == "IPL #" + "X" * 19 + "…"


def test_payment_conflict_goes_through_the_identity() -> None:
    fake = FakeFinvalda(
        GetOperations=items(
            {"order_number": "B", "client": "K1", "journal": "IPL", "op_number": "12/A"}
        )
    )
    response = run(
        fake,
        "register_payment",
        {"direction": "incoming", "client_code": "K1", "document": "B", "amount": "1"},
    )
    assert "IPL #12/A" in response["error"]


# -- R8: input numbers are normalised before their places are counted ----------------------


def test_trailing_zeros_and_float_noise() -> None:
    assert decimal_input("x", Decimal("1.000000000")) == 1
    assert decimal_input("x", "1.000000000") == 1
    with pytest.raises(ExtensionError, match="decimal places"):
        decimal_input("x", 0.1 + 0.2)
    with pytest.raises(ExtensionError, match="decimal places"):
        decimal_input("x", "0.30000000000000004")


# -- Publication standard: bundled branding provenance -------------------------------------


def test_branding_records_the_icon_hash() -> None:
    import hashlib

    branding = (Path(BUNDLE) / "BRANDING.md").read_text("utf-8")
    icon = hashlib.sha256((Path(BUNDLE) / "icon.png").read_bytes()).hexdigest()
    assert icon in branding
    assert (
        "https://finvalda.lt/wp-content/themes/finvalda/assets/fav/android-icon-192x192.png"
        in branding
    )
    assert "a9fbaf1327ac9ebfa08ea010f55189747186b76c161ec6b6e7d45680950ff111" in branding
