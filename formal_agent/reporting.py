"""Render a deterministic report without overstating verification."""

import json
from typing import Any

from formal_agent.investigation import source_warning


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_report(state: dict[str, Any]) -> str:
    model = state["model"]
    warning = source_warning(state)
    lines = [
        "Investigation report",
        "====================",
        f"Source: {quote(model['source'])}",
        f"Analyzed SHA-256: {quote(model['sha256'])}",
        f"Source status: {quote(warning) if warning else 'matches analyzed snapshot'}",
        "",
        "Scope and limitations",
        "- Syntax-only analysis of top-level Python functions and explicit imports.",
        "- Calls and imports are observations, not resolved or necessarily executed dependencies.",
        "- No target code was executed; methods and nested function bodies are not analyzed.",
        "- No verification engine or tests of the target were run.",
        "- Accepted properties are specifications, not proofs.",
        "",
        f"Functions ({len(model['functions'])})",
    ]
    for function in model["functions"]:
        parameters = ", ".join(quote(parameter) for parameter in function["parameters"])
        lines.append(
            f"- {quote(function['name'])} at line {function['line']}; "
            f"parameters: [{parameters}]"
        )
    lines.extend(["", f"Unknowns ({len(state['unknowns'])})"])
    for unknown in state["unknowns"]:
        lines.append(
            f"- {quote(unknown['id'])} [{unknown['status']}] "
            f"{quote(unknown['function'])}:{unknown['line']}"
        )
        lines.append(f"  Question: {quote(unknown['question'])}")
        if unknown["status"] == "answered":
            lines.append(f"  User answer: {quote(unknown['answer'])}")
        else:
            lines.append("  User answer: not provided")
    lines.extend(["", f"Properties ({len(state['properties'])})"])
    for property_item in state["properties"]:
        lines.append(
            f"- {property_item['id']} [{property_item['status']}] "
            f"{quote(property_item['statement'])}"
        )
        lines.append(
            f"  Origin: user; source answer: {quote(property_item['source_unknown_id'])}"
        )
        if property_item["status"] == "accepted":
            lines.append("  Accepted by: user; verification: not performed")
        else:
            lines.append("  Acceptance: pending; verification: not performed")
    lines.extend(["", "Verification results: none", ""])
    return "\n".join(lines)
