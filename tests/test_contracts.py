"""The committed contracts are exactly what the generator builds; their rules hold."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "tests" / "reference"))

# The generator and the runtime are imported after their folders are put on
# sys.path above, so these imports cannot be at the top of the module.
import build_contracts  # noqa: E402

from runtime import advanced, catalog, limits  # noqa: E402
from runtime.operations import neutral_result  # noqa: E402


def _load(relative: str) -> Any:
    text = (BUNDLE_ROOT / relative).read_text("utf-8")
    return json.loads(text) if relative.endswith(".json") else yaml.safe_load(text)


@pytest.fixture(scope="module")
def built() -> dict[str, Any]:
    return build_contracts.outputs()


def test_generated_files_match(built) -> None:
    assert _load("contracts/operation_manifest.yaml") == {"operations": built["operations"]}
    assert _load("contracts/step_ui_manifest.yaml") == {"forms": built["forms"]}
    assert _load("contracts/artifact_policies.yaml") == {
        "artifact_policies": built["artifact_policies"]
    }
    assert _load("ui/actions/actions.yaml") == {"actions": built["actions"]}
    assert _load("ui/pages/finvalda-api.yaml") == built["api_page"]
    assert _load("ui/pages/coverage.yaml") == built["coverage_page"]
    assert _load("ui/pages/changelog.yaml") == built["changelog_page"]
    assert _load("runtime/registry.json") == json.loads(json.dumps(built["registry"]))
    assert _load("tests/reference/coverage.json") == json.loads(json.dumps(built["coverage"]))
    assert _load("extension.yaml")["external_effects"] == built["effects"]


def test_operations_match_the_runtime_registry(built) -> None:
    ids = {op["operation_id"] for op in built["operations"]}
    assert ids == set(catalog.OPERATION_IDS)
    writes = {op["operation_id"] for op in built["operations"] if op["operation_kind"] == "action"}
    assert writes == set(catalog.WRITE_OPERATION_IDS)
    effects = {e["operation_id"]: e for e in _load("extension.yaml")["external_effects"]}
    assert set(effects) == writes
    for effect in effects.values():
        assert effect["idempotency"] == "required" and effect["test_mode_behavior"] == "suppress"
    for spec in built["specs"]:
        runtime = spec.get("runtime")
        if runtime:
            assert runtime["runner"] in advanced.RUNNERS, spec["operation_id"]


def test_neutral_test_mode_result_has_every_declared_output(built) -> None:
    for op in built["operations"]:
        if op["operation_kind"] == "action":
            result = neutral_result(op["operation_id"])
            assert set(result) == {o["name"] for o in op["outputs"]}, op["operation_id"]
            assert result["external_effect_status"] == "failed"
            assert result["definitely_no_external_effect"] is True


_WIDGETS = {
    "artifact_handle": {"artifact_picker"},
    "object": {"json"},
    "array": {"json"},
    "boolean": {"boolean"},
    "integer": {"number", "select"},
    "number": {"number"},
    "text": {"text", "textarea", "select"},
}


def test_every_input_has_a_matching_form_field(built) -> None:
    forms = {f["operation_id"]: f for f in built["forms"]}
    actions = {a["action_id"]: a for a in built["actions"]}
    for op in built["operations"]:
        oid = op["operation_id"]
        fields = {f["name"]: f for f in forms[oid]["fields"]}
        inputs = {i["name"]: i for i in op["inputs"] if i["name"] != "connection_ref"}
        assert set(fields) == set(inputs), oid
        for name, item in inputs.items():
            assert fields[name]["required"] == bool(item.get("required")), (oid, name)
            assert fields[name]["widget_type"] in _WIDGETS[item["value_type"]], (oid, name)
            if fields[name]["widget_type"] == "select":
                assert item["schema"].get("enum"), (oid, name)
                assert [o["value"] for o in fields[name]["options"]] == item["schema"]["enum"]
        assert [p["name"] for p in actions[oid]["parameters"]] == [
            i["name"] for i in op["inputs"]
        ], oid
        assert [f"result.{n}" for n in actions[oid]["result_fields"]] == op["result_fields"], oid


def test_coded_choices_show_words(built) -> None:
    for form in built["forms"]:
        for field in form["fields"]:
            for option in field.get("options", []):
                if isinstance(option["value"], int):
                    assert option["label"] != str(option["value"]), (
                        form["operation_id"],
                        field["name"],
                    )


def test_file_inputs_and_outputs_have_grants(built) -> None:
    policies = {p["operation_id"]: p for p in built["artifact_policies"]}
    for op in built["operations"]:
        if any(i["value_type"] == "artifact_handle" for i in op["inputs"]):
            assert (
                policies[op["operation_id"]]["inputs"][0]["binding_key"] == catalog.UPLOAD_BINDING
            )
        if any(o["value_type"] == "artifact_handle" for o in op["outputs"]):
            output = policies[op["operation_id"]]["outputs"][0]
            assert op["operation_id"] in catalog.FILE_OUTPUT_IDS
            if op["operation_id"] == "export_list":
                assert output["binding_key"] == catalog.EXPORT_BINDING
                assert output["content_type"] == limits.EXPORT_CONTENT_TYPE
            else:
                assert output["binding_key"] == catalog.DOWNLOAD_BINDING
                assert output["content_type"] == limits.DOWNLOAD_CONTENT_TYPE
    file_ops = {
        op["operation_id"]
        for op in built["operations"]
        if any(o["value_type"] == "artifact_handle" for o in op["outputs"])
    }
    assert file_ops == set(catalog.FILE_OUTPUT_IDS)


def test_help_texts_are_sentences(built) -> None:
    for form in built["forms"]:
        for field in form["fields"]:
            assert len(field.get("help_text", "").split()) >= 4, (
                form["operation_id"],
                field["name"],
            )


def test_step_titles_say_what_the_step_does(built) -> None:
    import step_names

    titles = [op["display_name"] for op in built["operations"]]
    # The picker is flat and searchable: no kind prefixes, a verb first, no two alike.
    for title in titles:
        assert "·" not in title and not title.startswith(("Advanced", "List", "Operations")), title
        assert title.split()[0] in step_names.VERBS, title
        assert not any(
            w.startswith(("Get", "Insert", "Edit")) and w[3:4].isupper() for w in title.split()[1:]
        ), title
    lowered = [t.lower() for t in titles]
    assert len(lowered) == len(set(lowered)), sorted(t for t in lowered if lowered.count(t) > 1)


def test_steps_come_in_picker_order(built) -> None:
    import step_names

    ids = [op["operation_id"] for op in built["operations"]]
    assert ids == step_names.order()
    assert ids[:13] == [oid for oid, _ in step_names.EVERYDAY]
    assert set(ids[:13]) == set(catalog.FRIENDLY_IDS)
    assert ids[-2:] == ["export_list", "call_method"]
    for oid in ("get_klientas", "get_klientas_e_mail", "get_klientus", "descriptions_clients"):
        assert ids.index(oid) < ids.index("descriptions_products"), oid


def test_operation_ids_did_not_change(built) -> None:
    """Workflows refer to steps by operation id: renaming the titles keeps every id."""
    coverage = json.loads((BUNDLE_ROOT / "tests" / "reference" / "coverage.json").read_text())
    named = {e["operation"] for e in coverage["entries"].values() if e.get("operation")}
    assert named <= {op["operation_id"] for op in built["operations"]}
    assert len(built["operations"]) == 173


def test_every_shipped_page_is_in_the_ui_manifest() -> None:
    refs = {page["ref"] for page in _load("ui/ui_manifest.yaml")["pages"]}
    assert refs == {f"pages/{p.name}" for p in (BUNDLE_ROOT / "ui" / "pages").glob("*.yaml")}


def test_no_page_uses_a_table_a_bare_url_or_effect_plumbing() -> None:
    for path in (BUNDLE_ROOT / "ui" / "pages").glob("*.yaml"):
        text = path.read_text("utf-8")
        assert "external_effect_status" not in text and "definitely_no_external_effect" not in text
        for component in yaml.safe_load(text)["components"]:
            for line in str(component.get("body", "")).splitlines():
                assert not line.lstrip().startswith("|"), f"{path.name}: {line}"
                assert not any(w.startswith(("http://", "https://")) for w in line.split()), (
                    f"{path.name}: {line}"
                )


def test_api_page_documents_every_operation(built) -> None:
    bodies = "\n".join(c["body"] for c in built["api_page"]["components"])
    for op in built["operations"]:
        assert f"`{op['operation_id']}`" in bodies, op["operation_id"]


def test_connection_page_matches_the_connection_type() -> None:
    from runtime.connection import CONFIG_FIELDS, SECRET_FIELDS

    page = _load("ui/pages/connection.yaml")
    form = page["components"][0]
    assert set(form["data"]["config_fields"]) == CONFIG_FIELDS
    assert set(form["data"]["secret_fields"]) == SECRET_FIELDS
    schema = _load("contracts/connection_types.yaml")["connection_types"][0]
    assert set(schema["config_schema"]["properties"]) == CONFIG_FIELDS
    assert set(schema["secret_schema"]["properties"]) == SECRET_FIELDS
    assert {f["path"] for f in form["fields"]} == CONFIG_FIELDS | SECRET_FIELDS


def test_manifest_basics() -> None:
    manifest = _load("extension.yaml")
    assert manifest["extension_id"] == "flowsteward.finvalda" and manifest["version"] == "0.1.3"
    assert manifest["egress"] == {"connection_fields": ["finvalda.base_url"]}
    assert (
        manifest["vendor"]["website_url"] == "https://github.com/Flow-Steward/ext-finance-finvalda"
    )
    assert manifest["listing"]["category"] == "finance"
    assert manifest["entrypoint"]["timeout_seconds"] == 600


def test_health_and_icon() -> None:
    import health

    report = health.check_health()
    assert report["ok"] is True and report["declared_operations"] == len(catalog.OPERATION_IDS)
    icon = (BUNDLE_ROOT / "icon.png").read_bytes()
    assert icon.startswith(b"\x89PNG\r\n\x1a\n") and len(icon) <= 100 * 1024
