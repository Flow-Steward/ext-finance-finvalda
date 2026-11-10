"""Every operation of the extension: inputs, outputs, texts and how the runtime runs it.

Built from ``fvs_reference.json`` (extracted from Finvalda's manual and Postman
collection by ``extract_reference.py``) plus the tables below, which give each
documented method and parameter a readable name. ``build_contracts.py`` turns
the result into the contracts, the UI pages and ``runtime/registry.json``.

Outputs list exactly the keys the runtime returns; build_contracts adds the two
external-effect outputs to every write.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ADVANCED = "Advanced · "

CONNECTION = {"name": "connection_ref", "value_type": "connection_ref", "required": True}
DATE: dict[str, Any] = {"pattern": r"^\d{4}-\d{2}-\d{2}$", "maxLength": 10}
DATE_TIME: dict[str, Any] = {
    "pattern": r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,7})?)?)?$",
    "maxLength": 27,
}


@cache
def reference() -> dict[str, Any]:
    return json.loads((HERE / "fvs_reference.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Input and output helpers


def text(
    name: str, title: str, description: str, *, required: bool = False, **extra: Any
) -> dict[str, Any]:
    schema = {"type": "string", "title": title, "description": description, "maxLength": 200}
    schema.update(extra)
    return {"name": name, "value_type": "text", "required": required, "schema": schema}


def date(name: str, title: str, description: str, *, required: bool = False) -> dict[str, Any]:
    return text(name, title, description, required=required, **DATE)


def whole(
    name: str,
    title: str,
    description: str,
    *,
    required: bool = False,
    minimum: int = 0,
    maximum: int = 2_147_483_647,
) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "integer",
        "required": required,
        "schema": {
            "type": "integer",
            "minimum": minimum,
            "maximum": maximum,
            "title": title,
            "description": description,
        },
    }


def flag(name: str, title: str, description: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "boolean",
        "required": required,
        "schema": {"type": "boolean", "title": title, "description": description},
    }


def amount(name: str, title: str, description: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "text",
        "required": required,
        "schema": {"type": ["string", "number"], "title": title, "description": description},
    }


def choice(
    name: str,
    title: str,
    description: str,
    values: list[Any],
    *,
    required: bool = False,
    labels: dict[Any, str] | None = None,
) -> dict[str, Any]:
    kind = "integer" if all(isinstance(v, int) for v in values) else "text"
    schema: dict[str, Any] = {
        "type": "integer" if kind == "integer" else "string",
        "enum": values,
        "title": title,
        "description": description,
    }
    item = {"name": name, "value_type": kind, "required": required, "schema": schema}
    if labels:
        item["labels"] = labels
    return item


def json_input(
    name: str,
    title: str,
    description: str,
    *,
    kind: str = "object",
    required: bool = False,
    **schema: Any,
) -> dict[str, Any]:
    body = {"type": kind, "title": title, "description": description, **schema}
    return {
        "name": name,
        "value_type": "array" if kind == "array" else "object",
        "required": required,
        "schema": body,
    }


def file_input(name: str, *, required: bool) -> dict[str, Any]:
    return {"name": name, "value_type": "artifact_handle", "required": required}


def out(
    name: str, value_type: str, schema_type: Any = None, description: str = ""
) -> dict[str, Any]:
    if value_type == "artifact_handle":
        return {"name": name, "value_type": value_type}
    schema: dict[str, Any] = {"type": schema_type} if schema_type is not None else {}
    if description:
        schema["description"] = description
    return {"name": name, "value_type": value_type, "schema": schema}


def snake(name: str) -> str:
    name = name.replace(".", "_")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return re.sub(r"_+", "_", name).lower().strip("_")


def words(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


IMPORT_PARAMETER = text(
    "import_parameter",
    "Import parameter",
    "The Finvalda import parameter (sParametras) that sets the journal, operation type, series "
    "and accounts. Default: the connection's operation import parameter.",
    maxLength=100,
)
ITEM_PARAMETER = text(
    "item_parameter",
    "Item import parameter",
    "The Finvalda import parameter for new records (sFvsImportoParametras), which fills accounts, "
    "VAT and unit. Default: the connection's item import parameter.",
    maxLength=100,
)
JOURNAL = text(
    "journal",
    "Journal",
    "The operation's journal code, for example PARD.",
    required=True,
    maxLength=10,
)
NUMBER = whole(
    "op_number",
    "Operation number",
    "The operation's number in its journal.",
    required=True,
    minimum=1,
)
RECORDS = out(
    "records", "array", "array", "The rows Finvalda returned; numbers as exact decimal text."
)
RECORD = out("record", "object", ["object", "null"], "The first row, or null.")
COUNT = out("count", "integer", "integer", "How many rows were returned.")
RESPONSE = out("response", "object", ["object", "null"], "Finvalda's whole answer.")
OP_RESULT = [
    out("journal", "text", ["string", "null"], "The saved operation's journal."),
    out("op_number", "integer", ["integer", "null"], "Its number in the journal."),
    out("series", "text", ["string", "null"], "Its document series."),
    out("document", "text", ["string", "null"], "Its document number."),
    out("finvalda_result", "integer", ["integer", "null"], "Finvalda's result code (0 = saved)."),
]

#: Words shown for coded select values (build_contracts reads ``labels`` from inputs).
OPTION_LABELS: dict[str, dict[Any, str]] = {}

# ---------------------------------------------------------------------------
# Readable names of documented methods and their parameters

#: Method -> (title, one-line description). Every documented read method is listed.
LEGACY_READS: dict[str, tuple[str, str]] = {
    "GetEinamiejiLikuciai": ("Current stock", "Current stock of products by warehouse."),
    "GetEinamiejiLikuciaiExt": (
        "Current stock, extended",
        "Current stock with product type and tags.",
    ),
    "GetEinamiejiLikuciaiExtSuKainom": (
        "Current stock with prices",
        "Current stock with product type, tags and sales prices.",
    ),
    "GetEinamiejiLikuciaiGrp": (
        "Current stock by warehouse group",
        "Current stock summed over a warehouse group.",
    ),
    "GetKlientuRusisPozymius": ("Client types and tags", "Client types and the three client tags."),
    "GetKlientusSet": ("Clients", "Clients, by code or created/edited since a date."),
    "GetMatavimoVienetus": ("Units of measure", "Every unit of measure."),
    "GetNeapmoketiKliDok": (
        "Unpaid sales documents",
        "Unpaid sales documents of one or all clients.",
    ),
    "GetAtsiskaitymaiUzDok": (
        "Settlements for a document",
        "Payments and offsets settled against one operation.",
    ),
    "GetAtsiskaitymaiUzDokDet": (
        "Settlements for a document, detailed",
        "Settlement lines of one operation.",
    ),
    "GetAtsiskaitymaiUzDokDataNuoDet": (
        "Settlements for a document since a date",
        "Settlement lines of one operation created or edited since a date.",
    ),
    "GetAtsiskaitymaiUzDokDataNuoDetParam": (
        "Settlements with parameters",
        "Settlement lines selected by a parameter object.",
    ),
    "GetPaslaugosSet": ("Services", "Services, by code or created/edited since a date."),
    "GetPaslauguRusisPozymius": (
        "Service types and tags",
        "Service types and the three service tags.",
    ),
    "GetPrekesSet": ("Products", "Products, by code or created/edited since a date."),
    "GetPrekesSetExt": (
        "Products by type and tags",
        "Products filtered by type, tags, suppliers and objects.",
    ),
    "GetPrekiuRusisPozymius": ("Product types and tags", "Product types and the product tags."),
    "GetPrekiuRusiuGrupes": ("Product type groups", "Every product type group."),
    "GetPrekiuRusiuGrupesSudeti": (
        "Product type group members",
        "The product types in one product type group.",
    ),
    "GetSandelius": ("Warehouses", "Every warehouse."),
    "GetKliPrekPasNuolPapKain": (
        "Client discounts and prices (client × item)",
        "Discounts and special prices of clients for products and services.",
    ),
    "GetKliRusPrekPasNuolPapKain": (
        "Client type discounts and prices (client type × item)",
        "Discounts and special prices of client types for products and services.",
    ),
    "GetKliPrekPasRusNuolPapKain": (
        "Client discounts and prices (client × item type)",
        "Discounts and special prices of clients for product and service types.",
    ),
    "GetKliRusPrekPasRusNuolPapKain": (
        "Client type discounts and prices (client type × item type)",
        "Discounts and special prices of client types for product and service types.",
    ),
    "GetMokesciai": ("Taxes", "Every tax (VAT) code."),
    "GetKlientuPaslauguNuol": ("Client service discounts", "Service discounts of a client."),
    "GetKlientuPaslauguPapKainas": ("Client service prices", "Special service prices of a client."),
    "GetKlientuPaslauguRusNuol": (
        "Client service type discounts",
        "Service type discounts of a client.",
    ),
    "GetKlientuPaslauguRusPapKainas": (
        "Client service type prices",
        "Special service type prices of a client.",
    ),
    "GetKlientuPrekiuNuol": ("Client product discounts", "Product discounts of a client."),
    "GetKlientuPrekiuPapKainas": ("Client product prices", "Special product prices of a client."),
    "GetKlientuPrekiuRusNuol": (
        "Client product type discounts",
        "Product type discounts of a client.",
    ),
    "GetKlientuPrekiuRusPapKainas": (
        "Client product type prices",
        "Special product type prices of a client.",
    ),
    "GetKlientuRusPaslauguNuol": (
        "Client type service discounts",
        "Service discounts of a client type.",
    ),
    "GetKlientuRusPaslauguPapKainas": (
        "Client type service prices",
        "Special service prices of a client type.",
    ),
    "GetKlientuRusPaslauguRusNuol": (
        "Client type service type discounts",
        "Service type discounts of a client type.",
    ),
    "GetKlientuRusPaslauguRusPapKainas": (
        "Client type service type prices",
        "Special service type prices of a client type.",
    ),
    "GetKlientuRusPrekiuNuol": (
        "Client type product discounts",
        "Product discounts of a client type.",
    ),
    "GetKlientuRusPrekiuPapKainas": (
        "Client type product prices",
        "Special product prices of a client type.",
    ),
    "GetKlientuRusPrekiuRusNuol": (
        "Client type product type discounts",
        "Product type discounts of a client type.",
    ),
    "GetKlientuRusPrekiuRusPapKainas": (
        "Client type product type prices",
        "Special product type prices of a client type.",
    ),
    "GetObjektas1": ("Object (level 1)", "One level-1 object by code."),
    "GetObjektas2": ("Object (level 2)", "One level-2 object by code."),
    "GetObjektas3": ("Object (level 3)", "One level-3 object by code."),
    "GetObjektas4": ("Object (level 4)", "One level-4 object by code."),
    "GetObjektas5": ("Object (level 5)", "One level-5 object by code."),
    "GetObjektas6": ("Object (level 6)", "One level-6 object by code."),
    "GetKlientas": ("Client", "One client by code."),
    "GetKlientasEMail": ("Client by e-mail", "Clients with an e-mail address."),
    "GetKlientus": ("Clients created or edited since", "Clients created or edited since a date."),
    "GetPaslauga": ("Service", "One service by code."),
    "GetPaslaugos": (
        "Services created or edited since",
        "Services created or edited since a date.",
    ),
    "GetPreke": ("Product", "One product by code."),
    "GetPrekes": ("Products created or edited since", "Products created or edited since a date."),
    "GetPrekesSandelyje": ("Products in a warehouse", "Products and quantities in one warehouse."),
    "GetPrekesSandelyjeOrder": (
        "Products in a warehouse, sorted",
        "Products and quantities in one warehouse in a chosen order.",
    ),
    "GetPrekiuRusiesSudeti": ("Products of a type", "The products of one product type."),
    "GetPardPrekPerPerioda": (
        "Products sold in a period",
        "Quantities of products sold in a period.",
    ),
    "GetPaslauguRusiesSudeti": ("Services of a type", "The services of one service type."),
    "GetKlientusRusiesSudeti": ("Clients of a type", "The clients of one client type."),
    "GetAtsiskaitymoTerm": ("Payment terms", "Every payment term."),
    "GetObjektai1Set": ("Objects (level 1)", "Level-1 objects, by code or created/edited since."),
    "GetObjektai2Set": ("Objects (level 2)", "Level-2 objects, by code or created/edited since."),
    "GetObjektai3Set": ("Objects (level 3)", "Level-3 objects, by code or created/edited since."),
    "GetObjektai4Set": ("Objects (level 4)", "Level-4 objects, by code or created/edited since."),
    "GetObjektai5Set": ("Objects (level 5)", "Level-5 objects, by code or created/edited since."),
    "GetObjektai6Set": ("Objects (level 6)", "Level-6 objects, by code or created/edited since."),
    "GetUVMPardRBusena": (
        "Order management: reservation status",
        "The status of one sales reservation (order management module).",
    ),
    "GetUVMPardRAnuliuoti": (
        "Order management: cancelled reservations",
        "Cancelled sales reservations in a period (order management module).",
    ),
    "GetUVMPardRIvykdyti": (
        "Order management: fulfilled reservations",
        "Fulfilled sales reservations in a period (order management module).",
    ),
    "GetUVMPardRNeivykdyti": (
        "Order management: open reservations",
        "Open sales reservations in a period (order management module).",
    ),
    "GetKlientoSaskaitas": (
        "Client documents",
        "A client's invoices and payments, paid or unpaid, in a period.",
    ),
    "GetPrekesIstorija": ("Product history", "The movements of a product since a date."),
    "GetVeiklaPagalObjektus": (
        "Activity by objects",
        "Operation lines by client and objects in a period.",
    ),
}

#: Wire parameter -> (input name, title, description).
PARAMS: dict[str, tuple[str, str, str]] = {
    "sPrekesKodas": ("product_code", "Product code", "The product's code in Finvalda."),
    "sPreKod": ("product_code", "Product code", "The product's code in Finvalda."),
    "sPaslaugosKodas": ("service_code", "Service code", "The service's code in Finvalda."),
    "sPasKod": ("service_code", "Service code", "The service's code in Finvalda."),
    "sSandelioKodas": ("warehouse_code", "Warehouse", "The code of the warehouse."),
    "sSanKod": ("warehouse_code", "Warehouse", "The code of the warehouse."),
    "sSandKod": ("warehouse_code", "Warehouse", "The code of the warehouse."),
    "sSandelioGrupesKodas": ("warehouse_group", "Warehouse group", "The warehouse group code."),
    "tKoregavimoData": (
        "edited_since",
        "Edited since",
        "Only records edited on or after this date, YYYY-MM-DD.",
    ),
    "tSukurimoData": (
        "created_since",
        "Created since",
        "Only records created on or after this date, YYYY-MM-DD.",
    ),
    "bNuliniaiLikuciai": (
        "include_zero_stock",
        "Include zero stock",
        "Also return products whose stock is zero.",
    ),
    "sKliKod": ("client_code", "Client code", "The client's code in Finvalda."),
    "sKlientoKodas": ("client_code", "Client code", "The client's code in Finvalda."),
    "sKlientas": ("client_code", "Client code", "The client's code in Finvalda."),
    "sKlientImonesKodas": (
        "client_company_code",
        "Client company code",
        "The client's company code.",
    ),
    "sKlientuGrupe": ("client_group", "Client group", "The client group code."),
    "sKlientoRusKodas": ("client_type_code", "Client type", "The client type code."),
    "sEMail": ("email", "E-mail", "The e-mail address to look for."),
    "sSerija": ("series", "Series", "The operation's document series."),
    "sDokumentas": ("document", "Document", "The operation's document number."),
    "sZurnalas": ("journal", "Journal", "The operation's journal code."),
    "nNumeris": ("op_number", "Operation number", "The operation's number in its journal."),
    "nOperacijosID": ("operation_row_id", "Operation row id", "Finvalda's internal operation id."),
    "nOperacijosKlase": (
        "operation_class",
        "Operation class",
        "Finvalda's operation class number.",
    ),
    "sParam": (
        "params",
        "Parameters",
        "The method's parameter object, as the manual describes it.",
    ),
    "sRusis": ("type_code", "Type", "The product type code."),
    "sRusiesKodas": ("type_code", "Type", "The code of the type."),
    "sPrekiuRusiuGrupe": (
        "product_type_group",
        "Product type group",
        "The product type group code.",
    ),
    "sRysysSuSask": ("account_link", "Account link", "The product's link to accounts."),
    "sObj1Kod": ("object_code", "Object code", "The code of the object."),
    "sObj2Kod": ("object_code", "Object code", "The code of the object."),
    "sObj3Kod": ("object_code", "Object code", "The code of the object."),
    "sObj4Kod": ("object_code", "Object code", "The code of the object."),
    "sObj5Kod": ("object_code", "Object code", "The code of the object."),
    "sObj6Kod": ("object_code", "Object code", "The code of the object."),
    "nOrder": ("order", "Order", "The sort order number the manual documents for this method."),
    "tDataNuo": ("date_from", "From", "The first date, YYYY-MM-DD."),
    "tDataIki": ("date_to", "To", "The last date, YYYY-MM-DD."),
    "tNuo": ("date_from", "From", "The first date, YYYY-MM-DD."),
    "tIki": ("date_to", "To", "The last date, YYYY-MM-DD."),
    "sPardZurKodas": ("sales_journal", "Sales journal", "Only sales of this journal."),
    "bItrauktiVisasPrekes": (
        "include_all_products",
        "Include all products",
        "Also list products that were not sold.",
    ),
    "sZurnaluGrupe": ("journal_group", "Journal group", "The journal group code."),
    "nSkolosTipas": (
        "debt_type",
        "Debt type",
        "2 = only unsettled, 1 = only settled, anything else = all.",
    ),
    "nOperacijosTipas": (
        "operation_type",
        "Operation type",
        "0 inflows, 1 disbursements, 2 purchases, 3 sales, 4 purchase returns, 5 sales returns.",
    ),
    "tDokumentoDataNuo": (
        "document_date_from",
        "Document date from",
        "The first document date, YYYY-MM-DD.",
    ),
    "tDokumentoDataIki": (
        "document_date_to",
        "Document date to",
        "The last document date, YYYY-MM-DD.",
    ),
}
for _n in range(1, 7):
    PARAMS[f"sPozymis{_n}"] = (f"tag{_n}", f"Tag {_n}", f"The product's tag {_n} code.")
    PARAMS[f"sObj{_n}"] = (f"object{_n}", f"Object {_n}", f"The level-{_n} object code.")
for _n in range(1, 4):
    PARAMS[f"sTiekejas{_n}"] = (
        f"supplier{_n}",
        f"Supplier {_n}",
        f"The product's supplier {_n} code.",
    )

#: Parameter names a published client reports the live FvsServicePure interface reads
#: instead of the manual's; both are sent (WCF ignores a query parameter it does not know).
WIRE_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("GetPrekesSandelyje", "sSanKod"): ("sSandKod",),
    ("GetPrekesSandelyjeOrder", "sSanKod"): ("sSandKod",),
    ("GetUVMPardRAnuliuoti", "tNuo"): ("tDataNuo",),
    ("GetUVMPardRAnuliuoti", "tIki"): ("tDataIki",),
    ("GetUVMPardRIvykdyti", "tNuo"): ("tDataNuo",),
    ("GetUVMPardRIvykdyti", "tIki"): ("tDataIki",),
    ("GetUVMPardRNeivykdyti", "tNuo"): ("tDataNuo",),
    ("GetUVMPardRNeivykdyti", "tIki"): ("tDataIki",),
}

#: The manual's signature of GetAtsiskaitymaiUzDokDataNuoDet marks its two dates
#: "out"; its name ("…DataNuo…", from a date) and its text make them inputs.
PARAM_OVERRIDES: dict[str, list[str]] = {
    "GetAtsiskaitymaiUzDokDataNuoDet": [
        "sSerija",
        "sDokumentas",
        "sZurnalas",
        "nNumeris",
        "nOperacijosID",
        "nOperacijosKlase",
        "tSukurimoData",
        "tKoregavimoData",
    ],
}

#: Methods documented with an object parameter (``sParam``) rather than scalars.
JSON_PARAM_METHODS = frozenset({"GetAtsiskaitymaiUzDokDataNuoDetParam", "GetVeiklaPagalObjektus"})


def param_kind(wire: str) -> str:
    """Finvalda's names carry the type: s text, t date, n whole number, b flag, d number."""
    if wire == "sParam":
        return "json"
    return {"s": "text", "t": "date", "n": "integer", "b": "boolean", "d": "number"}[wire[0]]


def param_input(method: str, wire: str) -> dict[str, Any]:
    name, title, description = PARAMS[wire]
    kind = param_kind(wire)
    if kind == "json":
        return json_input(name, title, description, required=True)
    if kind == "date":
        return date(name, title, description)
    if kind == "integer":
        return whole(name, title, description, minimum=-2_147_483_648)
    if kind == "boolean":
        return flag(name, title, description)
    return text(name, title, description, maxLength=100)


# ---------------------------------------------------------------------------
# Advanced: the documented methods one by one


def legacy_read_specs() -> list[dict[str, Any]]:
    methods = reference()["manual"]["methods"]
    specs = []
    for method, (title, description) in LEGACY_READS.items():
        documented = methods[method]
        wires = PARAM_OVERRIDES.get(method) or [
            w for w in documented["params"] if w != "writeSchema"
        ]
        inputs = [CONNECTION] + [param_input(method, w) for w in wires]
        names = [i["name"] for i in inputs]
        if len(names) != len(set(names)):
            raise SystemExit(f"{method}: two parameters map to the same input name")
        fields = [f["name"] for f in documented["result_fields"]]
        detail = f" Columns: {', '.join(fields)}." if fields else ""
        specs.append(
            {
                "operation_id": snake(method),
                "display_name": f"{ADVANCED}{title}",
                "kind": "source",
                "calls": [f"GET {method}"],
                "description": f"{description} Finvalda method {method} (manual section "
                f"{documented['section']}).{detail}",
                "inputs": inputs,
                "outputs": [RECORDS, RECORD, COUNT, RESPONSE],
                "runtime": {
                    "runner": "method_read",
                    "method": method,
                    "verb": "GET",
                    "params": [
                        [
                            param_input(method, w)["name"],
                            w,
                            param_kind(w),
                            list(WIRE_ALIASES.get((method, w), ())),
                        ]
                        for w in wires
                    ],
                },
            }
        )
    return specs


#: GetDescriptions types: filters and columns from the manual (section 4) and the
#: Postman collection; the types only Postman shows are marked so.
def description_types() -> dict[str, dict[str, Any]]:
    ref = reference()
    manual = ref["manual"]["descriptions"]
    postman = ref["postman"]["descriptions"]
    out: dict[str, dict[str, Any]] = {}
    for kind in sorted(set(manual) | set(postman)):
        filters: dict[str, list[str]] = {}
        for key, names in (manual.get(kind, {}).get("filters") or {}).items():
            filters[key] = list(names)
        pm = postman.get(kind) or {}
        if pm.get("filter_key"):
            target = filters.setdefault(pm["filter_key"], [])
            for name in pm["filter"]:
                if name != "columns" and name not in target:
                    target.append(name)
        columns = list(manual.get(kind, {}).get("columns") or [])
        for column in pm.get("columns") or []:
            if column not in columns:
                columns.append(column)
        out[kind] = {
            "filters": filters,
            "columns": columns,
            "source": "manual" if kind in manual else "postman",
        }
    return out


DESCRIPTION_TITLES: dict[str, str] = {
    "Accounts": "Chart of accounts",
    "Address": "Client addresses",
    "BarCodes": "Product barcodes",
    "CalendarEvents": "Calendar events",
    "ClientGroups": "Client groups",
    "ClientIndividualCards": "Client individual cards",
    "Clients": "Clients",
    "CompanyWorkStart": "Company start date",
    "CountClients": "Number of clients",
    "CountSales": "Sales totals",
    "CurrencyRates": "Currency rates",
    "CurrentStock": "Current stock",
    "CurrentStockDet": "Current stock by receipt",
    "DocumentSeries": "Document series",
    "FixedAsset": "Fixed assets",
    "InvoiceList": "Invoice forms",
    "LogbookGroups": "Journal groups",
    "OpTypeGroups": "Operation type groups",
    "OperationStatuses": "Operation statuses",
    "PartnerProducts": "Partner product catalogues",
    "Prices": "Client prices",
    "PricesByClientAndItemTypes": "Prices by client type and item type",
    "PricesByClientType": "Prices by client type",
    "PricesByItemType": "Prices by item type",
    "ProductionItem": "Production items",
    "Products": "Products",
    "ReportList": "Reports",
    "Services": "Services",
    "StockOnDate": "Stock on a date",
    "TypesAndTags": "Types and tags",
    "Vehicles": "Vehicles",
    "WarehouseGroups": "Warehouse groups",
}


def _filter_help(filters: dict[str, list[str]]) -> str:
    if not filters:
        return "This list takes no filter."
    if len(filters) == 1:
        ((key, names),) = filters.items()
        return f"Conditions as an object with any of: {', '.join(names)}. Lists (Codes) are arrays."
    parts = [f"{key}: {{{', '.join(names)}}}" for key, names in filters.items()]
    return "One object per filter group — " + "; ".join(parts) + "."


def description_specs() -> list[dict[str, Any]]:
    specs = []
    for kind, info in description_types().items():
        title = DESCRIPTION_TITLES[kind]
        columns = info["columns"]
        inputs: list[dict[str, Any]] = [CONNECTION]
        if info["filters"]:
            inputs.append(
                json_input(
                    "filter",
                    "Filter",
                    _filter_help(info["filters"])[:400],
                )
            )
        inputs.append(
            json_input(
                "columns",
                "Columns",
                "The columns to return; default all."
                + (
                    f" Documented: {', '.join(columns[:40])}{'…' if len(columns) > 40 else ''}."
                    if columns
                    else ""
                ),
                kind="array",
                maxItems=200,
                items={"type": "string"},
            )
        )
        inputs += [
            whole(
                "page", "Page", "The page to read, from 1. Default 1.", minimum=1, maximum=1_000_000
            ),
            whole(
                "limit",
                "Rows per page",
                "Rows per page, 1 to 1000. Default 500.",
                minimum=1,
                maximum=1000,
            ),
            flag("fetch_all_pages", "Read every page", "Read page after page up to max_pages."),
            whole(
                "max_pages",
                "Max pages",
                "With fetch_all_pages: at most this many pages, 1 to 100.",
                minimum=1,
                maximum=100,
            ),
        ]
        source = "manual section 4" if info["source"] == "manual" else "the Postman collection"
        specs.append(
            {
                "operation_id": f"descriptions_{snake(kind)}",
                "display_name": f"{ADVANCED}List · {title}",
                "kind": "source",
                "calls": [f"POST GetDescriptions type={kind}"],
                "description": f"{title}, read with Finvalda's GetDescriptions method, type {kind} "
                f"(documented in {source}). Pages of rows; columns can be chosen.",
                "inputs": inputs,
                "outputs": [
                    RECORDS,
                    RECORD,
                    COUNT,
                    out("page", "integer", ["integer", "null"], "The last page read."),
                    out(
                        "next_page",
                        "integer",
                        ["integer", "null"],
                        "The page to read next, or null.",
                    ),
                    out("has_more", "boolean", "boolean", "True when more rows may follow."),
                    out("pages_fetched", "integer", "integer", "How many pages were read."),
                ],
                "runtime": {
                    "runner": "descriptions",
                    "method": "GetDescriptions",
                    "verb": "POST",
                    "type": kind,
                    "filters": info["filters"],
                    "columns": columns,
                },
            }
        )
    return specs


OPERATION_TITLES: dict[str, str] = {
    "Sales": "Sales",
    "SalesDet": "Sales lines",
    "SalesReturns": "Sales returns",
    "SalesReturnsDet": "Sales return lines",
    "Purchases": "Purchases",
    "PurchasesDet": "Purchase lines",
    "PurchasesReturns": "Purchase returns",
    "PurchasesReturnsDet": "Purchase return lines",
    "GenLeadger": "Other non-analytical operations",
    "GenLeadgerDet": "Other non-analytical operation lines",
    "Capitalization": "Receipts (capitalization)",
    "CapitalizationDet": "Receipt lines",
    "WriteOff": "Write-offs",
    "WriteOffDet": "Write-off lines",
    "Inflows": "Inflows (incoming payments)",
    "InflowsDet": "Inflow lines",
    "Disbursement": "Disbursements (outgoing payments)",
    "DisbursementDet": "Disbursement lines",
    "ClearingOff": "Offsets",
    "ClearingOffDet": "Offset lines",
    "SalesReservations": "Sales reservations",
    "SalesReservationsDet": "Sales reservation lines",
    "PurchaseOrders": "Purchase orders",
    "PurchaseOrdersDet": "Purchase order lines",
    "InternalTransactions": "Internal transfers",
    "InternalTransactionsDet": "Internal transfer lines",
    "Production": "Production",
}

HEADER_COLUMNS_NOTE = "date_created, date_edited, op_number, journal, op_date, series, order_number, client, amount, …"


def operation_classes() -> dict[str, dict[str, Any]]:
    ref = reference()
    manual = ref["manual"]["get_operations"]
    postman = ref["postman"]["operations"]
    det_only = {"Warehouse", "GoodsCode", *(f"Object{n}" for n in range(1, 7))}
    out: dict[str, dict[str, Any]] = {}
    classes = list(manual["op_classes"]) + [c for c in postman if c not in manual["op_classes"]]
    for op_class in classes:
        detail = op_class.endswith("Det")
        filters = [
            f
            for f in manual["filters"]
            if (detail or f not in det_only)
            and (f != "AdvancePaymentSettled" or op_class == "Inflows")
            and (f not in {"ClientDeb", "ClientCred"} or op_class.startswith("ClearingOff"))
        ]
        for name in (postman.get(op_class) or {}).get("filter", []):
            if name not in filters:
                filters.append(name)
        if "NotEditedByUser" in filters and "NotChangedByUser" not in filters:
            filters.append("NotChangedByUser")
        pm = postman.get(op_class) or {}
        out[op_class] = {
            "filters": filters,
            "detail": detail,
            "columns": list(pm.get("columns") or []),
            "source": "manual" if op_class in manual["op_classes"] else "postman",
        }
    return out


def operation_read_specs() -> list[dict[str, Any]]:
    specs = []
    for op_class, info in operation_classes().items():
        title = OPERATION_TITLES[op_class]
        inputs: list[dict[str, Any]] = [
            CONNECTION,
            json_input(
                "filter",
                "Filter",
                f"Conditions as an object with any of: {', '.join(info['filters'])}. Dates are "
                "YYYY-MM-DD; Description fields accept * as a wildcard.",
            ),
            json_input(
                "columns",
                "Columns",
                f"The columns to return; default all (for example {HEADER_COLUMNS_NOTE}).",
                kind="array",
                maxItems=200,
                items={"type": "string"},
            ),
        ]
        if not info["detail"]:
            inputs += [
                flag("full_op", "With lines", "Return each operation with its lines."),
                json_input(
                    "columns_det",
                    "Line columns",
                    "With lines: the line columns to return, for example code, title, quantity, amount.",
                    kind="array",
                    maxItems=200,
                    items={"type": "string"},
                ),
            ]
        source = "manual section 3.77" if info["source"] == "manual" else "the Postman collection"
        specs.append(
            {
                "operation_id": f"operations_{snake(op_class)}",
                "display_name": f"{ADVANCED}Operations · {title}",
                "kind": "source",
                "calls": [f"POST GetOperations OpClass={op_class}"],
                "description": f"{title}, read with Finvalda's GetOperations method, class "
                f"{op_class} (documented in {source}). Give a filter — a date range, a journal, "
                "a client — to keep the answer small.",
                "inputs": inputs,
                "outputs": [RECORDS, RECORD, COUNT],
                "runtime": {
                    "runner": "operations",
                    "method": "GetOperations",
                    "verb": "POST",
                    "op_class": op_class,
                    "filters": info["filters"],
                    "detail": info["detail"],
                },
            }
        )
    return specs


# -- writes of records (aprašymai) -------------------------------------------


def item_classes() -> list[str]:
    classes = reference()["manual"]["classes"]
    return sorted(
        c for c in classes if c.startswith("Fvs.") and c not in {"Fvs.Prekes", "Fvs.Paslaugos"}
    )


#: Header class -> the line classes it takes (manual 3.70).
LINE_CLASSES: dict[str, list[str]] = {}
for _head in ("PardDok", "PardRezDok", "PardGrazDok", "UVMPardRezDok"):
    LINE_CLASSES[_head] = [
        "PardDokPrekeDetEil",
        "PardDokPaslaugaDetEil",
        "PardDokSaskaitaDetEil",
        "PardDokGaminysDetEil",
    ]
for _head in ("TrumpasPardDok", "TrumpasPardRezDok", "TrumpasPardGrazDok", "TrumpasUVMPardRDok"):
    LINE_CLASSES[_head] = ["PardDokPrekeDetEil", "PardDokPaslaugaDetEil"]
for _head in ("PirkDok", "PirkUzsDok", "PirkGrazDok", "UVMPirkUzsDok"):
    LINE_CLASSES[_head] = ["PirkDokPrekeDetEil", "PirkDokPaslaugaDetEil", "PirkDokSaskaitaDetEil"]
for _head in ("TrumpasPirkDok", "TrumpasPirkGrazDok", "TrumpasPirkUzsDok", "TrumpasUVMPirkUzsDok"):
    LINE_CLASSES[_head] = ["PirkDokPrekeDetEil", "PirkDokPaslaugaDetEil"]
LINE_CLASSES.update(
    {
        "IplDok": ["IplDokDetEil"],
        "IsmDok": ["IsmDokDetEil", "Mokejimas"],
        "VidPerkDok": ["VidPerkDokDetEil"],
        "NurasymasDok": ["NurasymasDokDetEil"],
        "PajamavimasDok": ["PajamavimasDokDetEil"],
        "UVMAnulDok": ["UVMAnulDokDetEil"],
        "GamybaDok": ["GamybaGDetEil", "GamybaZDetEil", "GamybaPDetEil"],
        "UzskaitaDok": ["UzskaitaDebitDetEil", "UzskaitaKreditDetEil"],
        "KtNeanalitDok": ["KtNeanalitDetEil"],
        "Inventorizacija": [],
    }
)
#: The manual's ItemClassName list writes "TrumpasUVMPardRezDok"; its field table
#: "TrumpasUVMPardRDok". Both are accepted and sent as given.
CLASS_ALIASES: dict[str, str] = {"TrumpasUVMPardRezDok": "TrumpasUVMPardRDok"}

UPDATE_CLASSES: dict[str, list[str]] = {
    "KoregPardDok": [
        "PardDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PardDokPrekeDetEil",
        "PardDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregPardRezDok": [
        "PardDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PardDokPrekeDetEil",
        "PardDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregPardGrazDok": [
        "PardDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PardDokPrekeDetEil",
        "PardDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregPirkDok": [
        "PirkDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PirkDokPrekeDetEil",
        "PirkDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregPirkUzsDok": [
        "PirkDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PirkDokPrekeDetEil",
        "PirkDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregPirkGrazDok": [
        "PirkDokHeadEil",
        "DelPrekeDetEil",
        "DelPaslaugaDetEil",
        "PirkDokPrekeDetEil",
        "PirkDokPaslaugaDetEil",
        "UpdPrekeDetEil",
    ],
    "KoregVidPerkDok": ["VidPerkDokHeadEil", "AddPrekeDetEil", "DelPrekeDetEil", "UpdPrekeDetEil"],
}
DELETE_CLASSES = [
    "DelPirkDok",
    "DelPirkUzsDok",
    "DelPirkGrazDok",
    "DelPardDok",
    "DelPardRezDok",
    "DelPardGrazDok",
    "DelVidPerkDok",
    "DelIplDok",
    "DelUVMPirkUzsDok",
    "DelGamybaDok",
]
#: Classes of the Postman collection that the manual has no field table for.
POSTMAN_ITEM_CLASSES = ["Fvs.KlientoIndividKort"]
#: EditItemProps groups (manual 3.68): the attributes come from their tables.
PROPS_CLASSES = ["Fvs.Prekes", "Fvs.Paslaugos", "Fvs.Klientas"]


def write_specs() -> list[dict[str, Any]]:
    insert_classes = sorted(set(LINE_CLASSES) | set(CLASS_ALIASES))
    items = item_classes() + POSTMAN_ITEM_CLASSES
    op_result = list(OP_RESULT)
    return [
        {
            "operation_id": "insert_item",
            "display_name": f"{ADVANCED}Create a record",
            "kind": "action",
            "calls": ["POST InsertNewItem"],
            "description": "Creates a client, product, service, address, object, bank, warehouse, "
            "payment term, type or tag (InsertNewItem, manual 3.66). The fields are the manual's "
            "names for the class (for example sKodas, sPavadinimas) and are checked against it "
            "before anything is sent.",
            "inputs": [
                CONNECTION,
                choice(
                    "item_class",
                    "Record class",
                    "The kind of record, for example Fvs.Klientas.",
                    items,
                    required=True,
                ),
                json_input(
                    "fields",
                    "Fields",
                    "The record's fields by the manual's names, for example "
                    '{"sKodas": "UAB1", "sPavadinimas": "UAB Pavyzdys"}.',
                    required=True,
                ),
                ITEM_PARAMETER,
            ],
            "outputs": [
                out("item_class", "text", "string"),
                out("code", "text", ["string", "null"], "The new record's code."),
                out("finvalda_result", "integer", ["integer", "null"]),
            ],
            "runtime": {
                "runner": "item_write",
                "method": "InsertNewItem",
                "verb": "POST",
                "classes": items,
            },
        },
        {
            "operation_id": "edit_item",
            "display_name": f"{ADVANCED}Update a record",
            "kind": "action",
            "calls": ["POST EditItem"],
            "description": "Changes fields of an existing client, product, service or other record "
            "(EditItem, manual 3.67). Only the fields given are sent.",
            "inputs": [
                CONNECTION,
                choice(
                    "item_class",
                    "Record class",
                    "The kind of record, for example Fvs.Preke.",
                    items,
                    required=True,
                ),
                text(
                    "item_code",
                    "Code",
                    "The record's code (not needed for Fvs.Adresas).",
                    maxLength=20,
                ),
                json_input(
                    "fields",
                    "Fields",
                    "The fields to change, by the manual's names.",
                    required=True,
                ),
                ITEM_PARAMETER,
            ],
            "outputs": [
                out("item_class", "text", "string"),
                out("code", "text", ["string", "null"]),
                out("finvalda_result", "integer", ["integer", "null"]),
            ],
            "runtime": {
                "runner": "item_write",
                "method": "EditItem",
                "verb": "POST",
                "classes": items,
            },
        },
        {
            "operation_id": "edit_item_props",
            "display_name": f"{ADVANCED}Update many records",
            "kind": "action",
            "calls": ["POST EditItemProps"],
            "description": "Sets the same values on many products, services or clients at once "
            "(EditItemProps, manual 3.68 — named EditItemsProps in its method list).",
            "inputs": [
                CONNECTION,
                json_input(
                    "changes",
                    "Changes",
                    'One entry per group: {"Fvs.Prekes": {"Kodas": ["P1", "P2"], "pardKaina1": 13.1}}. '
                    "Groups: Fvs.Prekes, Fvs.Paslaugos, Fvs.Klientas; values by the manual's names.",
                    required=True,
                ),
            ],
            "outputs": [out("finvalda_result", "integer", ["integer", "null"])],
            "runtime": {
                "runner": "props_write",
                "method": "EditItemProps",
                "verb": "POST",
                "classes": PROPS_CLASSES,
            },
        },
        {
            "operation_id": "delete_item",
            "display_name": f"{ADVANCED}Delete a record",
            "kind": "action",
            "calls": ["POST DeleteItem"],
            "description": "Deletes a client, product, service or other record by code (DeleteItem, "
            "from the Postman collection). Finvalda refuses records that are in use.",
            "inputs": [
                CONNECTION,
                choice(
                    "item_class",
                    "Record class",
                    "The kind of record, for example Fvs.Klientas.",
                    items,
                    required=True,
                ),
                text("item_code", "Code", "The code of the record.", required=True, maxLength=20),
            ],
            "outputs": [
                out("item_class", "text", "string"),
                out("code", "text", "string"),
                out("finvalda_result", "integer", ["integer", "null"]),
            ],
            "runtime": {
                "runner": "delete_item",
                "method": "DeleteItem",
                "verb": "POST",
                "classes": items,
            },
        },
        {
            "operation_id": "append_group",
            "display_name": f"{ADVANCED}Add a record to a group",
            "kind": "action",
            "calls": ["POST AppendGroup"],
            "description": "Adds a product, service, client, address, object or warehouse to a group "
            "(AppendGroup, manual 3.69).",
            "inputs": [
                CONNECTION,
                choice(
                    "item_class",
                    "Group class",
                    "The kind of group, for example Fvs.Preke for a product group.",
                    [
                        "Fvs.Preke",
                        "Fvs.Paslauga",
                        "Fvs.Klientas",
                        "Fvs.Adresas",
                        "Fvs.ObjektasI",
                        "Fvs.ObjektasII",
                        "Fvs.ObjektasIII",
                        "Fvs.ObjektasIV",
                        "Fvs.ObjektasV",
                        "Fvs.ObjektasVI",
                        "Fvs.Sandelis",
                    ],
                    required=True,
                ),
                text("group_code", "Group", "The code of the group.", required=True, maxLength=20),
                text("item_code", "Code", "The code of the record.", required=True, maxLength=20),
                text(
                    "item_code2",
                    "Client code",
                    "For an address group: the address's client code.",
                    maxLength=20,
                ),
            ],
            "outputs": [out("finvalda_result", "integer", ["integer", "null"])],
            "runtime": {"runner": "append_group", "method": "AppendGroup", "verb": "POST"},
        },
        {
            "operation_id": "insert_operation",
            "display_name": f"{ADVANCED}Create an operation",
            "kind": "action",
            "calls": ["POST InsertNewOperation"],
            "description": "Creates any operation the manual documents — sale, purchase, return, "
            "reservation, order, internal transfer, write-off, receipt, inflow, disbursement, "
            "production, offset, stocktaking or other — with its lines (InsertNewOperation, "
            "manual 3.70). Header and line fields are checked against the manual first. Finvalda "
            "refuses a document number already used, so a rerun cannot save it twice.",
            "inputs": [
                CONNECTION,
                choice(
                    "op_class",
                    "Operation class",
                    "The operation class, for example PardDok for a sale.",
                    insert_classes,
                    required=True,
                ),
                json_input(
                    "operation",
                    "Operation",
                    "Header fields by the manual's names, with lines as arrays under their class: "
                    '{"sKlientas": "K1", "tData": "2026-11-10", "sDokumentas": "S-1", "sValiuta": "EUR", '
                    '"PardDokPrekeDetEil": [{...}]}.',
                    required=True,
                ),
                IMPORT_PARAMETER,
            ],
            "outputs": op_result,
            "runtime": {
                "runner": "operation_write",
                "method": "InsertNewOperation",
                "verb": "POST",
                "classes": LINE_CLASSES,
                "aliases": CLASS_ALIASES,
            },
        },
        {
            "operation_id": "update_operation",
            "display_name": f"{ADVANCED}Change an operation",
            "kind": "action",
            "calls": ["POST UpdateOperation"],
            "description": "Changes a sale, purchase, reservation, return, order or internal transfer: "
            "header fields, lines removed, added or changed (UpdateOperation, manual 3.72).",
            "inputs": [
                CONNECTION,
                choice(
                    "op_class",
                    "Change class",
                    "The change class, for example KoregPardDok.",
                    sorted(UPDATE_CLASSES),
                    required=True,
                ),
                json_input(
                    "operation",
                    "Changes",
                    'sZurnalas and nNumeris of the operation, then the change blocks: {"sZurnalas": "PARD", '
                    '"nNumeris": 19, "PardDokHeadEil": {...}, "DelPrekeDetEil": [{...}], "PardDokPrekeDetEil": [{...}]}.',
                    required=True,
                ),
                IMPORT_PARAMETER,
            ],
            "outputs": op_result,
            "runtime": {
                "runner": "operation_write",
                "method": "UpdateOperation",
                "verb": "POST",
                "classes": UPDATE_CLASSES,
                "aliases": {},
            },
        },
        {
            "operation_id": "delete_operation",
            "display_name": f"{ADVANCED}Delete an operation",
            "kind": "action",
            "calls": ["POST DeleteOperation"],
            "description": "Deletes a purchase, sale, reservation, return, order, internal transfer, "
            "inflow or production operation by journal and number (DeleteOperation, manual 3.71). "
            "Finvalda refuses settled, locked or reversed operations.",
            "inputs": [
                CONNECTION,
                choice(
                    "op_class",
                    "Delete class",
                    "The class, for example DelPardDok for a sale.",
                    DELETE_CLASSES,
                    required=True,
                ),
                JOURNAL,
                NUMBER,
                IMPORT_PARAMETER,
            ],
            "outputs": op_result,
            "runtime": {"runner": "delete_operation", "method": "DeleteOperation", "verb": "POST"},
        },
        lock_spec(
            "lock_operation",
            "LockOperation",
            "Lock an operation",
            "Locks an operation so it cannot be changed (LockOperation, manual 3.73).",
        ),
        lock_spec(
            "unlock_operation",
            "UnLockOperation",
            "Unlock an operation",
            "Unlocks an operation (UnLockOperation, manual 3.73).",
        ),
        {
            "operation_id": "is_operation_locked",
            "display_name": f"{ADVANCED}Is an operation locked",
            "kind": "source",
            "calls": ["POST IsOperationLocked"],
            "description": "Whether an operation is locked: 0 not locked, 1 locked by an employee, 2 "
            "locked by the administrator (IsOperationLocked, manual 3.74).",
            "inputs": [CONNECTION, JOURNAL, NUMBER],
            "outputs": [
                out("locked", "boolean", ["boolean", "null"]),
                out("lock_state", "integer", ["integer", "null"], "0, 1 or 2."),
            ],
            "runtime": {"runner": "is_locked", "method": "IsOperationLocked", "verb": "POST"},
        },
        {
            "operation_id": "change_journal",
            "display_name": f"{ADVANCED}Move an operation to another journal",
            "kind": "action",
            "calls": ["POST ChangeJournal"],
            "description": "Moves an operation to another journal (ChangeJournal, from the Postman collection).",
            "inputs": [
                CONNECTION,
                JOURNAL,
                NUMBER,
                text(
                    "new_journal",
                    "New journal",
                    "The journal to move it to.",
                    required=True,
                    maxLength=10,
                ),
            ],
            "outputs": op_result,
            "runtime": {"runner": "change_journal", "method": "ChangeJournal", "verb": "POST"},
        },
        {
            "operation_id": "copy_operation",
            "display_name": f"{ADVANCED}Copy an operation",
            "kind": "action",
            "calls": ["POST CopyOperation"],
            "description": "Copies an operation into another journal, optionally deleting the source "
            "(CopyOperation, from the Postman collection).",
            "inputs": [
                CONNECTION,
                JOURNAL,
                NUMBER,
                text(
                    "new_journal",
                    "New journal",
                    "The journal of the copy.",
                    required=True,
                    maxLength=10,
                ),
                flag(
                    "delete_source", "Delete the source", "Delete the original once it is copied."
                ),
                flag(
                    "keep_document",
                    "Keep the document number",
                    "Keep the original document number.",
                ),
                text("new_document", "New document", "The copy's document number.", maxLength=20),
                text("new_series", "New series", "The copy's document series.", maxLength=10),
                whole(
                    "copy_doc_to_description",
                    "Copy the document number to",
                    "Which operation name (1-5) receives the original document number; 0 none.",
                    maximum=5,
                ),
                IMPORT_PARAMETER,
            ],
            "outputs": op_result,
            "runtime": {"runner": "copy_operation", "method": "CopyOperation", "verb": "POST"},
        },
    ]


def lock_spec(operation_id: str, method: str, title: str, description: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "display_name": f"{ADVANCED}{title}",
        "kind": "action",
        "calls": [f"POST {method}"],
        "description": description,
        "inputs": [CONNECTION, JOURNAL, NUMBER, IMPORT_PARAMETER],
        "outputs": [out("finvalda_result", "integer", ["integer", "null"])],
        "runtime": {"runner": "lock", "method": method, "verb": "POST"},
    }


REPORT_FILTER_KEYS = [
    "Code",
    "Warehouse",
    "WarehouseGroup",
    "Goods",
    "Client",
    "DateFrom",
    "DateTo",
    "Logbook",
    "LogbookGroup",
    "OpType",
    "OpTypeGroup",
    "IncludeReturns",
]

ENTITY_TYPES: dict[int, str] = {
    1: "Account",
    2: "Client",
    3: "Sale",
    7: "Purchase",
    8: "Product",
    16: "Service",
    74: "Sales return",
    75: "Sales reservation",
    76: "Purchase return",
    77: "Purchase order",
    78: "Internal transfer",
    97: "Write-off",
    98: "Receipt",
}
FILE_OUTPUTS = [
    out("document_file", "artifact_handle"),
    out("file_name", "text", ["string", "null"], "The file's name."),
    out("size_bytes", "integer", "integer", "The file's size."),
]


def file_and_document_specs() -> list[dict[str, Any]]:
    entity = choice(
        "entity_type",
        "Attached to",
        "What the file belongs to: an operation (sale, purchase …) or a record (client, product …).",
        list(ENTITY_TYPES),
        required=True,
        labels=ENTITY_TYPES,
    )
    id1 = text(
        "entity_id1",
        "Record code or journal",
        "A record's code, or an operation's journal code.",
        required=True,
        maxLength=20,
    )
    id2 = text(
        "entity_id2",
        "Second code or number",
        "An operation's number, or an address's code; empty for one-code records.",
        maxLength=20,
    )
    fin_user = text(
        "fin_user",
        "Finvalda employee",
        "The Finvalda employee the change is made as (not the web service user). Default: the connection's Finvalda employee.",
        maxLength=20,
    )
    return [
        {
            "operation_id": "make_invoice",
            "display_name": f"{ADVANCED}Invoice PDF",
            "kind": "source",
            "calls": ["GET MakeInvoice"],
            "description": "Prints an operation with one of Finvalda's invoice forms to a PDF workflow "
            "file (MakeInvoice, manual 3.75). The forms are listed by List · Invoice forms.",
            "inputs": [
                CONNECTION,
                text(
                    "invoice_form",
                    "Invoice form",
                    "The form's code, for example PARD_01.",
                    required=True,
                    maxLength=30,
                ),
                text(
                    "journal", "Journal", "The operation's journal; with op_number.", maxLength=10
                ),
                whole(
                    "op_number",
                    "Operation number",
                    "The operation's number; with journal.",
                    minimum=1,
                ),
                text("series", "Series", "The document series; with document.", maxLength=10),
                text("document", "Document", "The document number; with series.", maxLength=20),
            ],
            "outputs": FILE_OUTPUTS,
            "runtime": {"runner": "make_invoice", "method": "MakeInvoice", "verb": "GET"},
        },
        {
            "operation_id": "make_report",
            "display_name": f"{ADVANCED}Report PDF",
            "kind": "source",
            "calls": ["GET MakeReport"],
            "description": "Runs one of Finvalda's reports to a PDF workflow file (MakeReport, manual "
            "3.76): balance, profit, sales and purchase lists, debts, stock. The codes are listed by "
            "List · Reports.",
            "inputs": [
                CONNECTION,
                text(
                    "report_code",
                    "Report",
                    "The report's code, for example BALANSAS_01.",
                    required=True,
                    maxLength=40,
                ),
                json_input(
                    "filter",
                    "Filter",
                    f"The report's filter, with any of: {', '.join(k for k in REPORT_FILTER_KEYS if k != 'Code')}. "
                    "Dates are YYYY.MM.DD.",
                ),
            ],
            "outputs": FILE_OUTPUTS,
            "runtime": {
                "runner": "make_report",
                "method": "MakeReport",
                "verb": "GET",
                "filters": REPORT_FILTER_KEYS,
            },
        },
        {
            "operation_id": "auto_reports_list",
            "display_name": f"{ADVANCED}Automatic reports",
            "kind": "source",
            "calls": ["GET GetAutoReportsList"],
            "description": "The names of the reports Finvalda prepares automatically (GetAutoReportsList, "
            "from the Postman collection).",
            "inputs": [CONNECTION],
            "outputs": [out("names", "array", "array", "The report file names."), COUNT],
            "runtime": {
                "runner": "auto_reports_list",
                "method": "GetAutoReportsList",
                "verb": "GET",
            },
        },
        {
            "operation_id": "auto_report",
            "display_name": f"{ADVANCED}Automatic report file",
            "kind": "source",
            "calls": ["GET GetAutoReport"],
            "description": "Downloads one automatically prepared report as a workflow file "
            "(GetAutoReport, from the Postman collection).",
            "inputs": [
                CONNECTION,
                text(
                    "report_name",
                    "Report file",
                    "The name from Automatic reports.",
                    required=True,
                    maxLength=200,
                ),
            ],
            "outputs": FILE_OUTPUTS,
            "runtime": {"runner": "auto_report", "method": "GetAutoReport", "verb": "GET"},
        },
        {
            "operation_id": "product_image",
            "display_name": f"{ADVANCED}Product image",
            "kind": "source",
            "calls": ["GET GetPrekesImage"],
            "description": "Downloads a product's picture as a workflow file (GetPrekesImage, manual 3.45).",
            "inputs": [
                CONNECTION,
                text(
                    "product_code",
                    "Product code",
                    "The product's code in Finvalda.",
                    required=True,
                    maxLength=20,
                ),
                date(
                    "edited_since",
                    "Edited since",
                    "Only when the picture changed on or after this date, YYYY-MM-DD.",
                ),
                date(
                    "created_since",
                    "Created since",
                    "Only when the product was created on or after this date.",
                ),
            ],
            "outputs": FILE_OUTPUTS,
            "runtime": {"runner": "product_image", "method": "GetPrekesImage", "verb": "GET"},
        },
        {
            "operation_id": "insert_document",
            "display_name": f"{ADVANCED}Upload a document",
            "kind": "action",
            "calls": ["POST InsertDocument"],
            "description": "Stores a workflow file in Finvalda's document register (InsertDocument, "
            "manual 3.80). Attach it to an operation or record with Attach a document.",
            "inputs": [
                CONNECTION,
                file_input("document_file", required=True),
                text(
                    "file_name",
                    "File name",
                    "The name to store it under, with its extension, for example S-1.pdf.",
                    required=True,
                    maxLength=200,
                ),
                text(
                    "file_description",
                    "Description",
                    "A description of the document.",
                    maxLength=200,
                ),
                text(
                    "registration_number",
                    "Registration number",
                    "The document's registration number.",
                    maxLength=50,
                ),
                date(
                    "composition_date", "Date", "The document's date, YYYY-MM-DD. Default: today."
                ),
                text(
                    "search_phrase",
                    "Search phrase",
                    "Words to find the document by.",
                    maxLength=200,
                ),
                json_input(
                    "info",
                    "Information",
                    "Up to three lines of information.",
                    kind="array",
                    maxItems=3,
                    items={"type": "string", "maxLength": 200},
                ),
                fin_user,
            ],
            "outputs": [
                out("file_name", "text", "string"),
                out("size_bytes", "integer", "integer"),
            ],
            "runtime": {"runner": "insert_document", "method": "InsertDocument", "verb": "POST"},
        },
        {
            "operation_id": "delete_document",
            "display_name": f"{ADVANCED}Delete a document",
            "kind": "action",
            "calls": ["POST DeleteDocument"],
            "description": "Removes a document from Finvalda's register by its file name "
            "(DeleteDocument, manual 3.81).",
            "inputs": [
                CONNECTION,
                text(
                    "file_name",
                    "File name",
                    "The stored file name, with its extension.",
                    required=True,
                    maxLength=200,
                ),
            ],
            "outputs": [out("file_name", "text", "string")],
            "runtime": {"runner": "delete_document", "method": "DeleteDocument", "verb": "POST"},
        },
        {
            "operation_id": "attach_document",
            "display_name": f"{ADVANCED}Attach a document",
            "kind": "action",
            "calls": ["GET AttachDocument"],
            "description": "Attaches a document already in the register to an operation or a record "
            "(AttachDocument, manual 3.82). A document already attached there is reported, not "
            "attached twice.",
            "inputs": [
                CONNECTION,
                entity,
                id1,
                id2,
                text(
                    "file_name",
                    "File name",
                    "The stored file name, with its extension.",
                    required=True,
                    maxLength=200,
                ),
                fin_user,
            ],
            "outputs": [
                out("attached", "boolean", "boolean"),
                out("already_attached", "boolean", "boolean"),
            ],
            "runtime": {"runner": "attach_document", "method": "AttachDocument", "verb": "GET"},
        },
        {
            "operation_id": "attached_documents",
            "display_name": f"{ADVANCED}Attached documents",
            "kind": "source",
            "calls": ["GET GetAttachedDocument"],
            "description": "Lists the files attached to an operation or a record, and can save one of "
            "them as a workflow file (GetAttachedDocuments, manual 3.83; the Pure interface "
            "names it GetAttachedDocument).",
            "inputs": [
                CONNECTION,
                entity,
                id1,
                id2,
                text(
                    "file_name",
                    "Save file",
                    "Save the attached file with this name as a workflow file.",
                    maxLength=200,
                ),
            ],
            "outputs": [
                out("files", "array", "array", "Each attached file's name and size."),
                COUNT,
                *FILE_OUTPUTS,
            ],
            "runtime": {
                "runner": "attached_documents",
                "method": "GetAttachedDocument",
                "verb": "GET",
            },
        },
        {
            "operation_id": "recommended_price",
            "display_name": f"{ADVANCED}Recommended price",
            "kind": "source",
            "calls": ["POST GetRecommendedPrice"],
            "description": "The price, VAT and discount Finvalda would suggest for a line of a sale or a "
            "purchase (GetRecommendedPrice, manual 3.79).",
            "inputs": [
                CONNECTION,
                choice(
                    "invoice_type",
                    "Operation group",
                    "The sales or the purchases group.",
                    [0, 1],
                    required=True,
                    labels={0: "Sales", 1: "Purchases"},
                ),
                date("invoice_date", "Date", "The operation date, YYYY-MM-DD.", required=True),
                choice(
                    "item_type",
                    "Line type",
                    "A product or a service.",
                    [1, 2],
                    required=True,
                    labels={1: "Product", 2: "Service"},
                ),
                text(
                    "item_code",
                    "Code",
                    "The product's or service's code.",
                    required=True,
                    maxLength=20,
                ),
                amount("quantity", "Quantity", "The quantity of the line.", required=True),
                text("warehouse_code", "Warehouse", "The warehouse of the line.", maxLength=10),
                text("client_code", "Client", "The client of the operation.", maxLength=15),
                text(
                    "client_address_code",
                    "Client address",
                    "The address of the operation.",
                    maxLength=13,
                ),
                fin_user,
            ],
            "outputs": [
                out(
                    "price",
                    "text",
                    ["string", "null"],
                    "Price without discount and VAT, exact decimal.",
                ),
                out("vat", "text", ["string", "null"], "VAT, exact decimal."),
                out("discount", "text", ["string", "null"], "Discount, exact decimal."),
                RESPONSE,
            ],
            "runtime": {
                "runner": "recommended_price",
                "method": "GetRecommendedPrice",
                "verb": "POST",
            },
        },
        {
            "operation_id": "user_permissions",
            "display_name": f"{ADVANCED}Employee permissions",
            "kind": "source",
            "calls": ["GET GetUserPermissions"],
            "description": "The warehouses, clients, operation types and journals a Finvalda employee "
            "may use (GetUserPermissions, manual 3.84).",
            "inputs": [CONNECTION, fin_user],
            "outputs": [
                out("permissions", "array", "array", "One entry per permission class."),
                COUNT,
                RESPONSE,
            ],
            "runtime": {
                "runner": "user_permissions",
                "method": "GetUserPermissions",
                "verb": "GET",
            },
        },
        {
            "operation_id": "invoices_related_to_customer",
            "display_name": f"{ADVANCED}Invoices related to a client",
            "kind": "source",
            "calls": ["POST GetInvoicesRelatedToCustomer"],
            "description": "The invoices related to a client by debt type (GetInvoicesRelatedToCustomer, "
            "from the Postman collection, which shows only nDebtType; the client is sent as "
            "sKlientoKodas, the name a published client of the web service uses).",
            "inputs": [
                CONNECTION,
                text("client_code", "Client code", "The client's code in Finvalda.", maxLength=15),
                whole(
                    "debt_type",
                    "Debt type",
                    "The collection's nDebtType, for example 0.",
                    maximum=10,
                ),
            ],
            "outputs": [RECORDS, RECORD, COUNT, RESPONSE],
            "runtime": {
                "runner": "related_invoices",
                "method": "GetInvoicesRelatedToCustomer",
                "verb": "POST",
            },
        },
        {
            "operation_id": "export_list",
            "display_name": f"{ADVANCED}Export a list to a file",
            "kind": "source",
            "calls": ["POST GetDescriptions (every page)"],
            "description": "Saves a whole GetDescriptions list — clients, products, stock, prices … — as a "
            "JSON Lines workflow file, page by page; it can resume where it stopped.",
            "inputs": [
                CONNECTION,
                choice(
                    "list_type",
                    "List",
                    "The GetDescriptions type, for example Clients.",
                    sorted(description_types()),
                    required=True,
                ),
                json_input(
                    "filter",
                    "Filter",
                    "The same filter as the list's own step.",
                ),
                json_input(
                    "columns",
                    "Columns",
                    "The columns to save; default all.",
                    kind="array",
                    maxItems=200,
                    items={"type": "string"},
                ),
                whole(
                    "start_page",
                    "Start page",
                    "The page to start from; pass resume_page to continue.",
                    minimum=1,
                    maximum=1_000_000,
                ),
                whole(
                    "max_pages",
                    "Max pages",
                    "At most this many pages of 1000 rows, 1 to 2000.",
                    minimum=1,
                    maximum=2000,
                ),
            ],
            "outputs": [
                out("artifact_handle", "artifact_handle"),
                out("filename", "text", "string"),
                out("content_type", "text", "string"),
                out("record_count", "integer", "integer"),
                out("pages_fetched", "integer", "integer"),
                out("complete", "boolean", "boolean", "True when the last page was reached."),
                out("resume_page", "integer", ["integer", "null"], "The page to continue from."),
                out("stopped_by", "text", ["string", "null"], "Why the export stopped early."),
            ],
            "runtime": {"runner": "export", "method": "GetDescriptions", "verb": "POST"},
        },
        {
            "operation_id": "call_method",
            "display_name": f"{ADVANCED}Call a Finvalda method",
            "kind": "action",
            "calls": ["any documented method"],
            "description": "Calls any method of the Finvalda web service by name, with query parameters "
            "or a JSON body, for cases the other steps do not cover. Counted as a change.",
            "inputs": [
                CONNECTION,
                text(
                    "method",
                    "Method",
                    "The method name, for example GetKlientusSet.",
                    required=True,
                    maxLength=80,
                ),
                choice(
                    "verb",
                    "HTTP method",
                    "GET or POST, as the method expects.",
                    ["GET", "POST"],
                    required=True,
                ),
                json_input(
                    "query",
                    "Query parameters",
                    'Parameters for the address, for example {"sKliKod": "K1"}.',
                ),
                json_input(
                    "body",
                    "Body",
                    "A JSON body for POST.",
                ),
            ],
            "outputs": [out("method", "text", "string"), RESPONSE],
            "runtime": {"runner": "call_method", "verb": "ANY"},
        },
    ]


# ---------------------------------------------------------------------------
# Everyday operations

CLIENT_LOOKUP = [
    text("client_code", "Client code", "The client's code in Finvalda.", maxLength=15),
    text(
        "client_company_code",
        "Client company code",
        "The client's company code; used when no client code is given.",
        maxLength=30,
    ),
]

LINE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "quantity", "price"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["product", "service"],
            "description": "product (default) or service.",
        },
        "code": {
            "type": "string",
            "maxLength": 20,
            "description": "The product's or service's code.",
        },
        "quantity": {"type": ["number", "string"], "description": "Quantity, in the primary unit."},
        "price": {
            "type": ["number", "string"],
            "description": "Unit price without VAT, in the document currency.",
        },
        "discount_percent": {"type": ["number", "string"], "description": "Discount in percent."},
        "vat_percent": {
            "type": ["number", "string"],
            "description": "VAT percent; the VAT amount is computed.",
        },
        "vat_code": {"type": "string", "maxLength": 10, "description": "The VAT (tax) code."},
        "warehouse": {
            "type": "string",
            "maxLength": 10,
            "description": "Products: the warehouse. Default: the step's warehouse.",
        },
        "amount_eur": {
            "type": ["number", "string"],
            "description": "The line amount in EUR; needed when the currency is not EUR.",
        },
        "objects": {
            "type": "array",
            "items": {"type": "string", "maxLength": 10},
            "maxItems": 6,
            "description": "Object codes 1 to 6.",
        },
    },
}


def invoice_spec(operation_id: str, noun: str, header: str, journal_hint: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "display_name": f"Create {noun}",
        "kind": "action",
        "calls": [
            "GetOperations (look the document up)",
            f"InsertNewOperation {header}",
            "InsertDocument + AttachDocument (with a file)",
        ],
        "description": (
            f"Creates a {noun} ({header}) with product and service lines; amounts and VAT are computed "
            "from quantity, price, discount and VAT percent. The document number makes it safe to run "
            f"again: the step first looks for a {noun} of that client with that number and returns it "
            "instead of a second one, and Finvalda itself refuses a number already used. Can attach "
            "a file, such as the PDF, for which the connection needs a Finvalda employee."
        ),
        "inputs": [
            CONNECTION,
            *CLIENT_LOOKUP,
            text(
                "document",
                "Document number",
                f"The {noun}'s number, for example {journal_hint}. Makes reruns safe.",
                required=True,
                maxLength=20,
            ),
            text(
                "series",
                "Series",
                "The document series. Default: from the import parameter.",
                maxLength=10,
            ),
            date("date", "Date", "The operation date, YYYY-MM-DD. Default: today."),
            date("due_date", "Due date", "The payment date, YYYY-MM-DD."),
            text("currency", "Currency", "A currency code such as EUR. Default: EUR.", maxLength=3),
            text(
                "warehouse",
                "Warehouse",
                "The warehouse for product lines that name none.",
                maxLength=10,
            ),
            text("notes", "Notes", "A note saved on the operation.", maxLength=255),
            json_input(
                "lines",
                "Lines",
                "The lines, 1 to 200: kind (product or service), code, quantity, price without VAT, "
                "discount_percent, vat_percent, warehouse.",
                kind="array",
                required=True,
                minItems=1,
                maxItems=200,
                items=LINE_ITEM,
            ),
            IMPORT_PARAMETER,
            file_input("document_file", required=False),
        ],
        "outputs": [
            *OP_RESULT,
            out("created", "boolean", "boolean", "False when an existing document was returned."),
            out("client_code", "text", ["string", "null"]),
            out(
                "total_without_vat",
                "text",
                ["string", "null"],
                "Sum of the lines without VAT, exact decimal.",
            ),
            out("total_vat", "text", ["string", "null"], "Sum of the computed VAT, exact decimal."),
            out(
                "attachment_name",
                "text",
                ["string", "null"],
                "The attached file's name in Finvalda.",
            ),
            RECORD,
        ],
    }


def everyday_specs() -> list[dict[str, Any]]:
    return [
        {
            "operation_id": "test_connection",
            "display_name": "Test the connection",
            "kind": "source",
            "calls": ["GetDescriptions type=Clients (one row)"],
            "description": "Reads one client to check the address, the web service user, the password and the company.",
            "inputs": [CONNECTION],
            "outputs": [
                out("connected", "boolean", "boolean"),
                out(
                    "company_id",
                    "text",
                    ["string", "null"],
                    "The company the connection reads, if set.",
                ),
                out(
                    "clients_visible",
                    "boolean",
                    "boolean",
                    "Whether the user can see at least one client.",
                ),
                out("duration_ms", "integer", "integer"),
            ],
        },
        {
            "operation_id": "find_or_create_client",
            "display_name": "Find or create client",
            "kind": "action",
            "calls": ["GetDescriptions type=Clients", "InsertNewItem Fvs.Klientas"],
            "description": (
                "Looks the client up by code, then company code, then VAT code, then e-mail; several "
                "matches are an error. Creates the client only when none matches."
            ),
            "inputs": [
                CONNECTION,
                text(
                    "code",
                    "Code",
                    "The client's code, looked up first and used for a new client (capital letters and digits).",
                    maxLength=15,
                ),
                text(
                    "company_code",
                    "Company code",
                    "The company code, looked up second.",
                    maxLength=13,
                ),
                text("vat_code", "VAT code", "The VAT payer code, looked up third.", maxLength=30),
                text("email", "E-mail", "The e-mail address, looked up last.", maxLength=30),
                text(
                    "name",
                    "Name",
                    "The name, needed when the client has to be created.",
                    maxLength=100,
                ),
                text("address", "Address", "The address of a new client.", maxLength=90),
                text("city", "City", "The city of a new client.", maxLength=20),
                text(
                    "country_code",
                    "Country",
                    "The ISO country code of a new client, for example LT.",
                    maxLength=3,
                ),
                text("phone", "Phone", "The phone number of a new client.", maxLength=15),
                text(
                    "client_type",
                    "Client type",
                    "The client type code of a new client.",
                    maxLength=10,
                ),
                flag(
                    "is_person",
                    "Natural person",
                    "The new client is a natural person, not a company.",
                ),
                ITEM_PARAMETER,
            ],
            "outputs": [
                out("client_code", "text", ["string", "null"], "The client's code in Finvalda."),
                out("created", "boolean", "boolean", "True when this step created the client."),
                out(
                    "matched_by",
                    "text",
                    ["string", "null"],
                    "code, company_code, vat_code or email.",
                ),
                RECORD,
            ],
        },
        {
            "operation_id": "find_or_create_product",
            "display_name": "Find or create product",
            "kind": "action",
            "calls": [
                "GetDescriptions type=Products",
                "GetDescriptions type=BarCodes",
                "InsertNewItem Fvs.Preke",
            ],
            "description": "Looks the product up by code, then barcode; creates it when none matches.",
            "inputs": [
                CONNECTION,
                text(
                    "code",
                    "Code",
                    "The product's code, looked up first and used for a new product.",
                    maxLength=20,
                ),
                text("barcode", "Barcode", "The barcode, looked up second.", maxLength=13),
                text(
                    "name",
                    "Name",
                    "The name, needed when the product has to be created.",
                    maxLength=50,
                ),
                text(
                    "unit",
                    "Unit",
                    "The unit of measure code of a new product, for example VNT.",
                    maxLength=10,
                ),
                text("vat_code", "VAT code", "The VAT (tax) code of a new product.", maxLength=10),
                text(
                    "account_link",
                    "Account link",
                    "The link to accounts of a new product.",
                    maxLength=10,
                ),
                text(
                    "product_type", "Product type", "The type code of a new product.", maxLength=13
                ),
                amount("price", "Sales price", "The first sales price of a new product."),
                amount("purchase_price", "Purchase price", "The purchase price of a new product."),
                ITEM_PARAMETER,
            ],
            "outputs": [
                out("product_code", "text", ["string", "null"]),
                out("created", "boolean", "boolean", "True when this step created the product."),
                out("matched_by", "text", ["string", "null"], "code or barcode."),
                RECORD,
            ],
        },
        {
            "operation_id": "find_or_create_service",
            "display_name": "Find or create service",
            "kind": "action",
            "calls": ["GetDescriptions type=Services", "InsertNewItem Fvs.Paslauga"],
            "description": "Looks the service up by code; creates it when it does not exist.",
            "inputs": [
                CONNECTION,
                text(
                    "code", "Code", "The service's code in Finvalda.", required=True, maxLength=13
                ),
                text(
                    "name",
                    "Name",
                    "The name, needed when the service has to be created.",
                    maxLength=50,
                ),
                text("unit", "Unit", "The unit of measure of a new service.", maxLength=10),
                text("vat_code", "VAT code", "The VAT (tax) code of a new service.", maxLength=10),
                text(
                    "account_link",
                    "Account link",
                    "The link to accounts of a new service.",
                    maxLength=10,
                ),
                amount("price", "Sales price", "The sales price of a new service."),
                amount("purchase_price", "Purchase price", "The purchase price of a new service."),
                ITEM_PARAMETER,
            ],
            "outputs": [
                out("service_code", "text", ["string", "null"]),
                out("created", "boolean", "boolean", "True when this step created the service."),
                RECORD,
            ],
        },
        invoice_spec("create_sales_invoice", "sales invoice", "PardDok", "S-1001"),
        invoice_spec(
            "create_purchase_invoice",
            "purchase invoice",
            "PirkDok",
            "the supplier's invoice number",
        ),
        {
            "operation_id": "register_payment",
            "display_name": "Register payment",
            "kind": "action",
            "calls": ["GetOperations (look the payment up)", "InsertNewOperation IplDok / IsmDok"],
            "description": (
                "Registers money received from a client (inflow) or paid to a supplier (disbursement), "
                "against one document or as a prepayment. Safe to run again: a payment of the client "
                "with the same document number, amount and paid invoice is returned instead of a "
                "second one; one bank statement may hold several payments, and a payment that cannot "
                "be told apart from an earlier one stops the step."
            ),
            "inputs": [
                CONNECTION,
                choice(
                    "direction",
                    "Direction",
                    "incoming (from a client) or outgoing (to a supplier).",
                    ["incoming", "outgoing"],
                    required=True,
                    labels={"incoming": "Incoming (inflow)", "outgoing": "Outgoing (disbursement)"},
                ),
                *CLIENT_LOOKUP,
                text(
                    "document",
                    "Payment document",
                    "The payment's document number, for example the bank statement line.",
                    required=True,
                    maxLength=20,
                ),
                amount("amount", "Amount", "The amount paid, in the currency.", required=True),
                date("date", "Date", "The payment date, YYYY-MM-DD. Default: today."),
                text(
                    "currency",
                    "Currency",
                    "A currency code such as EUR. Default: EUR.",
                    maxLength=3,
                ),
                text(
                    "invoice_series",
                    "Invoice series",
                    "The series of the document paid.",
                    maxLength=10,
                ),
                text(
                    "invoice_document",
                    "Invoice number",
                    "The number of the document paid. Without it, the payment is a prepayment.",
                    maxLength=20,
                ),
                text(
                    "description",
                    "Description",
                    "A description saved on the payment.",
                    maxLength=50,
                ),
                IMPORT_PARAMETER,
            ],
            "outputs": [
                *OP_RESULT,
                out(
                    "created", "boolean", "boolean", "False when an existing payment was returned."
                ),
                out("client_code", "text", ["string", "null"]),
                RECORD,
            ],
        },
        {
            "operation_id": "download_invoice_pdf",
            "display_name": "Download invoice PDF",
            "kind": "source",
            "calls": ["MakeInvoice"],
            "description": "Prints a sale or purchase with an invoice form to a PDF workflow file, to e-mail it or archive it.",
            "inputs": [
                CONNECTION,
                text(
                    "invoice_form",
                    "Invoice form",
                    "The form's code, for example PARD_01 (List · Invoice forms).",
                    required=True,
                    maxLength=30,
                ),
                text(
                    "journal", "Journal", "The operation's journal; with op_number.", maxLength=10
                ),
                whole(
                    "op_number",
                    "Operation number",
                    "The operation's number; with journal.",
                    minimum=1,
                ),
                text("series", "Series", "The document series; with document.", maxLength=10),
                text("document", "Document", "The document number; with series.", maxLength=20),
            ],
            "outputs": FILE_OUTPUTS,
        },
        {
            "operation_id": "attach_file",
            "display_name": "Attach file",
            "kind": "action",
            "calls": ["GetAttachedDocument", "InsertDocument", "AttachDocument"],
            "description": "Stores a workflow file in Finvalda and attaches it to a sale, a purchase or another operation, or to a client or product. A file of the same name already attached there is not attached twice.",
            "inputs": [
                CONNECTION,
                choice(
                    "entity_type",
                    "Attach to",
                    "What the file belongs to.",
                    list(ENTITY_TYPES),
                    required=True,
                    labels=ENTITY_TYPES,
                ),
                text(
                    "entity_id1",
                    "Code or journal",
                    "A record's code, or an operation's journal.",
                    required=True,
                    maxLength=20,
                ),
                text(
                    "entity_id2",
                    "Number",
                    "An operation's number; empty for records.",
                    maxLength=20,
                ),
                file_input("document_file", required=True),
                text(
                    "file_name",
                    "File name",
                    "The name to store it under, with its extension, for example S-1001.pdf.",
                    required=True,
                    maxLength=200,
                ),
                text(
                    "file_description", "Description", "A description of the file.", maxLength=200
                ),
            ],
            "outputs": [
                out("file_name", "text", "string"),
                out("stored", "boolean", "boolean", "True when this step stored the file."),
                out("attached", "boolean", "boolean", "True when this step attached it."),
            ],
        },
        {
            "operation_id": "stock_levels",
            "display_name": "Stock levels",
            "kind": "source",
            "calls": ["GetDescriptions type=CurrentStock"],
            "description": "Current stock of products by warehouse: quantity, quantity with reservations and cost.",
            "inputs": [
                CONNECTION,
                json_input(
                    "product_codes",
                    "Products",
                    "The product codes; default all.",
                    kind="array",
                    maxItems=500,
                    items={"type": "string", "maxLength": 20},
                ),
                text("warehouse", "Warehouse", "Only the stock of this warehouse.", maxLength=10),
                text(
                    "warehouse_group", "Warehouse group", "Only this warehouse group.", maxLength=10
                ),
                whole(
                    "limit",
                    "Rows",
                    "At most this many rows, 1 to 5000. Default 1000.",
                    minimum=1,
                    maximum=5000,
                ),
            ],
            "outputs": [
                out(
                    "items",
                    "array",
                    "array",
                    "One row per product and warehouse: code, title, warehouse, quantity, reserved, unit, cost.",
                ),
                COUNT,
                out("has_more", "boolean", "boolean"),
            ],
        },
        {
            "operation_id": "product_prices",
            "display_name": "Product prices",
            "kind": "source",
            "calls": ["GetDescriptions type=Products", "GetDescriptions type=Prices"],
            "description": "The six sales prices and the purchase price of products, and a client's special prices and discounts when a client is given.",
            "inputs": [
                CONNECTION,
                json_input(
                    "product_codes",
                    "Products",
                    "The product codes, 1 to 500.",
                    kind="array",
                    required=True,
                    minItems=1,
                    maxItems=500,
                    items={"type": "string", "maxLength": 20},
                ),
                text(
                    "client_code", "Client", "Also read this client's special prices.", maxLength=15
                ),
            ],
            "outputs": [
                out(
                    "items",
                    "array",
                    "array",
                    "One row per product: code, title, prices, currencies.",
                ),
                out(
                    "client_prices", "array", "array", "The client's special prices and discounts."
                ),
                COUNT,
            ],
        },
        {
            "operation_id": "client_debt",
            "display_name": "Client debt",
            "kind": "source",
            "calls": ["GetDescriptions type=Clients", "GetNeapmoketiKliDok"],
            "description": "A client's current debt and unpaid sales documents, with the overdue part.",
            "inputs": [
                CONNECTION,
                text(
                    "client_code",
                    "Client code",
                    "The client's code in Finvalda.",
                    required=True,
                    maxLength=15,
                ),
                date(
                    "as_of", "As of", "The date overdue is counted at, YYYY-MM-DD. Default: today."
                ),
            ],
            "outputs": [
                out("client_code", "text", "string"),
                out("client_name", "text", ["string", "null"]),
                out(
                    "debt",
                    "text",
                    ["string", "null"],
                    "The client's debt from the client card, exact decimal.",
                ),
                out(
                    "unpaid_total", "text", "string", "The sum of unpaid documents, exact decimal."
                ),
                out(
                    "overdue_total",
                    "text",
                    "string",
                    "The unpaid sum past its payment date, exact decimal.",
                ),
                out(
                    "documents",
                    "array",
                    "array",
                    "The unpaid documents: document, date, payment date, amount, remaining, currency, overdue.",
                ),
                COUNT,
            ],
        },
        {
            "operation_id": "changes_since",
            "display_name": "Changes since",
            "kind": "source",
            "calls": ["GetDescriptions (EditedFrom)", "GetOperations (DateEditedFrom)"],
            "description": "Records or operations created or edited since a moment: clients, products, services, sales, purchases, inflows, disbursements. Pass latest_edited as since next time.",
            "inputs": [
                CONNECTION,
                choice(
                    "entity",
                    "What",
                    "Which records or operations.",
                    list(CHANGE_ENTITIES),
                    required=True,
                    labels={k: v[2] for k, v in CHANGE_ENTITIES.items()},
                ),
                text(
                    "since",
                    "Since",
                    "YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS; records edited at or after it.",
                    required=True,
                    **DATE_TIME,
                ),
                whole(
                    "limit",
                    "Rows",
                    "At most this many rows, 1 to 5000. Default 1000.",
                    minimum=1,
                    maximum=5000,
                ),
            ],
            "outputs": [
                RECORDS,
                COUNT,
                out(
                    "latest_edited",
                    "text",
                    ["string", "null"],
                    "The latest edit time seen; pass it as since next time.",
                ),
                out(
                    "has_more",
                    "boolean",
                    "boolean",
                    "True when the limit cut the list; continue from latest_edited.",
                ),
            ],
        },
    ]


#: entity -> (kind, Finvalda name, label)
CHANGE_ENTITIES: dict[str, tuple[str, str, str]] = {
    "clients": ("descriptions", "Clients", "Clients"),
    "products": ("descriptions", "Products", "Products"),
    "services": ("descriptions", "Services", "Services"),
    "sales": ("operations", "Sales", "Sales"),
    "purchases": ("operations", "Purchases", "Purchases"),
    "inflows": ("operations", "Inflows", "Inflows (incoming payments)"),
    "disbursements": ("operations", "Disbursement", "Disbursements (outgoing payments)"),
}


def operations() -> list[dict[str, Any]]:
    """Everyday operations first, then every documented method (named Advanced · …)."""
    return [
        *everyday_specs(),
        *description_specs(),
        *operation_read_specs(),
        *legacy_read_specs(),
        *write_specs(),
        *file_and_document_specs(),
    ]


__all__ = ["ADVANCED", "CHANGE_ENTITIES", "OPTION_LABELS", "operations", "reference"]
