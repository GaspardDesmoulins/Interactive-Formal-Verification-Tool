"""Suggest parameter boundary scenarios and record explicit user decisions."""

import re
from pathlib import Path
from typing import Any

from formal_agent.evidence import read_evidence
from formal_agent.scenarios import add_scenario


VALUES = ("0", "None")
MAX_SUGGESTIONS = 64
MAX_MATCHES = 5


def property_function(state: dict[str, Any], property_item: dict[str, Any]) -> dict[str, Any]:
    if property_item["origin"] == "user":
        source = next(
            item for item in state["unknowns"] if item["id"] == property_item["source_unknown_id"]
        )
        key = (source["function"], source["line"])
    else:
        source = property_item["source_function"]
        key = (source["name"], source["line"])
    return next(
        function for function in state["model"]["functions"]
        if (function["name"], function["line"]) == key
    )


def suggestion_description(suggestion: dict[str, Any]) -> str:
    return (
        f"Parameter {suggestion['parameter']} of {suggestion['function']['name']} "
        f"is {suggestion['value']}; other inputs unspecified."
    )


def suggest_scenarios(state: dict[str, Any], property_id: str) -> tuple[int, int]:
    prop = next((item for item in state["properties"] if item["id"] == property_id), None)
    if prop is None:
        raise ValueError(f"Property {property_id} does not exist.")
    function = property_function(state, prop)
    parameters = [item for item in function["parameters"] if item.isidentifier()]
    if not parameters:
        raise ValueError("The property function has no supported named parameters.")
    if len(parameters) * len(VALUES) > MAX_SUGGESTIONS:
        raise ValueError(f"More than {MAX_SUGGESTIONS} suggestions would be generated.")
    existing = {
        (item["property_id"], item["parameter"], item["value"])
        for item in state["scenario_suggestions"]
    }
    candidates = [
        (parameter, value) for parameter in parameters for value in VALUES
        if (property_id, parameter, value) not in existing
    ]
    if not candidates:
        return 0, len(parameters) * len(VALUES)
    references = []
    for reference in state["model"].get("evidence_sources", []):
        text, digest = read_evidence(Path(reference["path"]))
        if digest != reference["sha256"]:
            raise ValueError(f"Evidence changed during scenario search: {reference['path']}")
        references.append((reference, text.splitlines()))
    next_id = max(
        (int(item["id"][1:]) for item in state["scenario_suggestions"]), default=0
    ) + 1
    new_items = []
    for parameter, value in candidates:
        parameter_pattern = re.compile(r"(?<!\w)" + re.escape(parameter) + r"(?!\w)")
        value_pattern = re.compile(
            r"(?<![\w.])0(?![\w.])" if value == "0" else r"(?<!\w)None(?!\w)"
        )
        matches = []
        truncated = False
        for reference, lines in references:
            for line_number, line in enumerate(lines, 1):
                if parameter_pattern.search(line) and value_pattern.search(line):
                    if len(matches) >= MAX_MATCHES:
                        truncated = True
                    else:
                        matches.append({**reference, "line": line_number})
        new_items.append({
            "id": f"T{next_id}",
            "property_id": property_id,
            "function": {"name": function["name"], "line": function["line"]},
            "parameter": parameter,
            "value": value,
            "evidence_scope": [dict(reference) for reference, _ in references],
            "matches": matches,
            "matches_truncated": truncated,
            "decisions": [],
        })
        next_id += 1
    state["scenario_suggestions"].extend(new_items)
    return len(new_items), len(parameters) * len(VALUES) - len(new_items)


def decide_suggestion(
    state: dict[str, Any], suggestion_id: str, decision: str, reason: str,
    expected: str | None = None, description: str | None = None,
) -> str | None:
    suggestion = next(
        (item for item in state["scenario_suggestions"] if item["id"] == suggestion_id), None
    )
    if suggestion is None:
        raise ValueError(f"Scenario suggestion {suggestion_id} does not exist.")
    if suggestion["decisions"] and suggestion["decisions"][-1]["decision"] == "accepted":
        raise ValueError(f"Scenario suggestion {suggestion_id} is already accepted.")
    if decision not in ("accepted", "rejected") or not reason.strip():
        raise ValueError("A valid decision and non-empty reason are required.")
    scenario_id = None
    if decision == "accepted":
        if expected is None or not expected.strip():
            raise ValueError("An expected outcome is required to accept a suggestion.")
        if description is not None and not description.strip():
            raise ValueError("A replacement description must not be empty.")
        scenario_id = add_scenario(
            state, suggestion["property_id"],
            description if description is not None else suggestion_description(suggestion),
            expected,
        )
    elif expected is not None or description is not None:
        raise ValueError("A rejected suggestion must not include a scenario.")
    suggestion["decisions"].append({
        "decision": decision,
        "origin": "user",
        "reason": reason.strip(),
        "scenario_id": scenario_id,
    })
    return scenario_id
