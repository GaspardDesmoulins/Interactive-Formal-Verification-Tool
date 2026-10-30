"""Validate proposals from a provider without treating them as facts."""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from formal_agent.duplicates import question_key
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


@dataclass
class ProposalResult:
    properties_added: int = 0
    unknowns_added: int = 0
    duplicate_properties: list[tuple[int, str]] = field(default_factory=list)
    duplicate_unknowns: list[tuple[int, str]] = field(default_factory=list)
    similar_unknowns: list[tuple[str, str]] = field(default_factory=list)


def apply_proposals(
    state: dict[str, Any], provider: ReasoningProvider, reasoner: str
) -> ProposalResult:
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
        "scenarios": state["scenarios"],
        "scenario_suggestions": state["scenario_suggestions"],
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

    for item in properties:
        check_item(item, {"function", "line", "statement"})
    for item in unknowns:
        check_item(item, {"function", "line", "description", "question", "impact"})

    applied = ProposalResult()
    for index, item in enumerate(properties, 1):
        source_function = {"name": item["function"], "line": item["line"]}
        statement = item["statement"].strip()
        existing = next((
            candidate for candidate in state["properties"]
            if candidate["origin"] == "llm"
            and candidate["reasoner"] == reasoner
            and candidate["source_function"] == source_function
            and candidate["statement"] == statement
        ), None)
        if existing is not None:
            applied.duplicate_properties.append((index, existing["id"]))
            continue
        state["properties"].append({
            "id": next_property_id(state),
            "statement": statement,
            "status": "candidate",
            "origin": "llm",
            "source_function": source_function,
            "reasoner": reasoner,
            "accepted_by": None,
        })
        applied.properties_added += 1
    existing_ids = {item["id"] for item in state["unknowns"]}
    next_id = 1
    for index, item in enumerate(unknowns, 1):
        fields = ("description", "question", "impact")
        same_function = [
            existing for existing in state["unknowns"]
            if existing["function"] == item["function"] and existing["line"] == item["line"]
        ]
        existing = next((
            candidate for candidate in same_function
            if all(candidate[name] == item[name].strip() for name in fields)
        ), None)
        if existing is not None:
            applied.duplicate_unknowns.append((index, existing["id"]))
            continue
        while f"U{next_id}" in existing_ids:
            next_id += 1
        unknown_id = f"U{next_id}"
        existing_ids.add(unknown_id)
        key = question_key(item)
        applied.similar_unknowns.extend(
            (unknown_id, candidate["id"])
            for candidate in same_function
            if question_key(candidate) == key
        )
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
        applied.unknowns_added += 1
    return applied
