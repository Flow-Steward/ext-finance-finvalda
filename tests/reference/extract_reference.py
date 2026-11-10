#!/usr/bin/env python3
"""Extract the Finvalda web service reference into ``fvs_reference.json``.

Two sources, both pinned by SHA-256:

* Finvalda's published Postman collection "Finvaldos webservisas V2"
  (https://documenter.getpostman.com/view/7208231/2s8YmRMLvd), committed as
  ``sources/finvalda_postman_collection.json``;
* Finvalda's manual "FVS Webserviso metodų naudojimo instrukcija", revision 110
  (2025-04-14). Finvalda gives it to customers as a Word file; the copy read here
  is the Markdown conversion published in the MIT-licensed finvalda-sdk
  repository at a fixed commit (``MANUAL_URL``). The manual itself is not
  committed: only the facts extracted from it (method names, parameter names,
  field tables, error codes) are, in ``fvs_reference.json``.

Run from the bundle root:

    python3 tests/reference/extract_reference.py <path to FVS_Webservice.md>

``tests/test_reference.py`` checks that the committed contracts cover every
method and class recorded here; it re-runs this extraction when the manual is
available (``FINVALDA_MANUAL`` names its path) and compares the result.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
POSTMAN = HERE / "sources" / "finvalda_postman_collection.json"
OUTPUT = HERE / "fvs_reference.json"

POSTMAN_URL = "https://documenter.getpostman.com/view/7208231/2s8YmRMLvd"
POSTMAN_SHA256 = "6de5f1cd12ede2afba8394c1a3b29469182bb9a09ee5d7a12cb586cedc69b80f"
MANUAL_URL = (
    "https://github.com/arturas88/finvalda-sdk/blob/"
    "dcdf7ca27d033eae5cb18e839d1c3c76f012c6d0/docs/FVS_Webservice.md"
)
MANUAL_SHA256 = "d74d6b5a91388140bc22feb013ff056e171c5fa7d09a5c5b2094406f34ccb74e"

#: Output-only or format parameters that are not inputs of a method.
NOT_INPUTS = frozenset({"writeSchema", "Data", "Xml", "sError", "data", "error", "nResult"})
#: Sections of the manual that describe one or more read methods by name.
_SECTION_RE = re.compile(r"^##\s+\d+\.\d+\.\s+(?P<names>[A-Za-z][A-Za-z0-9, ]+?)\s*$")
_CLASS_RE = re.compile(r"^#{3,4}\s+\\?<?(?P<names>[A-Za-z][A-Za-z0-9., ;()ŽžĖė\\ -]*?)\\?>?\s*$")
_SIG_RE = re.compile(r"^(?P<name>[A-Z]\w+)\((?P<args>.*)\)\s*$")
_PARAM_LINE_RE = re.compile(r"^\*?(?P<name>[a-zA-Z]\w*)\*?\s*[–-]\s*(?P<text>.+)$")
_ERROR_RE = re.compile(r"^(?P<code>\d{1,4})\s*[–-]\s*(?P<text>.+?);?\s*$")
_METHOD_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]+$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_after(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """The first markdown table at or after ``start`` (rows without the header)."""
    i = start
    while i < len(lines) and not lines[i].startswith("|"):
        if lines[i].startswith("#"):
            return [], i
        i += 1
    rows: list[list[str]] = []
    header = True
    while i < len(lines) and lines[i].startswith("|"):
        cells = _cells(lines[i])
        if header:
            header = False
        elif not all(set(c) <= {"-", ":", " "} for c in cells):
            rows.append(cells)
        i += 1
    return rows, i


def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text)
    text = text.replace("\\", "").replace("*", "")
    return " ".join(text.split())


def _signature_params(args: str) -> list[str]:
    names = []
    for part in args.split(","):
        words = part.replace("\\", "").split()
        if not words or words[0] == "out" or "out" in words[:1]:
            continue
        name = words[-1]
        if name in NOT_INPUTS or not re.fullmatch(r"[A-Za-z]\w*", name):
            continue
        names.append(name)
    return names


def _fields(rows: list[list[str]]) -> list[dict[str, Any]]:
    """Rows of a Tipas | Laukas | Aprašymas | Ilgis | Privaloma | Pasirenkama | Pastaba table."""
    fields = []
    for row in rows:
        if len(row) < 3 or not re.fullmatch(r"[a-zA-Z]\w*", row[1]):
            continue
        length = row[3] if len(row) > 3 else ""
        fields.append(
            {
                "name": row[1],
                "type": _clean(row[0]),
                "description": _clean(row[2]),
                "length": int(length) if length.isdigit() else None,
                "required": len(row) > 4 and "+" in row[4],
                "from_parameter": len(row) > 5 and "+" in row[5],
                "note": _clean(row[6]) if len(row) > 6 else "",
            }
        )
    return fields


def _result_fields(rows: list[list[str]]) -> list[dict[str, str]]:
    """Rows of a Nr. | Laukas | Tipas | Aprašymas result table."""
    out = []
    for row in rows:
        if len(row) >= 4 and re.fullmatch(r"\w+", row[1]):
            out.append({"name": row[1], "type": _clean(row[2]), "description": _clean(row[3])})
    return out


def parse_manual(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    meta = {
        "revision": re.search(r'^revision:\s*"?(\d+)', text, re.M).group(1),  # type: ignore[union-attr]
        "date": re.search(r'^date:\s*"(.+)"', text, re.M).group(1),  # type: ignore[union-attr]
    }
    start = next(i for i, line in enumerate(lines) if line.startswith("# 3. Webserviso metodai"))
    end = next(i for i, line in enumerate(lines) if line.startswith("# **I Priedas"))
    sections: list[tuple[int, list[str]]] = []
    for i in range(start, end):
        match = _SECTION_RE.match(lines[i])
        if match:
            names = [n.strip() for n in match.group("names").split(",") if n.strip()]
            if all(_METHOD_NAME_RE.fullmatch(n) for n in names):
                sections.append((i, names))
    # GetDescriptions is a top-level heading ("# 4. GetDescriptions").
    for i in range(start, end):
        if lines[i].strip() == "# 4. GetDescriptions":
            sections.append((i, ["GetDescriptions"]))
    sections.sort()
    methods: dict[str, dict[str, Any]] = {}
    for index, (line_no, names) in enumerate(sections):
        stop = sections[index + 1][0] if index + 1 < len(sections) else end
        body = lines[line_no + 1 : stop]
        signatures: dict[str, list[str]] = {}
        in_code = False
        for line in body:
            if line.startswith("```"):
                in_code = not in_code
                continue
            sig = _SIG_RE.match(line.strip()) if in_code else None
            if sig and not sig.group("name").endswith("Xml"):
                signatures[sig.group("name")] = _signature_params(sig.group("args"))
        documented: list[str] = []
        mode = ""
        for line in body:
            if line.startswith("### "):
                mode = line[4:].strip().lower()
                continue
            if mode.startswith("įėjimo") or mode.startswith("input"):
                match = _PARAM_LINE_RE.match(line.strip().lstrip("- "))
                if match and match.group("name") not in NOT_INPUTS:
                    documented.append(match.group("name"))
        result_rows: list[list[str]] = []
        for j, line in enumerate(body):
            if line.startswith("### Rezultatas"):
                result_rows, _ = _table_after(body, j + 1)
                break
        for name in names:
            params = signatures.get(name)
            methods[name] = {
                "section": " ".join(lines[line_no].split()[1:2]).rstrip("."),
                "params": params if params is not None else list(dict.fromkeys(documented)),
                "params_from": "signature" if params is not None else "text",
                "result_fields": _result_fields(result_rows),
            }
    # Every read method named in the grouped lists of section 3, Xml twins aside.
    listed: set[str] = set()
    for line in lines[start : start + 300]:
        name = line.strip().rstrip(",.")
        if _METHOD_NAME_RE.fullmatch(name):
            listed.add(name)
    # Method names the manual mentions anywhere (revision history, examples) —
    # including some without a section of their own.
    verbs = r"(?:Get|Insert|Edit|Delete|Update|Append|Attach|Make|Lock|UnLock|Is|Change|Copy)"
    mentioned = set(re.findall(rf"\b({verbs}[A-Z][A-Za-z0-9]+)\b", text))
    classes = _classes(lines, start, end)
    return {
        "meta": meta,
        "methods": methods,
        "listed_methods": sorted(listed),
        "mentioned_methods": sorted(mentioned),
        "classes": classes,
        "errors": _errors(lines, end),
        "get_operations": _get_operations(lines),
        "descriptions": _descriptions(lines),
    }


def _classes(lines: list[str], start: int, end: int) -> dict[str, list[dict[str, Any]]]:
    """Field tables of item and operation classes (InsertNewItem … UpdateOperation)."""
    classes: dict[str, list[dict[str, Any]]] = {}
    for i in range(start, end):
        line = lines[i]
        names: list[str] = []
        match = re.match(r"^#{3,4}\s+(.+?)\s*$", line)
        bold = re.match(r"^\*{2,3}\\?(?P<names>[A-Z][A-Za-z, ]+?)\s*(?:\*{2,3}|\()", line)
        if match and not match.group(1).startswith(("Aprašymas", "Įėjimo", "Išėjimo")):
            raw = match.group(1).replace("\\<", "").replace("\\>", "").replace("<", "")
            raw = raw.replace(">", "").split("(")[0]
            names = [n.strip() for n in re.split(r"[,;]|\.\.", raw) if n.strip()]
        elif bold:
            names = [n.strip() for n in bold.group("names").split(",") if n.strip()]
        if not names or not all(re.fullmatch(r"[A-Za-z][\w.]*", n) for n in names):
            continue
        if names == ["Rezultatas"]:
            continue
        rows, _ = _table_after(lines, i + 1)
        fields = _fields(rows)
        if not fields:
            continue
        for name in names:
            classes.setdefault(name, fields)
    # "Fvs.PrekesPoz1 .. Fvs.PrekesPoz20" names a range.
    if "Fvs.PrekesPoz1" in classes:
        for n in range(2, 21):
            classes.setdefault(f"Fvs.PrekesPoz{n}", classes["Fvs.PrekesPoz1"])
    return dict(sorted(classes.items()))


def _errors(lines: list[str], start: int) -> dict[str, str]:
    errors: dict[str, str] = {}
    for line in lines[start:]:
        if line.startswith("# 7."):
            break
        match = _ERROR_RE.match(line.strip())
        if match:
            errors.setdefault(match.group("code"), _clean(match.group("text")))
    return dict(sorted(errors.items(), key=lambda kv: int(kv[0])))


def _get_operations(lines: list[str]) -> dict[str, Any]:
    """OpClass values and filter names of GetOperations (its parameter table)."""
    op_classes: list[str] = []
    filters: list[str] = []
    for line in lines:
        if line.startswith("| OpClass |"):
            values = _cells(line)[2]
            op_classes = re.findall(r"(?:^|<br>)\s*([A-Z][A-Za-z]+)\b", values)
        if line.startswith("| filter |"):
            values = _cells(line)[2]
            for chunk in values.split("<br>"):
                for name in re.findall(r"^\s*([A-Z][A-Za-z0-9]+)(?:,|\s|$)", chunk):
                    filters.append(name)
                if chunk.strip().startswith("Description,"):
                    filters.extend(n.strip() for n in chunk.split("–")[0].split(","))
                if chunk.strip().startswith("Object1..6"):
                    filters.extend(f"Object{n}" for n in range(1, 7))
    filters = [f for f in dict.fromkeys(filters) if f not in {"Pastabos", "Object1", "Papildomi"}]
    filters += [f"Object{n}" for n in range(1, 7)]
    return {
        "op_classes": list(dict.fromkeys(op_classes)),
        "filters": list(dict.fromkeys(filters)),
    }


def _descriptions(lines: list[str]) -> dict[str, Any]:
    """GetDescriptions types of section 4: filter object(s), their keys, and columns.

    The manual writes each type as commented JSON (``"Code": "", \\* Prekės kodas``),
    sometimes broken by quoted lines; keys are read token by token.
    """
    start = next(i for i, line in enumerate(lines) if line.strip() == "# 4. GetDescriptions")
    out: dict[str, Any] = {}
    sections = [i for i in range(start, len(lines)) if re.match(r"^## 4\.\d+\.", lines[i])]
    stop_at = next(i for i in range(start, len(lines)) if lines[i].startswith("## Pavyzd"))
    for index, line_no in enumerate(sections):
        end = sections[index + 1] if index + 1 < len(sections) else stop_at
        body = [ln for ln in lines[line_no + 1 : end] if not ln.startswith("```")]
        text = "\n".join(body)
        kind = re.search(r'"type"\s*:\s*"(\w+)"', text)
        if not kind:
            continue
        entry: dict[str, Any] = {"filters": {}, "columns": [], "paging": '"page"' in text}
        current: str | None = None
        in_columns = False
        for raw in body:
            line = raw.split("\\*")[0].lstrip("> ")
            opener = re.match(r'^\s*"(\w+)"\s*:\s*\{', line)
            if opener and opener.group(1) != "type":
                current = opener.group(1)
                entry["filters"].setdefault(current, [])
                continue
            if re.match(r'^\s*"?(\w+)"?\s*:\s*$', line) and "columns" not in line:
                name = re.match(r'^\s*"?(\w+)', line).group(1)  # type: ignore[union-attr]
                current = name
                entry["filters"].setdefault(current, [])
                continue
            if current and line.strip().startswith("}"):
                current = None
                continue
            if '"columns"' in line:
                in_columns = True
                line = line.split(":", 1)[1]
            if in_columns:
                for token in re.findall(r'"?([a-z][a-z0-9_]*)"?\s*(?:,|\]|$)', line):
                    if token not in entry["columns"]:
                        entry["columns"].append(token)
                if "]" in line:
                    in_columns = False
                continue
            if current:
                for key in re.findall(r'"?(\w+)"\s*:', line):
                    if key not in entry["filters"][current]:
                        entry["filters"][current].append(key)
        out[kind.group(1)] = entry
    return dict(sorted(out.items()))


def _json_in_html(description: str) -> Any:
    """The first JSON object in a Postman description (``<code>{…}</code>``)."""
    for block in re.findall(r"<code[^>]*>(.*?)</code>", description, re.S):
        text = html.unescape(block).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def _endpoint(request: dict[str, Any]) -> str:
    url = request.get("url")
    raw = url.get("raw", "") if isinstance(url, dict) else str(url or "")
    return raw.split("?")[0].rsplit("/", 1)[-1]


def parse_postman(data: dict[str, Any]) -> dict[str, Any]:
    endpoints: dict[str, set[str]] = {}
    descriptions: dict[str, Any] = {}
    operations: dict[str, Any] = {}
    examples: dict[str, Any] = {}

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            if "item" in item:
                walk(item["item"])
                continue
            request = item.get("request") or {}
            name = _endpoint(request)
            endpoints.setdefault(name, set()).add(str(request.get("method", "GET")).upper())
            body = request.get("body") or {}
            raw = body.get("raw") if isinstance(body, dict) else None
            parsed: Any = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
            full = _json_in_html(str(request.get("description") or ""))
            if name == "GetDescriptions" and isinstance(parsed, dict):
                params = parsed.get("readParams") or {}
                kind = params.get("type")
                if kind:
                    entry = descriptions.setdefault(
                        kind, {"filter_key": None, "filter": [], "columns": []}
                    )
                    same = isinstance(full, dict) and full.get("type") == kind
                    for source in (params, full if same else {}):
                        for key, value in source.items():
                            if key in {"type", "page", "limit", "columns"}:
                                continue
                            if isinstance(value, dict):
                                entry["filter_key"] = entry["filter_key"] or key
                                for f in value:
                                    if f not in entry["filter"]:
                                        entry["filter"].append(f)
                        cols = source.get("columns")
                        if isinstance(cols, list):
                            for c in cols:
                                if isinstance(c, str) and c not in entry["columns"]:
                                    entry["columns"].append(c)
            elif name == "GetOperations" and isinstance(parsed, dict):
                params = parsed.get("opReadParams") or {}
                kind = params.get("OpClass")
                if kind:
                    entry = operations.setdefault(
                        kind, {"filter": [], "columns": [], "columns_det": []}
                    )
                    same = isinstance(full, dict) and full.get("OpClass") == kind
                    for source in (params, full if same else {}):
                        for f in source.get("filter") or {}:
                            if f not in entry["filter"]:
                                entry["filter"].append(f)
                        for key, out in (("columns", "columns"), ("columnsDet", "columns_det")):
                            cols = source.get(key) or {}
                            cols = cols.get("column") if isinstance(cols, dict) else cols
                            for c in cols or []:
                                if isinstance(c, str) and c not in entry[out]:
                                    entry[out].append(c)
            elif isinstance(parsed, dict) and name not in examples:
                examples[name] = sorted(parsed)

    walk(data["item"])
    return {
        "published": data["info"].get("publishDate"),
        "name": data["info"].get("name"),
        "endpoints": {k: sorted(v) for k, v in sorted(endpoints.items())},
        "descriptions": dict(sorted(descriptions.items())),
        "operations": dict(sorted(operations.items())),
        "body_keys": dict(sorted(examples.items())),
    }


def build(manual: Path) -> dict[str, Any]:
    if sha256(POSTMAN) != POSTMAN_SHA256:
        raise SystemExit(f"{POSTMAN.name}: unexpected SHA-256; update POSTMAN_SHA256 on purpose")
    if sha256(manual) != MANUAL_SHA256:
        raise SystemExit(f"{manual}: unexpected SHA-256; update MANUAL_SHA256 on purpose")
    return {
        "sources": {
            "postman": {"url": POSTMAN_URL, "sha256": POSTMAN_SHA256},
            "manual": {"url": MANUAL_URL, "sha256": MANUAL_SHA256},
        },
        "manual": parse_manual(manual.read_text(encoding="utf-8")),
        "postman": parse_postman(json.loads(POSTMAN.read_text(encoding="utf-8"))),
    }


def dumps(reference: dict[str, Any]) -> str:
    return json.dumps(reference, ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract_reference.py <FVS_Webservice.md>")
    reference = build(Path(sys.argv[1]))
    OUTPUT.write_text(dumps(reference), encoding="utf-8")
    manual = reference["manual"]
    print(
        f"{len(manual['methods'])} methods, {len(manual['classes'])} classes, "
        f"{len(manual['errors'])} error codes, "
        f"{len(reference['postman']['endpoints'])} Postman endpoints"
    )


if __name__ == "__main__":
    main()
