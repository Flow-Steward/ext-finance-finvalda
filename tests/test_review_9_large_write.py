"""Review 2026-11-17, item 9: a write's result too large for the step output keeps its
identifiers and the write's facts, also through the real main.py."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import Any

from support import BUNDLE, FakeFinvalda, items, run

from runtime import budget


CLIENT = {
    "code": "K1",
    "company_code": "300000001",
    "vat_payer_code": "LT1",
    "email": "k@k.lt",
}


def test_9_a_large_client_keeps_its_code_and_fits() -> None:
    big = {**CLIENT, "title": "x" * 3_600_000}
    response = run(
        FakeFinvalda(GetDescriptions=items(big)), "find_or_create_client", {"code": "K1"}
    )
    assert response["ok"] is True, response.get("error")
    assert response["external_effect_status"] == "succeeded"
    result = response["result"]
    assert result["client_code"] == "K1" and result["created"] is False
    assert result["client"]["code"] == "K1"
    assert "name" not in result["client"]
    assert any("too large" in w for w in result["warnings"])
    assert budget.fits(result)


def test_9_a_large_invoice_keeps_its_journal_and_number() -> None:
    row = {
        "journal": "PARD",
        "op_number": 61,
        "series": "",
        "order_number": "S-1",
        "client": "K1",
        "client_title": "x" * 3_600_000,
        "currency": "EUR",
        "amount": "121.00",
        "op_date": "2026-11-10",
    }
    lines = [{"kind": "service", "code": "S1", "quantity": 1, "price": 100, "vat_percent": 21}]
    inputs = {"client_code": "K1", "document": "S-1", "date": "2026-11-10", "lines": lines}
    response = run(FakeFinvalda(GetOperations=items(row)), "create_sales_invoice", inputs)
    assert response["ok"] is True, response.get("error")
    result = response["result"]
    assert (result["journal"], result["op_number"], result["document"]) == ("PARD", 61, "S-1")
    assert result["invoice"]["journal"] == "PARD" and "client_name" not in result["invoice"]
    assert budget.fits(result)


def _main_with(script: str) -> tuple[int, dict[str, Any], int]:
    """Run the real main.py, its dispatch replaced by ``script``'s ``dispatch``."""
    code = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, "tests")
        import main
        {script}
        main.dispatch_runtime = dispatch
        raise SystemExit(main.main())
        """
    ).format(script=textwrap.dedent(script).strip())
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BUNDLE,
        input=b"{}",
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.stderr == b"", proc.stderr.decode()[-2000:]
    return proc.returncode, json.loads(proc.stdout), len(proc.stdout)


def test_9_main_runs_a_large_write_and_keeps_the_write_facts() -> None:
    code, response, _ = _main_with(
        """
        from support import FakeFinvalda, items, run
        def dispatch(payload):
            big = {"code": "K1", "title": "x" * 3600000}
            fake = FakeFinvalda(GetDescriptions=items(big))
            return run(fake, "find_or_create_client", {"code": "K1"})
        """
    )
    assert code == 0, response
    assert response["external_effect_status"] == "succeeded"
    assert response["result"]["client_code"] == "K1"


def _padded(size: int) -> str:
    """A dispatch whose serialized response (plus the newline) is exactly ``size`` bytes."""
    return f"""
        def response(pad):
            return {{"ok": True, "result": {{"id": 77, "pad": "x" * pad}}, "error_code": None,
                     "error": None, "errors": [], "external_effect_status": "succeeded",
                     "definitely_no_external_effect": False}}
        base = len(main._json_bytes(response(0))) + 1
        def dispatch(payload):
            return response({size} - base)
        """


def test_9_main_passes_a_response_exactly_at_the_limit() -> None:
    from main import MAX_SERIALIZED_RESPONSE_BYTES

    code, response, size = _main_with(_padded(MAX_SERIALIZED_RESPONSE_BYTES))
    assert size == MAX_SERIALIZED_RESPONSE_BYTES
    assert code == 0 and response["result"]["id"] == 77


def test_9_main_one_byte_over_the_limit_keeps_the_write_facts() -> None:
    from main import MAX_SERIALIZED_RESPONSE_BYTES

    code, response, _ = _main_with(_padded(MAX_SERIALIZED_RESPONSE_BYTES + 1))
    assert code == 2
    assert response["error_code"] == "response_too_large"
    assert response["external_effect_status"] == "succeeded"
    assert response["definitely_no_external_effect"] is False
    assert response["result"] == {"id": 77}
