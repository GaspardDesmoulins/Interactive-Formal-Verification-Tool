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
        f"Investigation phase: {state['workflow']['phase']}",
        f"Pending question: {quote(state['workflow']['pending_unknown_id']) if state['workflow']['pending_unknown_id'] else 'none'}",
        "",
        "Scope and limitations",
        "- Syntax-only analysis of top-level Python functions and explicit imports.",
        "- Calls and imports are observations, not resolved or necessarily executed dependencies.",
        "- No target code was executed; methods and nested function bodies are not analyzed.",
        "- No verification engine or tests of the target were run.",
        "- Accepted properties are specifications, not proofs.",
        "",
        f"Module imports ({len(model.get('imports', []))})",
    ]
    for imported in model.get("imports", []):
        lines.append(f"- line {imported['line']}: {quote(imported['statement'])}")
    lines.extend(["", f"Functions ({len(model['functions'])})"])
    for function in model["functions"]:
        parameters = ", ".join(quote(parameter) for parameter in function["parameters"])
        lines.append(
            f"- {quote(function['name'])} at line {function['line']}; "
            f"parameters: [{parameters}]"
        )
        lines.append(f"  Calls: {', '.join(quote(call) for call in function['calls']) or 'none'}")
        for imported in function.get("imports", []):
            lines.append(f"  Import at line {imported['line']}: {quote(imported['statement'])}")
        lines.append(f"  Return lines: {', '.join(map(str, function['return_lines'])) or 'none'}")
        lines.append(f"  Raise lines: {', '.join(map(str, function['raise_lines'])) or 'none'}")
        if function["docstring"] is not None:
            lines.append(f"  Docstring (unverified): {quote(function['docstring'])}")
    evidence = model.get("evidence_sources", [])
    lines.extend(["", f"Reference files ({len(evidence)})"])
    for item in evidence:
        lines.append(f"- {quote(item['path'])}; SHA-256: {quote(item['sha256'])}")
    if evidence:
        lines.append("  References are untrusted text, not confirmed requirements.")
    lines.extend(["", f"Unknowns ({len(state['unknowns'])})"])
    for unknown in state["unknowns"]:
        lines.append(
            f"- {quote(unknown['id'])} [{unknown['status']}] "
            f"{quote(unknown['function'])}:{unknown['line']}"
        )
        lines.append(f"  Origin: {quote(unknown['origin'])}")
        lines.append(f"  Description: {quote(unknown['description'])}")
        lines.append(f"  Question: {quote(unknown['question'])}")
        lines.append(f"  Impact: {quote(unknown['impact'])}")
        related = [
            link["property_id"] for link in state["links"] if link["unknown_id"] == unknown["id"]
        ]
        lines.append(f"  Related properties: {', '.join(related) if related else 'none recorded'}")
        if unknown["status"] == "answered":
            lines.append(f"  User answer: {quote(unknown['answer'])}")
            reference = unknown.get("answer_reference")
            if reference is not None:
                lines.append(
                    f"  User-cited reference: {quote(reference['path'])}:{reference['line']}; "
                    f"SHA-256: {quote(reference['sha256'])}; interpretation not verified"
                )
        elif unknown["status"] == "duplicate":
            lines.append(
                f"  User-marked duplicate of: {quote(unknown['duplicate_of'])}; "
                f"reason: {quote(unknown['duplicate_reason'])}"
            )
        else:
            lines.append("  User answer: not provided")
    lines.extend(["", f"Properties ({len(state['properties'])})"])
    for property_item in state["properties"]:
        lines.append(
            f"- {property_item['id']} [{property_item['status']}] "
            f"{quote(property_item['statement'])}"
        )
        if property_item["origin"] == "user":
            lines.append(
                f"  Origin: user; source answer: {quote(property_item['source_unknown_id'])}"
            )
        else:
            function = property_item["source_function"]
            lines.append(
                f"  Origin: LLM proposal; reasoner: {quote(property_item['reasoner'])}; "
                f"source: {quote(function['name'])}:{function['line']}"
            )
        if property_item["status"] == "accepted":
            lines.append("  Accepted by: user; verification: not performed")
        else:
            lines.append("  Acceptance: pending; verification: not performed")
    lines.extend(["", f"Assumptions ({len(state['assumptions'])})"])
    for assumption in state["assumptions"]:
        lines.append(
            f"- {assumption['id']} [assumed] {quote(assumption['statement'])}"
        )
        lines.append(
            f"  Origin: user; source answer: {quote(assumption['source_unknown_id'])}; "
            f"property: {assumption['property_id']}; verification: not performed"
        )
    lines.extend(["", "Verification results: none", ""])
    return "\n".join(lines)
