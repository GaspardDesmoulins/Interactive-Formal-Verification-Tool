"""CLI for starting, inspecting and resuming investigations."""

import argparse
import json
import sys
from pathlib import Path

from formal_agent.analysis import analyze
from formal_agent.investigation import create_investigation, resolve_unknown, source_warning
from formal_agent.ollama import MODEL, OllamaProvider
from formal_agent.properties import accept_property, add_property
from formal_agent.reasoning import JsonFileProvider, apply_proposals
from formal_agent.reporting import render_report
from formal_agent.storage import create, load, load_snapshot, save


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="formal-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Analyze a Python file without executing it")
    start.add_argument("source", type=Path)
    start.add_argument("state", type=Path, help="New investigation JSON file")
    show = commands.add_parser("show", help="Display a saved investigation as JSON")
    show.add_argument("state", type=Path)
    report = commands.add_parser("report", help="Render an investigation report")
    report.add_argument("state", type=Path)
    answer = commands.add_parser("answer", help="Answer an open question")
    answer.add_argument("state", type=Path)
    answer.add_argument("unknown_id")
    answer.add_argument("--answer", help="Answer text; prompt if omitted")
    add = commands.add_parser("add-property", help="Propose a property from an answer")
    add.add_argument("state", type=Path)
    add.add_argument("unknown_id")
    add.add_argument("--statement", required=True, help="Candidate property")
    accept = commands.add_parser("accept-property", help="Accept a candidate specification")
    accept.add_argument("state", type=Path)
    accept.add_argument("property_id")
    propose = commands.add_parser("import-proposals", help="Import untrusted LLM proposals")
    propose.add_argument("state", type=Path)
    propose.add_argument("proposals", type=Path)
    propose.add_argument("--reasoner", required=True, help="Name of the proposal provider")
    ollama = commands.add_parser("propose-ollama", help="Generate proposals with local Ollama")
    ollama.add_argument("state", type=Path)
    ollama.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    ollama.add_argument("--max-tokens", type=int, default=1400, help="Output token budget (default: 1400)")
    args = parser.parse_args(argv)

    try:
        if args.command == "start":
            model = analyze(args.source)
            state = create_investigation(model)
            create(args.state, state)
            print(f"Investigation saved: {args.state}")
            print(f"Functions: {len(model['functions'])}; open questions: {len(state['unknowns'])}")
        elif args.command == "show":
            state = load(args.state)
            warning = source_warning(state)
            if warning:
                print(f"Warning: {warning}", file=sys.stderr)
            print(json.dumps(state, ensure_ascii=False, indent=2))
        elif args.command == "report":
            print(render_report(load(args.state)), end="")
        else:
            state, revision = load_snapshot(args.state)
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Start a new investigation.")
            if args.command == "answer":
                unknown = next(
                    (item for item in state["unknowns"] if item["id"] == args.unknown_id),
                    None,
                )
                if unknown is None:
                    raise ValueError(f"Unknown {args.unknown_id} does not exist.")
                if unknown["status"] != "open":
                    raise ValueError(f"Unknown {args.unknown_id} is already resolved.")
                text = args.answer if args.answer is not None else input(f"{unknown['question']}\n> ")
                resolve_unknown(state, args.unknown_id, text)
                message = f"Answer recorded for {args.unknown_id}."
            elif args.command == "add-property":
                property_id = add_property(state, args.unknown_id, args.statement)
                message = f"Candidate property {property_id} recorded."
            elif args.command == "accept-property":
                accept_property(state, args.property_id)
                message = f"Property {args.property_id} accepted as a specification, not proved."
            elif args.command == "import-proposals":
                counts = apply_proposals(state, JsonFileProvider(args.proposals), args.reasoner)
                message = f"Imported {counts[0]} candidate properties and {counts[1]} unknowns."
            else:
                provider = OllamaProvider(timeout=args.timeout, max_tokens=args.max_tokens)
                counts = apply_proposals(state, provider, f"ollama:{MODEL}")
                message = f"Generated {counts[0]} candidate properties and {counts[1]} unknowns."
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Changes were not saved; start a new investigation.")
            save(args.state, state, revision)
            print(message)
    except (OSError, UnicodeError, SyntaxError, ValueError, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
