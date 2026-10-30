"""Record user-defined scenarios and unverified coverage assessments."""

from pathlib import Path
from typing import Any

from formal_agent.evidence import reference_at_line


ASSESSMENTS = ("potential_gap", "reference_found", "inconclusive")


def add_scenario(
    state: dict[str, Any], property_id: str, description: str, expected: str
) -> str:
    if not description.strip() or not expected.strip():
        raise ValueError("A scenario description and expected outcome are required.")
    if not any(item["id"] == property_id for item in state["properties"]):
        raise ValueError(f"Property {property_id} does not exist.")
    next_id = max((int(item["id"][1:]) for item in state["scenarios"]), default=0) + 1
    scenario_id = f"S{next_id}"
    state["scenarios"].append({
        "id": scenario_id,
        "property_id": property_id,
        "description": description.strip(),
        "expected": expected.strip(),
        "origin": "user",
        "reviews": [],
    })
    return scenario_id


def review_scenario(
    state: dict[str, Any], scenario_id: str, assessment: str, reason: str,
    reference: Path | None, line: int | None,
) -> None:
    scenario = next((item for item in state["scenarios"] if item["id"] == scenario_id), None)
    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} does not exist.")
    if assessment not in ASSESSMENTS:
        raise ValueError(f"Invalid scenario assessment; choose from {ASSESSMENTS}.")
    if not reason.strip():
        raise ValueError("A reason for the scenario assessment is required.")
    if (reference is None) != (line is None):
        raise ValueError("--reference and --line must be provided together.")
    if line is not None and type(line) is not int:
        raise ValueError("Reference line must be an integer.")
    if assessment == "reference_found" and reference is None:
        raise ValueError("A reference and line are required for reference_found.")
    citation = None
    if reference is not None and line is not None:
        citation = reference_at_line(state["model"], reference, line)
    scenario["reviews"].append({
        "assessment": assessment,
        "reason": reason.strip(),
        "origin": "user",
        "reference": citation,
    })
