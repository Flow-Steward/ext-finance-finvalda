"""Shaping a step's result: the write markers and the output budget."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from . import budget, errors
from .values import exact

#: Fields kept when a write's echo is shrunk to fit the step output.
_KEY_FIELDS = re.compile(
    r"(code|kodas|journal|zurnalas|number|numeris|series|serija|document|dokumentas)", re.I
)


def mark_write(result: dict[str, Any]) -> dict[str, Any]:
    result["external_effect_status"] = "succeeded"
    result["definitely_no_external_effect"] = False
    return result


#: Identifying fields besides ``_KEY_FIELDS``: ids.
_ID_FIELDS = re.compile(r"(^id$|_id$|Id$|ID$)")
_SCALARS = (str, int, Decimal, bool, type(None))
MAX_KEPT_TEXT = 200
MAX_KEPT_WARNINGS = 20


def _identifiers(record: Mapping[str, Any]) -> dict[str, Any]:
    """The record's own identifying fields (codes, journal, numbers, series, document,
    ids), short scalars only."""
    return {
        key: value
        for key, value in record.items()
        if isinstance(key, str)
        and (_KEY_FIELDS.search(key) or _ID_FIELDS.search(key))
        and isinstance(value, _SCALARS)
        and len(str(value)) <= MAX_KEPT_TEXT
    }


def fit_write_result(result: dict[str, Any]) -> dict[str, Any]:
    """The result of a write that already happened, cut until it fits the step output.

    Every large record or list (client, product, service, invoice, payment, record,
    response…) is cut to its identifying fields, largest first, until the result
    fits. When even that does not fit, only the result's short values and the first
    warnings are kept. A cut is always said in the warnings; the write's own facts are
    set by the caller.
    """
    if budget.fits(result):
        return result
    warnings = result.setdefault("warnings", [])
    large = [
        key
        for key, value in result.items()
        if key != "warnings" and isinstance(value, (Mapping, list))
    ]
    large.sort(key=lambda k: budget.measure(result[k])[3], reverse=True)
    for key in large:
        value = result[key]
        if isinstance(value, Mapping):
            result[key] = _identifiers(value)
        else:
            result[key] = [_identifiers(v) for v in value if isinstance(v, Mapping)][:100]
        warnings.append(
            f"The {key} returned is too large for the step output; only its identifying "
            "fields (codes, journal, numbers) are kept: see the full record in Finvalda"
        )
        if budget.fits(result):
            return result
    kept: dict[str, Any] = {
        key: value
        for key, value in result.items()
        if key != "warnings" and isinstance(value, _SCALARS) and len(str(value)) <= MAX_KEPT_TEXT
    }
    kept["warnings"] = [errors.safe_text(w, 300) for w in warnings[:MAX_KEPT_WARNINGS]] + [
        "The result is cut to its identifiers: it is too large for the step output"
    ]
    return kept


def _ok(result: Mapping[str, Any]) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "result": exact(dict(result)),
        "error_code": None,
        "error": None,
        "errors": [],
    }
    if "external_effect_status" in result:
        response["external_effect_status"] = result["external_effect_status"]
        response["definitely_no_external_effect"] = result["definitely_no_external_effect"]
    return response
