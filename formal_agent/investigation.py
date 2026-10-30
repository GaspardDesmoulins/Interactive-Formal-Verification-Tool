"""Build and update a small, explicit investigation state."""

import hashlib
from pathlib import Path
from typing import Any

from formal_agent.evidence import evidence_warning


def create_investigation(model: dict[str, Any]) -> dict[str, Any]:
    unknowns = []
    for function in model["functions"]:
        unknowns.append({
            "id": f"U{len(unknowns) + 1}",
            "function": function["name"],
            "line": function["line"],
            "origin": "function signature",
            "description": "The intended behavior is not established.",
            "question": (
                f"What behavior is required for {function['name']} "
                f"at line {function['line']}?"
            ),
            "impact": "A specification cannot be accepted from syntax alone.",
            "evidence": ["Function signature extracted from source."],
            "interpretations": [],
            "affected_obligations": [],
            "status": "open",
            "answer": None,
        })
        for parameter in function["parameters"]:
            unknowns.append({
                "id": f"U{len(unknowns) + 1}",
                "function": function["name"],
                "line": function["line"],
                "origin": "function signature",
                "description": f"The valid values and constraints for {parameter} are unknown.",
                "question": (
                    f"What values and constraints are required for parameter {parameter} "
                    f"of {function['name']} at line {function['line']}?"
                ),
                "impact": "The input requirements cannot be accepted from syntax alone.",
                "evidence": [f"Parameter {parameter} extracted from function signature."],
                "interpretations": [],
                "affected_obligations": [],
                "status": "open",
                "answer": None,
            })
    return {
        "schema_version": 7,
        "model": model,
        "unknowns": unknowns,
        "properties": [],
        "links": [],
        "assumptions": [],
        "workflow": {"phase": "active", "pending_unknown_id": None},
        "conflicts": [],
    }


def source_warning(state: dict[str, Any]) -> str | None:
    path = Path(state["model"]["source"])
    try:
        content = path.read_bytes()
    except OSError as error:
        return f"Source cannot be read ({path}): {error}"
    if hashlib.sha256(content).hexdigest() != state["model"]["sha256"]:
        return f"Source has changed since this investigation was created: {path}"
    return evidence_warning(state["model"])


def resolve_unknown(state: dict[str, Any], unknown_id: str, answer: str) -> None:
    if not answer.strip():
        raise ValueError("The answer must not be empty.")
    for unknown in state["unknowns"]:
        if unknown["id"] == unknown_id:
            if unknown["status"] != "open":
                raise ValueError(f"Unknown {unknown_id} is already resolved.")
            unknown["answer"] = answer.strip()
            unknown["status"] = "answered"
            return
    raise ValueError(f"Unknown {unknown_id} does not exist.")
