"""Review 2026-11-17, item 9 (planner's acceptance): a list of ids cut for the step output
keeps its short values, and the warning says what was really kept."""

from __future__ import annotations

from runtime import budget
from runtime.results import fit_write_result


def test_9_a_long_list_of_ids_keeps_ids_and_says_how_many() -> None:
    ids = list(range(1, 1_000_001))
    result = fit_write_result({"sent_ids": ids, "warnings": []})
    assert result["sent_ids"] == ids[:100]
    assert budget.fits(result)
    assert any("100 of its 1000000 items are kept" in w for w in result["warnings"]), result[
        "warnings"
    ]


def test_9_a_list_of_records_and_codes_keeps_both() -> None:
    items = [{"id": i, "note": "x" * 300} for i in range(20_000)] + ["CODE-1"]
    result = fit_write_result({"items": items, "warnings": []})
    assert result["items"][:2] == [{"id": 0}, {"id": 1}]
    assert budget.fits(result)
    assert any("records cut to their identifying fields" in w for w in result["warnings"])


def test_9_a_list_of_only_long_values_is_said_to_be_left_out() -> None:
    result = fit_write_result({"texts": ["y" * 500] * 20_000, "warnings": []})
    assert result["texts"] == []
    assert any("it is left out" in w for w in result["warnings"]), result["warnings"]
