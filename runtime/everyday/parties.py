"""Find or create client, product, service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import errors
from ..errors import invalid
from ..fields import field
from ..inputs import flag_input
from .common import (
    _EMAIL_RE,
    CLIENT_COLUMNS,
    PRODUCT_COLUMNS,
    SERVICE_COLUMNS,
    Progress,
    _best_effort,
    _same,
    code_input,
    describe,
    insert_item,
    money,
    text,
)

CLIENT_INPUTS = {
    "code",
    "company_code",
    "vat_code",
    "email",
    "name",
    "address",
    "city",
    "country_code",
    "phone",
    "client_type",
    "is_person",
    "item_parameter",
}


def _client_values(inputs: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "code": code_input("code", inputs, 15),
        "company_code": text("company_code", inputs, 13),
        "vat_code": text("vat_code", inputs, 30),
        "email": text("email", inputs, 30),
        "name": text("name", inputs, 100),
        "address": text("address", inputs, 90),
        "city": text("city", inputs, 20),
        "country_code": text("country_code", inputs, 3).upper(),
        "phone": text("phone", inputs, 15),
        "client_type": text("client_type", inputs, 10),
        "is_person": flag_input("is_person", inputs.get("is_person")),
    }
    if values["email"] and not _EMAIL_RE.fullmatch(str(values["email"])):
        raise invalid("email is not an e-mail address")
    return values


def client_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    values = _client_values(inputs)
    lookups = [
        ("code", "Codes", [values["code"]] if values["code"] else None, "code"),
        ("company_code", "CompanyCode", values["company_code"] or None, "company_code"),
        ("vat_code", "VatCode", values["vat_code"] or None, "vat_payer_code"),
        ("email", "Email", values["email"] or None, "email"),
    ]
    for matched_by, key, value, column in lookups:
        if value is None:
            continue
        wanted = value[0] if isinstance(value, list) else value
        rows = [
            r
            for r in describe(context, "Clients", {key: value}, CLIENT_COLUMNS)
            if _same(field(r, column), wanted)
        ]
        if len(rows) > 1:
            raise errors.no_effect(
                errors.CONFLICT,
                f"{len(rows)} Finvalda clients match {matched_by} {wanted}; give the client's code",
            )
        if rows:
            record = rows[0]
            for other, column_name in (
                ("company_code", "company_code"),
                ("vat_code", "vat_payer_code"),
            ):
                found = str(field(record, column_name) or "").strip()
                if values[other] and found and not _same(found, values[other]):
                    raise errors.no_effect(
                        errors.CONFLICT,
                        f"Client {field(record, 'code')} has {other} {found}, not {values[other]}",
                    )
            return {
                "client_code": str(field(record, "code")),
                "created": False,
                "matched_by": matched_by,
                "record": dict(record),
            }
    if not values["code"] or not values["name"]:
        raise invalid("No client matches; give code and name to create it")
    fields: dict[str, Any] = {"sKodas": values["code"], "sPavadinimas": values["name"]}
    for key, wire in (
        ("company_code", "sImKodas"),
        ("vat_code", "sPvmMoketojoKod"),
        ("email", "sEMail"),
        ("address", "sAdresas"),
        ("city", "sMiestas"),
        ("country_code", "sValstybeKodas"),
        ("phone", "sTelefonas"),
        ("client_type", "sRusis"),
    ):
        if values[key]:
            fields[wire] = values[key]
    if values["is_person"]:
        fields["nFizinisAsmuo"] = 1
    insert_item(context, "Fvs.Klientas", fields, inputs)
    progress.add(f"client {values['code']}")
    record = _best_effort(
        lambda: describe(context, "Clients", {"Codes": [values["code"]]}, CLIENT_COLUMNS)
    )
    return {
        "client_code": values["code"],
        "created": True,
        "matched_by": None,
        "record": dict(record[0]) if record else None,
    }


PRODUCT_INPUTS = {
    "code",
    "barcode",
    "name",
    "unit",
    "vat_code",
    "account_link",
    "product_type",
    "price",
    "purchase_price",
    "item_parameter",
}


def barcode_owners(rows: list[dict[str, Any]], barcode: str) -> list[str]:
    """The product codes of the rows that carry this barcode.

    The manual (4.28) does not name BarCodes' columns, so a row is used only when
    it shows both a product code and the barcode; anything else is an error rather
    than a reason to create a second product.
    """
    owners: set[str] = set()
    for row in rows:
        code = str(field(row, "code") or field(row, "product") or "").strip()
        values = {
            str(v).strip()
            for k, v in row.items()
            if "bar" in str(k).lower() and isinstance(v, (str, int))
        }
        if not code or not values:
            raise errors.no_effect(
                errors.CONFLICT,
                f"Finvalda found barcode {barcode} but its answer does not say which product has "
                "it; give the product code",
            )
        if barcode in values:
            owners.add(code)
    return sorted(owners)


def product_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    code = code_input("code", inputs, 20)
    barcode = text("barcode", inputs, 13)
    if code:
        rows = [
            r
            for r in describe(context, "Products", {"Codes": [code]}, PRODUCT_COLUMNS)
            if _same(field(r, "product"), code)
        ]
        if rows:
            return {
                "product_code": code,
                "created": False,
                "matched_by": "code",
                "record": dict(rows[0]),
            }
    if barcode:
        hits = describe(context, "BarCodes", {"BarCodes": [barcode]}, None)
        codes = barcode_owners(hits, barcode)
        if len(codes) > 1:
            raise errors.no_effect(errors.CONFLICT, f"{len(codes)} products have barcode {barcode}")
        if codes:
            if code and not _same(codes[0], code):
                raise errors.no_effect(
                    errors.CONFLICT, f"Barcode {barcode} belongs to product {codes[0]}, not {code}"
                )
            rows = describe(context, "Products", {"Codes": codes}, PRODUCT_COLUMNS)
            return {
                "product_code": codes[0],
                "created": False,
                "matched_by": "barcode",
                "record": dict(rows[0]) if rows else None,
            }
    name = text("name", inputs, 50)
    if not code or not name:
        raise invalid("No product matches; give code and name to create it")
    fields: dict[str, Any] = {"sKodas": code, "sPavadinimas": name}
    for key, wire, limit in (
        ("barcode", "sBarKodas", 13),
        ("unit", "sMatavimoVnt", 10),
        ("vat_code", "sMokestis", 10),
        ("account_link", "sRysysSuSask", 10),
        ("product_type", "sRusis", 13),
    ):
        value = text(key, inputs, limit)
        if value:
            fields[wire] = value
    if inputs.get("price") is not None:
        fields["dKaina1"] = str(money("price", inputs["price"]))
    if inputs.get("purchase_price") is not None:
        fields["dPirkimoKaina"] = str(money("purchase_price", inputs["purchase_price"]))
    insert_item(context, "Fvs.Preke", fields, inputs)
    progress.add(f"product {code}")
    rows = _best_effort(lambda: describe(context, "Products", {"Codes": [code]}, PRODUCT_COLUMNS))
    return {
        "product_code": code,
        "created": True,
        "matched_by": None,
        "record": dict(rows[0]) if rows else None,
    }


SERVICE_INPUTS = {
    "code",
    "name",
    "unit",
    "vat_code",
    "account_link",
    "price",
    "purchase_price",
    "item_parameter",
}


def service_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    code = code_input("code", inputs, 13, required=True)
    rows = [
        r
        for r in describe(context, "Services", {"Codes": [code]}, SERVICE_COLUMNS)
        if _same(field(r, "code"), code)
    ]
    if rows:
        return {"service_code": code, "created": False, "record": dict(rows[0])}
    name = text("name", inputs, 50)
    if not name:
        raise invalid("No service has this code; give name to create it")
    fields: dict[str, Any] = {"sKodas": code, "sPavadinimas": name}
    for key, wire in (
        ("unit", "sMatVienetas"),
        ("vat_code", "sMokestis"),
        ("account_link", "sRysysSuSask"),
    ):
        value = text(key, inputs, 10)
        if value:
            fields[wire] = value
    if inputs.get("price") is not None:
        fields["dPardavimoKaina"] = str(money("price", inputs["price"]))
    if inputs.get("purchase_price") is not None:
        fields["dPirkimoKaina"] = str(money("purchase_price", inputs["purchase_price"]))
    insert_item(context, "Fvs.Paslauga", fields, inputs)
    progress.add(f"service {code}")
    rows = _best_effort(lambda: describe(context, "Services", {"Codes": [code]}, SERVICE_COLUMNS))
    return {"service_code": code, "created": True, "record": dict(rows[0]) if rows else None}
