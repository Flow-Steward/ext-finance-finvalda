"""The rows and the saved operation in a Finvalda answer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import errors
from .fields import field
from .values import whole_of


def rows_of(data: Mapping[str, Any], *, strict: bool = True) -> list[Any]:
    """The list of rows in a Finvalda answer.

    ``strict`` (the reads everyday steps rely on, lookups before a create among
    them): the form every read example of Finvalda's Postman collection shows,
    ``{"AccessResult": "Success", "error": "", "items": [...]}``. No rows: an empty
    or null ``items``, or no data key at all (the empty answer the collection shows
    for GetInvoicesRelatedToCustomer). Any other data key is an error, never "no
    rows": a lookup would otherwise create a record that already exists.

    Not strict (the older methods, whose answers the manual describes by their
    columns only): ``items``, or the one list the answer holds, or nothing.
    """
    rest = {
        k: v
        for k, v in data.items()
        if str(k).lower() not in {"accessresult", "error", "serror", "nresult"}
    }
    items = field(rest, "items")
    if isinstance(items, list):
        return items
    if not rest or (items is None and {str(k).lower() for k in rest} == {"items"}):
        # No data key at all (as GetInvoicesRelatedToCustomer's empty example shows:
        # AccessResult, error, nResult) or "items": null: no rows.
        return []
    if strict:
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            "Finvalda's answer has no list of rows (items)"
            + (": " + ", ".join(sorted(str(k) for k in rest))[:200] if rest else ""),
        )
    if all(value in (None, "", []) for value in rest.values()):
        return []
    lists = [v for v in rest.values() if isinstance(v, list)]
    if len(lists) == 1:
        return lists[0]
    if len(rest) == 1:
        (only,) = rest.values()
        if isinstance(only, Mapping):
            nested = [v for v in only.values() if isinstance(v, list)]
            if len(nested) == 1:
                return nested[0]
            if len(only) == 1:
                (inner,) = only.values()
                if isinstance(inner, Mapping):
                    return [inner]
    return []


_STATUS = {"accessresult", "error", "serror"}
_RESULT_STATUS = {"errorcode", "errortext", "accessresult"}


def rest_of(data: Mapping[str, Any], rows: list[Any]) -> dict[str, Any] | None:
    """Everything in an answer but its rows and status fields, or None.

    The rows are removed wherever they are, with the objects that only wrapped
    them (``{"Klientai": {"Fvs.Klientas": [...]}}``), so nothing is returned twice.
    """
    found = {id(rows), *(id(r) for r in rows if isinstance(r, (Mapping, list)))}

    def strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            out = {}
            for key, item in value.items():
                if id(item) in found:
                    continue
                kept = strip(item)
                if isinstance(item, (Mapping, list)) and item and not kept:
                    continue
                out[key] = kept
            return out
        if isinstance(value, list):
            return [strip(item) for item in value if id(item) not in found]
        return value

    rest = strip({k: v for k, v in data.items() if str(k).lower() not in _STATUS})
    for key in [k for k in rest if str(k).lower() == "result"]:
        result = rest[key]
        if isinstance(result, Mapping):
            inner = {k: v for k, v in result.items() if str(k).lower() not in _RESULT_STATUS}
            if inner:
                rest[key] = inner
            else:
                del rest[key]
    return rest or None


def op_result(data: Mapping[str, Any]) -> dict[str, Any]:
    """Journal, number, series and document of a saved operation (OP_DUOMENYS)."""
    info = field(data, "OP_DUOMENYS")
    if not isinstance(info, Mapping):
        text = field(data, "error") or field(data, "sError") or ""
        info = {}
        if isinstance(text, str) and "<OP_DUOMENYS>" in text:
            for tag in ("SERIJA", "DOKUMENTAS", "ZURNALAS", "NUMERIS"):
                match = re.search(rf"<{tag}>(.*?)</\s*{tag}>", text, re.S)
                if match:
                    info[tag] = match.group(1).strip()
    number = field(info, "NUMERIS")
    # The same reading of a whole number as everywhere else ("61.0" is 61).
    op_number = whole_of(number)
    return {
        "journal": errors.safe_text(field(info, "ZURNALAS"), 20) or None,
        "op_number": op_number,
        "series": errors.safe_text(field(info, "SERIJA"), 20) or None,
        "document": errors.safe_text(field(info, "DOKUMENTAS"), 40) or None,
    }
