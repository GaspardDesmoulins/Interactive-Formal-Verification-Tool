"""Validate proposals from a provider without treating them as facts."""

import copy
import json
from pathlib import Path
from typing import Any, Protocol

from formal_agent.properties import next_property_id


class ReasoningProvider(Protocol):
    def propose(self, model: dict[str, Any]) -> Any:
        """Return untrusted proposed properties and unknowns."""


class JsonFileProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    def propose(self, model: dict[str, Any]) -> Any:
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)


def apply_proposals(
    state: dict[str, Any], provider: ReasoningProvider, reasoner: str
) -> tuple[int, int]:
    if not reasoner.strip():
        raise ValueError("The reasoner name must not be empty.")
    reasoner = reasoner.strip()
    model = copy.deepcopy(state["model"])
    model["investigation_context"] = copy.deepcopy({
        "unknowns": state["unknowns"],
        "properties": state["properties"],
        "links": state["links"],
        "assumptions": state["assumptions"],
        "conflicts": state["conflicts"],
    })
    result = provider.propose(model)
    if not isinstance(result, dict) or set(result) != {"source_sha256", "properties", "unknowns"}:
        raise ValueError("Proposal must contain source_sha256, properties and unknowns.")
    if result["source_sha256"] != state["model"]["sha256"]:
        raise ValueError("Proposal source hash does not match the investigation.")
    properties = result["properties"]
    unknowns = result["unknowns"]
    if not isinstance(properties, list) or not isinstance(unknowns, list):
        raise ValueError("Proposal properties and unknowns must be arrays.")
    if not properties and not unknowns:
        raise ValueError("Proposal contains no properties or unknowns.")
    functions = {
        (item["name"], item["line"])
        for item in state["model"]["functions"]
    }

    def check_item(item: Any, fields: set[str]) -> None:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("Invalid proposal fields.")
        if (
            not isinstance(item["function"], str)
            or type(item["line"]) is not int
            or (item["function"], item["line"]) not in functions
        ):
            raise ValueError("Proposal refers to an unknown function.")
        for field in fields - {"line"}:
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f"Proposal field {field} must be a non-empty string.")

    property_keys = set()
    for item in properties:
        check_item(item, {"function", "line", "statement"})
        key = (item["function"], item["line"], item["statement"].strip())
        if key in property_keys or any(
            existing["origin"] == "llm"
            and existing["reasoner"] == reasoner
            and existing["source_function"] == {"name": key[0], "line": key[1]}
            and existing["statement"] == key[2]
            for existing in state["properties"]
        ):
            raise ValueError("Duplicate proposed property.")
        property_keys.add(key)

    unknown_keys = set()
    for item in unknowns:
        check_item(item, {"function", "line", "description", "question", "impact"})
        key = (
            item["function"], item["line"],
            " ".join(item["question"].split()).casefold(),
        )
        if key in unknown_keys or any(
            existing["function"] == key[0]
            and existing["line"] == key[1]
            and " ".join(existing["question"].split()).casefold() == key[2]
            for existing in state["unknowns"]
        ):
            raise ValueError("Duplicate proposed unknown.")
        unknown_keys.add(key)

    for item in properties:
        state["properties"].append({
            "id": next_property_id(state),
            "statement": item["statement"].strip(),
            "status": "candidate",
            "origin": "llm",
            "source_function": {"name": item["function"], "line": item["line"]},
            "reasoner": reasoner,
            "accepted_by": None,
        })
    existing_ids = {item["id"] for item in state["unknowns"]}
    next_id = 1
    for item in unknowns:
        while f"U{next_id}" in existing_ids:
            next_id += 1
        unknown_id = f"U{next_id}"
        existing_ids.add(unknown_id)
        state["unknowns"].append({
            "id": unknown_id,
            "function": item["function"],
            "line": item["line"],
            "origin": f"LLM proposal ({reasoner})",
            "description": item["description"].strip(),
            "question": item["question"].strip(),
            "impact": item["impact"].strip(),
            "evidence": [],
            "interpretations": [],
            "affected_obligations": [],
            "status": "open",
            "answer": None,
        })
    return len(properties), len(unknowns)
