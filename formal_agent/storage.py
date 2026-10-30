"""Persist versioned investigation snapshots locally."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from formal_agent.scenarios import ASSESSMENTS


def valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def valid_reference(value: Any, references_by_path: dict[str, str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"path", "sha256", "line"}
        and isinstance(value["path"], str)
        and value["path"] in references_by_path
        and value["sha256"] == references_by_path[value["path"]]
        and type(value["line"]) is int
        and value["line"] >= 1
    )


def validate_imports(imports: Any) -> None:
    if not isinstance(imports, list) or any(
        not isinstance(item, dict)
        or type(item.get("line")) is not int
        or not isinstance(item.get("statement"), str)
        for item in imports
    ):
        raise ValueError("Invalid import observation.")


def validate(state: Any) -> None:
    if (
        not isinstance(state, dict)
        or type(state.get("schema_version")) is not int
        or state["schema_version"] not in (1, 2, 3, 4, 5, 6, 7, 8)
    ):
        raise ValueError("Invalid or unsupported investigation schema.")
    if state["schema_version"] < 8 and "scenarios" in state:
        raise ValueError("Legacy schemas must not contain scenarios.")
    model = state.get("model")
    unknowns = state.get("unknowns")
    if (
        not isinstance(model, dict)
        or not isinstance(model.get("source"), str)
        or not model["source"].strip()
        or not valid_sha256(model.get("sha256"))
        or not isinstance(model.get("functions"), list)
        or not isinstance(unknowns, list)
    ):
        raise ValueError("Invalid investigation model.")
    if "imports" in model:
        validate_imports(model["imports"])
    references = model.get("evidence_sources", [])
    if not isinstance(references, list):
        raise ValueError("Invalid evidence sources.")
    reference_paths = set()
    references_by_path = {}
    for reference in references:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256"}
            or not isinstance(reference["path"], str)
            or not reference["path"]
            or reference["path"] == model["source"]
            or reference["path"] in reference_paths
            or not valid_sha256(reference["sha256"])
        ):
            raise ValueError("Invalid evidence source.")
        reference_paths.add(reference["path"])
        references_by_path[reference["path"]] = reference["sha256"]
    function_keys = set()
    for function in model["functions"]:
        if not isinstance(function, dict):
            raise ValueError("Invalid function observation.")
        if (
            not isinstance(function.get("name"), str)
            or not function["name"].strip()
            or type(function.get("line")) is not int
            or function["line"] < 1
            or type(function.get("end_line")) is not int
            or function["end_line"] < function["line"]
            or type(function.get("async")) is not bool
            or not isinstance(function.get("parameters"), list)
            or not all(isinstance(item, str) for item in function["parameters"])
            or not isinstance(function.get("calls"), list)
            or not all(isinstance(item, str) for item in function["calls"])
            or not isinstance(function.get("return_lines"), list)
            or not all(type(item) is int for item in function["return_lines"])
            or not isinstance(function.get("raise_lines"), list)
            or not all(type(item) is int for item in function["raise_lines"])
            or (function.get("docstring") is not None
                and not isinstance(function["docstring"], str))
        ):
            raise ValueError("Invalid function observation.")
        function_key = (function["name"], function["line"])
        if function_key in function_keys:
            raise ValueError("Duplicate function observation.")
        function_keys.add(function_key)
        if "imports" in function:
            validate_imports(function["imports"])
    ids = set()
    for unknown in unknowns:
        if not isinstance(unknown, dict):
            raise ValueError("Invalid unknown.")
        unknown_id = unknown.get("id")
        status = unknown.get("status")
        answer = unknown.get("answer")
        if (
            not isinstance(unknown_id, str)
            or not unknown_id.startswith("U")
            or not unknown_id[1:].isascii()
            or not unknown_id[1:].isdigit()
            or unknown_id[1] == "0"
            or unknown_id in ids
            or status not in ("open", "answered", "duplicate")
            or (status == "duplicate" and state["schema_version"] < 6)
            or (status in ("open", "duplicate") and answer is not None)
            or (status == "answered" and (not isinstance(answer, str) or not answer.strip()))
            or any(
                not isinstance(unknown.get(key), str) or not unknown[key].strip()
                for key in ("function", "origin", "description", "question", "impact")
            )
            or type(unknown.get("line")) is not int
            or (unknown["function"], unknown["line"]) not in function_keys
            or any(
                not isinstance(unknown.get(key), list)
                or not all(isinstance(item, str) for item in unknown[key])
                for key in ("evidence", "interpretations", "affected_obligations")
            )
        ):
            raise ValueError("Invalid unknown.")
        if "answer_reference" in unknown:
            reference = unknown["answer_reference"]
            if (
                status != "answered"
                or not valid_reference(reference, references_by_path)
            ):
                raise ValueError("Invalid answer reference.")
        ids.add(unknown_id)
    unknowns_by_id = {unknown["id"]: unknown for unknown in unknowns}
    for unknown in unknowns:
        if unknown["status"] == "duplicate":
            canonical_id = unknown.get("duplicate_of")
            reason = unknown.get("duplicate_reason")
            if (
                not isinstance(canonical_id, str)
                or canonical_id == unknown["id"]
                or canonical_id not in unknowns_by_id
                or unknowns_by_id[canonical_id]["status"] not in ("open", "answered")
                or (
                    unknown["function"], unknown["line"]
                ) != (
                    unknowns_by_id[canonical_id]["function"],
                    unknowns_by_id[canonical_id]["line"],
                )
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise ValueError("Invalid duplicate decision.")
        elif "duplicate_of" in unknown or "duplicate_reason" in unknown:
            raise ValueError("Invalid duplicate decision.")
    if state["schema_version"] == 1:
        if "properties" in state:
            raise ValueError("Schema 1 must not contain properties.")
        return
    properties = state.get("properties")
    if not isinstance(properties, list):
        raise ValueError("Invalid properties.")
    property_ids = set()
    for property_item in properties:
        if not isinstance(property_item, dict):
            raise ValueError("Invalid property.")
        property_id = property_item.get("id")
        status = property_item.get("status")
        statement = property_item.get("statement")
        source_id = property_item.get("source_unknown_id")
        if (
            not isinstance(property_id, str)
            or not property_id.startswith("P")
            or not property_id[1:].isascii()
            or not property_id[1:].isdigit()
            or int(property_id[1:]) < 1
            or str(int(property_id[1:])) != property_id[1:]
            or property_id in property_ids
            or not isinstance(statement, str)
            or not statement.strip()
            or status not in ("candidate", "accepted")
            or "accepted_by" not in property_item
            or (status == "candidate" and property_item.get("accepted_by") is not None)
            or (status == "accepted" and property_item.get("accepted_by") != "user")
        ):
            raise ValueError("Invalid property or provenance.")
        if property_item.get("origin") == "user":
            if (
                not isinstance(source_id, str)
                or source_id not in unknowns_by_id
                or unknowns_by_id[source_id]["status"] != "answered"
            ):
                raise ValueError("Invalid property or provenance.")
        elif property_item.get("origin") == "llm" and state["schema_version"] >= 3:
            function = property_item.get("source_function")
            reasoner = property_item.get("reasoner")
            if (
                not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or type(function.get("line")) is not int
                or not any(
                    item["name"] == function["name"] and item["line"] == function["line"]
                    for item in model["functions"]
                )
                or not isinstance(reasoner, str)
                or not reasoner.strip()
            ):
                raise ValueError("Invalid property or provenance.")
        else:
            raise ValueError("Invalid property or provenance.")
        property_ids.add(property_id)
    if state["schema_version"] < 4:
        if "links" in state or "assumptions" in state:
            raise ValueError("Legacy schemas must not contain links or assumptions.")
        return
    links = state.get("links")
    assumptions = state.get("assumptions")
    if not isinstance(links, list) or not isinstance(assumptions, list):
        raise ValueError("Invalid links or assumptions.")
    properties_by_id = {item["id"]: item for item in properties}
    link_ids = set()
    for link in links:
        if not isinstance(link, dict) or set(link) != {"unknown_id", "property_id"}:
            raise ValueError("Invalid property link.")
        unknown_id = link["unknown_id"]
        property_id = link["property_id"]
        if (
            not isinstance(unknown_id, str)
            or not isinstance(property_id, str)
            or unknown_id not in unknowns_by_id
            or property_id not in properties_by_id
            or (unknown_id, property_id) in link_ids
        ):
            raise ValueError("Invalid property link.")
        unknown = unknowns_by_id[unknown_id]
        prop = properties_by_id[property_id]
        if prop["origin"] == "user":
            source = unknowns_by_id[prop["source_unknown_id"]]
            target = (source["function"], source["line"])
        else:
            target = (prop["source_function"]["name"], prop["source_function"]["line"])
        if (unknown["function"], unknown["line"]) != target:
            raise ValueError("Property link refers to different functions.")
        link_ids.add((unknown_id, property_id))
    for prop in properties:
        if prop["origin"] == "user" and (prop["source_unknown_id"], prop["id"]) not in link_ids:
            raise ValueError("User property is missing its source link.")
    assumption_ids = set()
    for assumption in assumptions:
        if not isinstance(assumption, dict):
            raise ValueError("Invalid assumption.")
        assumption_id = assumption.get("id")
        statement = assumption.get("statement")
        unknown_id = assumption.get("source_unknown_id")
        property_id = assumption.get("property_id")
        if (
            not isinstance(assumption_id, str)
            or not assumption_id.startswith("A")
            or not assumption_id[1:].isascii()
            or not assumption_id[1:].isdigit()
            or int(assumption_id[1:]) < 1
            or str(int(assumption_id[1:])) != assumption_id[1:]
            or assumption_id in assumption_ids
            or not isinstance(statement, str)
            or not statement.strip()
            or assumption.get("status") != "assumed"
            or assumption.get("origin") != "user"
            or not isinstance(unknown_id, str)
            or not isinstance(property_id, str)
            or (unknown_id, property_id) not in link_ids
            or unknowns_by_id[unknown_id]["status"] != "answered"
        ):
            raise ValueError("Invalid assumption or provenance.")
        assumption_ids.add(assumption_id)
    if state["schema_version"] == 4:
        if "workflow" in state:
            raise ValueError("Legacy schema must not contain workflow.")
        return
    workflow = state.get("workflow")
    if not isinstance(workflow, dict) or set(workflow) != {"phase", "pending_unknown_id"}:
        raise ValueError("Invalid investigation workflow.")
    phase = workflow["phase"]
    pending = workflow["pending_unknown_id"]
    if (
        phase not in ("active", "awaiting_answer", "finished")
        or (phase != "awaiting_answer" and pending is not None)
        or (
            phase == "awaiting_answer"
            and (
                not isinstance(pending, str)
                or pending not in unknowns_by_id
                or unknowns_by_id[pending]["status"] != "open"
            )
        )
    ):
        raise ValueError("Invalid investigation workflow.")
    if state["schema_version"] < 7:
        if "conflicts" in state:
            raise ValueError("Legacy schema must not contain disagreements.")
        return
    conflicts = state.get("conflicts")
    if not isinstance(conflicts, list):
        raise ValueError("Invalid disagreements.")
    conflict_ids = set()
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            raise ValueError("Invalid disagreement.")
        conflict_id = conflict.get("id")
        unknown_id = conflict.get("unknown_id")
        description = conflict.get("description")
        status = conflict.get("status")
        resolution = conflict.get("resolution")
        if (
            not isinstance(conflict_id, str)
            or not conflict_id.startswith("C")
            or not conflict_id[1:].isascii()
            or not conflict_id[1:].isdigit()
            or int(conflict_id[1:]) < 1
            or str(int(conflict_id[1:])) != conflict_id[1:]
            or conflict_id in conflict_ids
            or not isinstance(unknown_id, str)
            or unknown_id not in unknowns_by_id
            or not isinstance(description, str)
            or not description.strip()
            or conflict.get("origin") != "user"
            or status not in ("open", "resolved")
            or (status == "open" and resolution is not None)
            or (status == "resolved" and (not isinstance(resolution, str) or not resolution.strip()))
        ):
            raise ValueError("Invalid disagreement.")
        conflict_ids.add(conflict_id)
    if state["schema_version"] == 7:
        return
    scenarios = state.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("Invalid scenarios.")
    scenario_ids = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {
            "id", "property_id", "description", "expected", "origin", "reviews"
        }:
            raise ValueError("Invalid scenario.")
        scenario_id = scenario["id"]
        property_id = scenario["property_id"]
        if (
            not isinstance(scenario_id, str)
            or not scenario_id.startswith("S")
            or not scenario_id[1:].isascii()
            or not scenario_id[1:].isdigit()
            or scenario_id[1] == "0"
            or scenario_id in scenario_ids
            or not isinstance(property_id, str)
            or property_id not in properties_by_id
            or not isinstance(scenario["description"], str)
            or not scenario["description"].strip()
            or not isinstance(scenario["expected"], str)
            or not scenario["expected"].strip()
            or scenario["origin"] != "user"
            or not isinstance(scenario["reviews"], list)
        ):
            raise ValueError("Invalid scenario or provenance.")
        scenario_ids.add(scenario_id)
        for review in scenario["reviews"]:
            if (
                not isinstance(review, dict)
                or set(review) != {"assessment", "reason", "origin", "reference"}
                or review["assessment"] not in ASSESSMENTS
                or not isinstance(review["reason"], str)
                or not review["reason"].strip()
                or review["origin"] != "user"
                or (review["assessment"] == "reference_found" and review["reference"] is None)
                or (
                    review["reference"] is not None
                    and not valid_reference(review["reference"], references_by_path)
                )
            ):
                raise ValueError("Invalid scenario review or reference.")


def load_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    state = json.loads(raw.decode("utf-8"))
    validate(state)
    if state["schema_version"] == 1:
        state["schema_version"] = 2
        state["properties"] = []
    if state["schema_version"] == 2:
        state["schema_version"] = 3
    if state["schema_version"] == 3:
        state["schema_version"] = 4
        state["links"] = [
            {"unknown_id": item["source_unknown_id"], "property_id": item["id"]}
            for item in state["properties"] if item["origin"] == "user"
        ]
        state["assumptions"] = []
    if state["schema_version"] == 4:
        state["schema_version"] = 5
        state["workflow"] = {"phase": "active", "pending_unknown_id": None}
    if state["schema_version"] == 5:
        state["schema_version"] = 6
    if state["schema_version"] == 6:
        state["schema_version"] = 7
        state["conflicts"] = []
    if state["schema_version"] == 7:
        state["schema_version"] = 8
        state["scenarios"] = []
    return state, hashlib.sha256(raw).hexdigest()


def load(path: Path) -> dict[str, Any]:
    state, _ = load_snapshot(path)
    return state


def create(path: Path, state: dict[str, Any]) -> None:
    validate(state)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")


def save(path: Path, state: dict[str, Any], expected_revision: str) -> None:
    validate(state)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.write("\n")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_revision:
            raise ValueError("Investigation changed while processing; changes were not saved.")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
