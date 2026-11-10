"""Every name Finvalda documents is offered by a step, or is listed with the reason it is not.

The reference (``tests/reference/fvs_reference.json``) is extracted from the
pinned Postman collection (committed) and the pinned manual (by SHA-256). When
the manual is available (``FINVALDA_MANUAL`` names it), the extraction is run
again and must give the committed reference.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REFERENCE = Path(__file__).resolve().parent / "reference"
sys.path.insert(0, str(REFERENCE))

import build_contracts  # noqa: E402
import extract_reference  # noqa: E402

from runtime import catalog  # noqa: E402
from runtime.reference_data import CALLABLE_METHODS, CLASSES, OPERATIONS  # noqa: E402

COVERAGE = json.loads((REFERENCE / "coverage.json").read_text("utf-8"))
REF = json.loads((REFERENCE / "fvs_reference.json").read_text("utf-8"))


def test_sources_are_the_pinned_ones() -> None:
    postman = REFERENCE / "sources" / "finvalda_postman_collection.json"
    assert hashlib.sha256(postman.read_bytes()).hexdigest() == extract_reference.POSTMAN_SHA256
    assert REF["sources"]["postman"]["sha256"] == extract_reference.POSTMAN_SHA256
    assert REF["sources"]["manual"]["sha256"] == extract_reference.MANUAL_SHA256
    assert REF["manual"]["meta"]["revision"] == "110"


def test_postman_part_of_the_reference_is_current() -> None:
    data = json.loads(
        (REFERENCE / "sources" / "finvalda_postman_collection.json").read_text("utf-8")
    )
    assert extract_reference.parse_postman(data) == REF["postman"]


@pytest.mark.skipif(not os.environ.get("FINVALDA_MANUAL"), reason="the manual is not committed")
def test_manual_part_of_the_reference_is_current() -> None:
    manual = Path(os.environ["FINVALDA_MANUAL"])
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == extract_reference.MANUAL_SHA256
    assert extract_reference.parse_manual(manual.read_text("utf-8")) == REF["manual"]


def _entry(key: str) -> dict:
    assert key in COVERAGE["entries"], f"{key} is missing from the coverage map"
    return COVERAGE["entries"][key]


def test_every_documented_method_is_mapped() -> None:
    manual = REF["manual"]
    sectioned = [
        n
        for n, m in manual["methods"].items()
        if not m["section"].startswith("4.") or n == "GetDescriptions"
    ]
    for name in [*sectioned, *manual["listed_methods"], *REF["postman"]["endpoints"]]:
        entry = _entry(f"method:{name}")
        assert entry["operation"] or entry["reason"], name


def test_every_xml_twin_names_its_json_step() -> None:
    twins = [n for n in REF["manual"]["listed_methods"] if n.endswith("Xml")]
    assert len(twins) >= 50
    for name in twins:
        entry = _entry(f"method:{name}")
        assert entry["reason"].startswith("The same data as"), name
        assert entry["operation"] in OPERATIONS, name


def test_every_list_and_operation_class_has_its_own_step() -> None:
    types = set(REF["manual"]["descriptions"]) | set(REF["postman"]["descriptions"])
    for kind in types:
        entry = _entry(f"GetDescriptions:{kind}")
        assert OPERATIONS[entry["operation"]]["type"] == kind
    classes = set(REF["manual"]["get_operations"]["op_classes"]) | set(REF["postman"]["operations"])
    for op_class in classes:
        entry = _entry(f"GetOperations:{op_class}")
        assert OPERATIONS[entry["operation"]]["op_class"] == op_class


def test_every_record_and_operation_class_is_writable() -> None:
    for cls in REF["manual"]["classes"]:
        entry = _entry(f"class:{cls}")
        assert entry["operation"] in {
            "insert_item",
            "edit_item_props",
            "insert_operation",
            "update_operation",
            "delete_operation",
        }
        assert cls in CLASSES


def test_every_mapped_operation_exists_and_unmapped_names_say_why() -> None:
    for key, entry in COVERAGE["entries"].items():
        if entry["operation"] is None:
            assert entry["reason"].startswith("Not offered"), key
        else:
            assert entry["operation"] in catalog.OPERATION_IDS, key


def test_call_method_accepts_every_offered_method_and_not_the_excluded_one() -> None:
    offered = {
        k.split(":", 1)[1]
        for k, e in COVERAGE["entries"].items()
        if k.startswith("method:") and e["operation"] and not e["reason"]
    }
    assert offered <= CALLABLE_METHODS
    assert "GetFvsUser" not in CALLABLE_METHODS


def test_every_legacy_read_sends_the_manuals_parameters() -> None:
    methods = REF["manual"]["methods"]
    for spec in OPERATIONS.values():
        if spec["runner"] != "method_read":
            continue
        wires = [p[1] for p in spec["params"]]
        documented = [w for w in methods[spec["method"]]["params"] if w != "writeSchema"]
        assert set(documented) <= set(wires), spec["method"]


def test_generator_and_reference_agree() -> None:
    built = build_contracts.outputs()
    assert json.loads(json.dumps(built["coverage"])) == COVERAGE
