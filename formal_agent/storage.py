"""Persist versioned investigation snapshots locally."""

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
        or state["schema_version"] not in (1, 2)
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
            or property_item.get("origin") != "user"
            or not isinstance(source_id, str)
            or source_id not in unknowns_by_id
            or unknowns_by_id[source_id]["status"] != "answered"
            or "accepted_by" not in property_item
            or (status == "candidate" and property_item.get("accepted_by") is not None)
            or (status == "accepted" and property_item.get("accepted_by") != "user")
        ):
            raise ValueError("Invalid property or provenance.")
        property_ids.add(property_id)


def load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    validate(state)
    if state["schema_version"] == 1:
        state["schema_version"] = 2
        state["properties"] = []
    return state


def create(path: Path, state: dict[str, Any]) -> None:
    validate(state)
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")


def save(path: Path, state: dict[str, Any]) -> None:
    validate(state)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
