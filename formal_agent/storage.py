"""Persist versioned investigation snapshots locally."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
        or state["schema_version"] not in (1, 2, 3, 4, 5)
    ):
        raise ValueError("Invalid or unsupported investigation schema.")
    model = state.get("model")
    unknowns = state.get("unknowns")
    if (
        not isinstance(model, dict)
        or not isinstance(model.get("source"), str)
        or not isinstance(model.get("sha256"), str)
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
    for reference in references:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256"}
            or not isinstance(reference["path"], str)
            or not reference["path"]
            or reference["path"] == model["source"]
            or reference["path"] in reference_paths
            or not isinstance(reference["sha256"], str)
            or len(reference["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in reference["sha256"])
        ):
            raise ValueError("Invalid evidence source.")
        reference_paths.add(reference["path"])
    for function in model["functions"]:
        if not isinstance(function, dict):
            raise ValueError("Invalid function observation.")
        if (
            not isinstance(function.get("name"), str)
            or type(function.get("line")) is not int
            or type(function.get("end_line")) is not int
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
            or not unknown_id
            or unknown_id in ids
            or status not in ("open", "answered")
            or (status == "open" and answer is not None)
            or (status == "answered" and (not isinstance(answer, str) or not answer.strip()))
            or any(
                not isinstance(unknown.get(key), str)
                for key in ("function", "origin", "description", "question", "impact")
            )
            or type(unknown.get("line")) is not int
            or any(
                not isinstance(unknown.get(key), list)
                or not all(isinstance(item, str) for item in unknown[key])
                for key in ("evidence", "interpretations", "affected_obligations")
            )
        ):
            raise ValueError("Invalid unknown.")
        ids.add(unknown_id)
    if state["schema_version"] == 1:
        if "properties" in state:
            raise ValueError("Schema 1 must not contain properties.")
        return
    properties = state.get("properties")
    if not isinstance(properties, list):
        raise ValueError("Invalid properties.")
    property_ids = set()
    unknowns_by_id = {unknown["id"]: unknown for unknown in unknowns}
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
