"""Observe same-name calls in declared Python references without executing them."""

import ast
import hashlib
import io
import tokenize
from pathlib import Path
from typing import Any

from formal_agent.evidence import read_evidence
from formal_agent.scenario_suggestions import property_function


METHOD = "python-ast-direct-v1"
MAX_CALLS = 200
MAX_ARGUMENTS = 64
MAX_EXPRESSION = 512


def python_references(model: dict[str, Any]) -> list[dict[str, str]]:
    return [
        item for item in model.get("evidence_sources", [])
        if Path(item["path"]).suffix.lower() == ".py"
    ]


def source_function(state: dict[str, Any], property_item: dict[str, Any]) -> ast.FunctionDef | ast.AsyncFunctionDef:
    model = state["model"]
    source = Path(model["source"]).read_bytes()
    if hashlib.sha256(source).hexdigest() != model["sha256"]:
        raise ValueError("Source changed during static call analysis.")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    tree = ast.parse(source.decode(encoding), filename=model["source"])
    function = property_function(state, property_item)
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (node.name, node.lineno) == (function["name"], function["line"])
        ):
            return node
    raise ValueError("Function observation does not match the source.")


def expression_text(source: str, node: ast.AST) -> str:
    text = ast.get_source_segment(source, node)
    if text is None or not text.strip():
        raise ValueError("Unable to extract an observed call expression.")
    if len(text) > MAX_EXPRESSION:
        raise ValueError(f"An observed expression exceeds {MAX_EXPRESSION} characters.")
    return text


def value_observation(node: ast.AST) -> tuple[str, str | None]:
    literal = node
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        if isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float, complex):
            literal = node.operand
    if not isinstance(literal, ast.Constant):
        return "unresolved", None
    value = literal.value
    if value is None and literal is node:
        return "literal", "None"
    if type(value) is int and value == 0:
        return "literal", "0"
    return "literal", None


def call_arguments(
    source: str, node: ast.Call, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[dict[str, Any]]:
    if len(node.args) + len(node.keywords) > MAX_ARGUMENTS:
        raise ValueError(f"An observed call exceeds {MAX_ARGUMENTS} arguments.")
    positional = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
    keywords = {arg.arg for arg in (*function.args.args, *function.args.kwonlyargs)}
    result = []
    after_star = False
    for index, arg in enumerate(node.args):
        if isinstance(arg, ast.Starred):
            after_star = True
            kind, parameter = "starred", None
            observed = arg.value
        else:
            kind = "positional"
            parameter = positional[index] if not after_star and index < len(positional) else None
            observed = arg
        value_kind, boundary = value_observation(observed) if kind == "positional" else ("unresolved", None)
        result.append({
            "kind": kind, "position": index, "keyword": None, "parameter": parameter,
            "expression": expression_text(source, observed),
            "value_kind": value_kind, "boundary": boundary,
        })
    for keyword in node.keywords:
        kind = "keyword" if keyword.arg is not None else "keyword_unpack"
        parameter = keyword.arg if keyword.arg in keywords else None
        value_kind, boundary = (
            value_observation(keyword.value) if kind == "keyword" else ("unresolved", None)
        )
        result.append({
            "kind": kind, "position": None, "keyword": keyword.arg, "parameter": parameter,
            "expression": expression_text(source, keyword.value),
            "value_kind": value_kind, "boundary": boundary,
        })
    return result


def analyze_test_calls(state: dict[str, Any], property_id: str) -> tuple[str, bool, int]:
    prop = next((item for item in state["properties"] if item["id"] == property_id), None)
    if prop is None:
        raise ValueError(f"Property {property_id} does not exist.")
    references = python_references(state["model"])
    if not references:
        raise ValueError("No Python references were declared for this investigation.")
    function = source_function(state, prop)
    calls = []
    for reference in references:
        text, digest = read_evidence(Path(reference["path"]))
        if digest != reference["sha256"]:
            raise ValueError(f"Evidence changed during static call analysis: {reference['path']}")
        try:
            tree = ast.parse(text, filename=reference["path"])
        except SyntaxError as error:
            raise ValueError(
                f"Python reference cannot be parsed ({reference['path']}:{error.lineno}): {error.msg}"
            ) from error
        nodes = sorted(
            (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset),
        )
        for node in nodes:
            if isinstance(node.func, ast.Name) and node.func.id == function.name:
                kind = "name"
            elif isinstance(node.func, ast.Attribute) and node.func.attr == function.name:
                kind = "attribute"
            else:
                continue
            if len(calls) >= MAX_CALLS:
                raise ValueError(f"Static analysis exceeds {MAX_CALLS} same-name calls.")
            calls.append({
                "reference": {**reference, "line": node.lineno},
                "callee": expression_text(text, node.func),
                "kind": kind,
                "arguments": call_arguments(text, node, function),
            })
    result = {
        "property_id": property_id,
        "function": {"name": function.name, "line": function.lineno},
        "method": METHOD,
        "evidence_scope": [dict(reference) for reference in references],
        "calls": calls,
    }
    for existing in state["test_call_analyses"]:
        if {key: value for key, value in existing.items() if key != "id"} == result:
            return existing["id"], False, len(calls)
    next_id = max(
        (int(item["id"][2:]) for item in state["test_call_analyses"]), default=0
    ) + 1
    analysis_id = f"TA{next_id}"
    state["test_call_analyses"].append({"id": analysis_id, **result})
    return analysis_id, True, len(calls)


def suggestion_matches(
    state: dict[str, Any], suggestion: dict[str, Any]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    return [
        (analysis["id"], call, argument)
        for analysis in state["test_call_analyses"]
        if analysis["property_id"] == suggestion["property_id"]
        for call in analysis["calls"]
        for argument in call["arguments"]
        if (
            argument["parameter"] == suggestion["parameter"]
            and argument["boundary"] == suggestion["value"]
        )
    ]
