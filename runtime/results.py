"""Shaping a step's result: the write markers and the output budget."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import budget
from .values import exact

#: Fields kept when a write's echo is shrunk to fit the step output.
_KEY_FIELDS = re.compile(
    r"(code|kodas|journal|zurnalas|number|numeris|series|serija|document|dokumentas)", re.I
)


def mark_write(result: dict[str, Any]) -> dict[str, Any]:
    result["external_effect_status"] = "succeeded"
    result["definitely_no_external_effect"] = False
    return result


def fit_write_result(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink a write's echo until it fits; the write itself already happened."""
    if budget.fits(result):
        return result
    for key in ("record", "response"):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k: v for k, v in value.items() if _KEY_FIELDS.search(str(k))}
    if not budget.fits(result):
        for key in ("record", "response"):
            if key in result:
                result[key] = None
    return result


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
