"""Render the UI pages that restate the operation registry, so they cannot drift.

* ``ui/pages/finvalda-api.yaml`` — the everyday steps in full, how steps behave, and
  every other step grouped by subject, in picker order
* ``ui/pages/coverage.yaml`` — what of Finvalda's documented web service is offered,
  and why the few names that are not have no step of their own
* ``ui/pages/changelog.yaml`` — ``CHANGELOG.md``

The platform renders page bodies as simple markdown: headings, paragraphs, lists,
bold, code and ``[label](https://…)`` links. Tables and bare URLs are not rendered,
so no page uses them (``tests/test_contracts.py`` checks that).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import step_names

ROOT = Path(__file__).resolve().parents[2]
POSTMAN_DOCS = "https://documenter.getpostman.com/view/7208231/2s8YmRMLvd"
HIDDEN_RESULTS = frozenset({"external_effect_status", "definitely_no_external_effect"})

BEHAVIOUR = """\
### The web service

- Every step calls the Finvalda web service of your company (the FvsServicePure interface) with the web service user of the connection. A step can do exactly what that user may do in Finvalda.
- Writes of operations use an import parameter (sParametras): a preset made in Finvalda's FvsNETParamKonfig tool that sets the journal, operation type, series and accounts. New clients, products and services can use an item import parameter the same way. Set defaults on the connection; a step can name another.
- The older read methods answer with Lithuanian column names, or English ones when the connection's language is English. The list steps (List · …) and the operation steps (Operations · …) always use English column names.

### Inputs

- Fields of records and operations use the names of Finvalda's manual (sKodas, sPavadinimas, tData, PardDokPrekeDetEil …). They are checked against the manual before anything is sent: an unknown field, a text that is too long or a value of the wrong kind is refused.
- Dates are YYYY-MM-DD. Amounts are exact decimals with "." as the decimal point.
- Results give every number Finvalda returns as exact decimal text ("12.50"), never rounded through floating point.

### Writes, retries and errors

- Flow Steward remembers every change it made, so a retried step does not send it twice. Test runs never write.
- When Finvalda refuses a change, it says why with its error code, and nothing was saved. The step shows the code and its meaning from the manual.
- A timeout, a lost connection, a server error, or one of the errors the manual describes as a program or transaction failure leave the outcome unknown: the step is not retried, and you check Finvalda first.
- Everyday steps look up before they create: clients and products by code, documents and payments by their number for that client. Finvalda itself refuses a document number already used.
- If a step that writes several things fails half-way, it says exactly what was already saved.

### Lists and files

- List steps read pages of up to 1000 rows. A step output has a size limit: a long read stops at a page boundary and returns next_page. For whole lists use Export a list to a file.
- Operation steps read everything the filter matches in one answer: give a date range or a journal.
- Files travel as workflow files: invoice and report PDFs, product pictures, documents attached to operations.
"""


class _PageDumper(yaml.SafeDumper):
    """Multi-line strings as ``|`` blocks, so the page files read like markdown."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_PageDumper.add_representer(str, _represent_str)


def dump_page(path: Path, header: str, page: dict[str, Any]) -> None:
    text = yaml.dump(page, Dumper=_PageDumper, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text(header + text, encoding="utf-8")


def _type_word(schema: dict[str, Any], value_type: str) -> str:
    """A short human type: text, whole number, yes/no, date, list of …, record."""
    if value_type == "artifact_handle":
        return "file"
    if "anyOf" in schema and "type" not in schema:
        # A typed value, or the text of a value that did not fit: named by its type.
        schema = schema["anyOf"][0]
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    kinds = [k for k in kinds if k != "null"]
    pattern = str(schema.get("pattern") or "")
    if schema.get("enum"):
        return (
            "one of "
            + ", ".join(str(v) for v in schema["enum"][:8])
            + (", …" if len(schema["enum"]) > 8 else "")
        )
    if kinds == ["string"] and pattern.startswith("^-?\\d+(\\.\\d+)?"):
        return "decimal"
    if kinds == ["string"] and "T" in pattern and "[+-]" in pattern and "d{2}:" in pattern:
        return "date and time"
    if kinds == ["string"] and pattern.startswith("^\\d{4}"):
        return "date"
    if set(kinds) == {"string", "number"}:
        return "decimal"
    if kinds == ["array"]:
        items = schema.get("items") or {}
        if items.get("properties") or items.get("anyOf"):
            return "list of records"
        return "list"
    if kinds == ["object"]:
        return "record" if schema.get("properties") else "object"
    return {
        "string": "text",
        "integer": "whole number",
        "number": "number",
        "boolean": "yes/no",
    }.get(kinds[0] if len(kinds) == 1 else "", " or ".join(kinds) or value_type)


def _describe_item(item: dict[str, Any], *, input_side: bool) -> str:
    schema = item.get("schema") or {}
    word = _type_word(schema, item.get("value_type", ""))
    optional = "" if not input_side or item.get("required") else ", optional"
    description = str(schema.get("description") or "").strip()
    if item.get("value_type") == "artifact_handle" and not description:
        description = "A workflow file."
    record = schema.get("items") if word == "list of records" else schema
    fields = list((record or {}).get("properties") or {})
    if fields and not input_side and word in {"record", "list of records"}:
        shown = ", ".join(f"`{f}`" for f in fields[:16]) + (", …" if len(fields) > 16 else "")
        description = f"{description} Fields: {shown}." if description else f"Fields: {shown}."
    return f"- `{item['name']}` ({word}{optional}){': ' + description if description else ''}"


def _inputs(op: dict[str, Any]) -> list[str]:
    items = [i for i in op["inputs"] if i["name"] != "connection_ref"]
    return [_describe_item(i, input_side=True) for i in items] or ["- none"]


def _results(op: dict[str, Any]) -> list[str]:
    items = [o for o in op["outputs"] if o["name"] not in HIDDEN_RESULTS]
    return [_describe_item(o, input_side=False) for o in items] or ["- none"]


def _section(op: dict[str, Any], spec: dict[str, Any], *, title: str | None = None) -> list[str]:
    heading = title or str(op["display_name"])
    kind = "changes data in Finvalda" if op["operation_kind"] == "action" else "reads only"
    lines = [f"### {heading}", "", str(op["description"]), ""]
    lines.append(
        f"Action id `{op['operation_id']}` · {kind} · Finvalda: "
        + ", ".join(f"`{c}`" for c in spec.get("calls", []))
    )
    lines += ["", "**Inputs**", "", *_inputs(op), "", "**Results**", "", *_results(op), ""]
    return lines


def api_page(ops: list[dict[str, Any]], specs: list[dict[str, Any]]) -> dict[str, Any]:
    """The steps in picker order: the everyday steps, then one section per subject."""
    spec_by_id = {s["operation_id"]: s for s in specs}
    op_by_id = {op["operation_id"]: op for op in ops}
    lines = [
        "These steps take plain inputs and make the Finvalda calls for you. Every documented "
        "method of the web service also has its own step; they follow, by subject, in the "
        "order of the step picker.",
        "",
    ]
    for oid, _title in step_names.EVERYDAY:
        lines.extend(_section(op_by_id[oid], spec_by_id[oid]))
    components: list[dict[str, Any]] = [
        {
            "component_id": "operations_everyday",
            "type": "markdown",
            "title": "Everyday steps",
            "body": "\n".join(lines).rstrip() + "\n",
        },
        {
            "component_id": "operations_behaviour",
            "type": "markdown",
            "title": "How steps behave",
            "body": BEHAVIOUR,
        },
    ]
    for component_id, title, intro, steps in step_names.GROUPS:
        body = [intro, ""]
        for oid, _title in steps:
            body.extend(_section(op_by_id[oid], spec_by_id[oid]))
        components.append(
            {
                "component_id": component_id,
                "type": "markdown",
                "title": title,
                "body": "\n".join(body).rstrip() + "\n",
            }
        )
    return {
        "page_id": "finvalda-api",
        "title": "Finvalda API",
        "description": "Every Finvalda step: what it does, which Finvalda calls it makes, its inputs and results.",
        "components": components,
    }


def coverage_page(cov: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    entries = cov["entries"]
    titles = {op["operation_id"]: str(op["display_name"]) for op in ops}
    methods = [k for k in entries if k.startswith("method:")]
    lists = [k for k in entries if k.startswith("GetDescriptions:")]
    classes = [k for k in entries if k.startswith("GetOperations:")]
    records = [k for k in entries if k.startswith("class:")]
    twins = [k for k in entries if entries[k]["reason"].startswith("The same data as")]
    noted = [k for k in entries if entries[k]["reason"] and k not in twins]
    lines = [
        f'The steps cover the whole web service Finvalda documents: the manual "FVS Webserviso '
        f"metodų naudojimo instrukcija\" (revision 110, 2025-04-14) and Finvalda's [Postman collection]({POSTMAN_DOCS}).",
        "",
        f"- **Methods:** {len(methods)} names, each with a step or a reason below.",
        f"- **Lists (GetDescriptions types):** {len(lists)}, one step each (List · …).",
        f"- **Operation classes (GetOperations):** {len(classes)}, one step each (Operations · …).",
        f"- **Record and operation classes (writes):** {len(records)}, through Create a record, "
        "Update a record, Create an operation, Change an operation and Delete an operation.",
        f"- **XML twins:** {len(twins)} methods ending in Xml return the same data as their "
        "method without Xml, as an XML string. Each is offered by that method's step, which returns "
        "the data as JSON (tests/reference/coverage.json lists every pair).",
        "",
        "### Names with a note",
        "",
    ]
    for key in noted:
        entry = entries[key]
        name = key.split(":", 1)[1]
        target = titles.get(entry["operation"] or "", "")
        where = f" → {target}" if target else " → no step"
        lines.append(f"- **{name}**{where}. {entry['reason']}")
    lines += [
        "",
        "### Not verified on a live server",
        "",
        "- No Finvalda server was available while this version was built, so every step follows the "
        "manual and the Postman collection and has been tested against recorded answers only.",
        "- For a few parameters, a published client reports that the live interface reads another "
        "name than the manual (sSandKod for sSanKod; tDataNuo and tDataIki for tNuo and tIki of the "
        "order management reads). Those steps send both names.",
        "- The document methods (InsertDocument, AttachDocument, GetAttachedDocument) are written as "
        "the manual's Pure examples show them.",
        "- The everyday steps take the operation lists' client column for the client's code (as in "
        "the Postman example of Sales, where client_code holds the company code). If a live server "
        "shows otherwise, a purchase or payment could be registered twice: check this first.",
        "- Register payment does not tell a rerun from another payment: any payment of the "
        "client with the same document number stops the step without a write.",
        "- Attach file stores the file in Finvalda's document register before attaching it. The "
        'manual documents no "already stored" answer and the register cannot be listed, so after '
        "a stored but unattached file a rerun reports Finvalda's refusal with a hint to attach it "
        "by hand; only the invoice steps, whose file names include the kind, client and document, "
        "attach the stored file themselves.",
        "- Times given as /Date(ms)/ without an offset are read as Lithuanian local time, like "
        "every other time Finvalda gives.",
        "- Invoices related to a client sends the client as sKlientoKodas, a name from a published "
        "client, not from Finvalda; the step passes on only rows that show they belong to that client.",
    ]
    return {
        "page_id": "coverage",
        "title": "Coverage",
        "description": "What of Finvalda's documented web service the steps offer.",
        "components": [
            {
                "component_id": "coverage_summary",
                "type": "markdown",
                "title": "Coverage",
                "body": "\n".join(lines).rstrip() + "\n",
            }
        ],
    }


def changelog_page() -> dict[str, Any]:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return {
        "page_id": "changelog",
        "title": "Changelog",
        "description": "What changed in each release of this extension.",
        "components": [
            {
                "component_id": "changelog_entries",
                "type": "markdown",
                "title": "Release history",
                "body": "\n".join(line.rstrip() for line in lines).strip() + "\n",
            }
        ],
    }
