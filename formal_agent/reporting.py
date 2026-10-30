"""Render a deterministic report without overstating verification."""

import json
from typing import Any

from formal_agent.investigation import source_warning
from formal_agent.scenario_suggestions import suggestion_description
from formal_agent.reference_calls import suggestion_matches


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
        "- Scenario assessments and citations do not establish coverage or gaps.",
        "- Suggestions are limited boundary templates, not an exhaustive scenario search.",
        "- Static call observations do not resolve targets or establish execution or coverage.",
        "- LLM context completeness is not independently verified; servers may truncate inputs.",
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
    lines.extend(["", f"Candidate scenarios ({len(state['scenarios'])})"])
    for scenario in state["scenarios"]:
        reviews = scenario["reviews"]
        status = f"user assessment: {reviews[-1]['assessment']}" if reviews else "not assessed"
        lines.append(f"- {scenario['id']} [{status}] {quote(scenario['description'])}")
        lines.append(
            f"  Origin: user; property: {scenario['property_id']}; "
            f"expected: {quote(scenario['expected'])}"
        )
        source_suggestion = next((
            item["id"] for item in state["scenario_suggestions"]
            if item["decisions"] and item["decisions"][-1]["scenario_id"] == scenario["id"]
        ), None)
        if source_suggestion is not None:
            lines.append(f"  Accepted from suggestion: {source_suggestion}; relevance not verified")
        related_analyses = [
            item["id"] for item in state["test_call_analyses"]
            if item["property_id"] == scenario["property_id"]
        ]
        if related_analyses:
            lines.append(
                f"  Static analyses for the same property: {', '.join(related_analyses)}; "
                "free-text scenario not automatically matched"
            )
        for index, review in enumerate(reviews, 1):
            lines.append(
                f"  Review {index}: {review['assessment']}; user reason: "
                f"{quote(review['reason'])}; not verified"
            )
            reference = review["reference"]
            if reference is not None:
                lines.append(
                    f"    User-cited reference: {quote(reference['path'])}:{reference['line']}; "
                    f"SHA-256: {quote(reference['sha256'])}; relevance not verified"
                )
    lines.extend(["", f"Scenario suggestions ({len(state['scenario_suggestions'])})"])
    for suggestion in state["scenario_suggestions"]:
        decisions = suggestion["decisions"]
        status = decisions[-1]["decision"] if decisions else "pending review"
        lines.append(
            f"- {suggestion['id']} [{status}] {quote(suggestion_description(suggestion))}"
        )
        lines.append(
            f"  Origin: parameter boundary template; property: {suggestion['property_id']}; "
            f"function: {quote(suggestion['function']['name'])}:{suggestion['function']['line']}"
        )
        lines.append("  Expected outcome, applicability and coverage: not established")
        count = len(suggestion["evidence_scope"])
        lines.append(f"  Declared references searched: {count}; same-line literal text match only")
        if not count:
            lines.append("  No references were declared; no reference search was possible.")
        elif not suggestion["matches"]:
            lines.append("  No same-line text match found; this is not evidence of a coverage gap.")
        for match in suggestion["matches"]:
            lines.append(
                f"  Text match: {quote(match['path'])}:{match['line']}; "
                f"SHA-256: {quote(match['sha256'])}; relevance and coverage not verified"
            )
        if suggestion["matches_truncated"]:
            lines.append("  Additional text matches omitted; the displayed list is limited.")
        matching_calls = suggestion_matches(state, suggestion)
        if not matching_calls and any(
            item["property_id"] == suggestion["property_id"]
            for item in state["test_call_analyses"]
        ):
            lines.append(
                "  No syntactic argument match found; this is not evidence of a coverage gap."
            )
        for analysis_id, call, argument in matching_calls:
            reference = call["reference"]
            lines.append(
                f"  Possible syntactic match: {analysis_id}, {quote(reference['path'])}:"
                f"{reference['line']}; argument {quote(argument['expression'])}; "
                "target and coverage not verified"
            )
        for index, decision in enumerate(decisions, 1):
            lines.append(
                f"  Decision {index}: {decision['decision']}; user reason: "
                f"{quote(decision['reason'])}; scenario: {decision['scenario_id'] or 'none'}"
            )
    lines.extend(["", f"Static call analyses ({len(state['test_call_analyses'])})"])
    for analysis in state["test_call_analyses"]:
        lines.append(
            f"- {analysis['id']} property {analysis['property_id']}; "
            f"method: {analysis['method']}; Python references: {len(analysis['evidence_scope'])}"
        )
        lines.append(
            "  Same-name calls only; target resolution, test collection and execution are unknown."
        )
        lines.append(
            "  Variables, fixtures, indirect calls, aliases and dynamic parametrization are not resolved."
        )
        lines.append(
            "  Parameter names are hypothetical mappings to the property function, not bindings."
        )
        if not analysis["calls"]:
            lines.append("  No same-name calls found; this is not evidence of a coverage gap.")
        for call in analysis["calls"]:
            reference = call["reference"]
            lines.append(
                f"  {quote(reference['path'])}:{reference['line']}; "
                f"SHA-256: {quote(reference['sha256'])}; "
                f"{call['kind']} call {quote(call['callee'])}"
            )
            for argument in call["arguments"]:
                parameter = quote(argument["parameter"]) if argument["parameter"] is not None else "unknown"
                position = (
                    f"position {argument['position']}" if argument["position"] is not None
                    else f"keyword {quote(argument['keyword'])}" if argument["keyword"] is not None
                    else "keyword unpack"
                )
                lines.append(
                    f"    {argument['kind']} {position}; expression "
                    f"{quote(argument['expression'])}; {argument['value_kind']}; "
                    f"possible parameter: {parameter}"
                )
    lines.extend(["", f"Disagreements ({len(state['conflicts'])})"])
    for conflict in state["conflicts"]:
        lines.append(
            f"- {conflict['id']} [{conflict['status']}] {quote(conflict['description'])}"
        )
        lines.append(f"  Origin: user; unknown: {quote(conflict['unknown_id'])}")
        if conflict["status"] == "resolved":
            lines.append(f"  User decision: {quote(conflict['resolution'])}; not verified")
    lines.extend(["", "Verification results: none", ""])
    return "\n".join(lines)
