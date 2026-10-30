"""CLI for starting, inspecting and resuming investigations."""

import argparse
import json
import sys
from pathlib import Path

from formal_agent.analysis import analyze
from formal_agent.investigation import create_investigation, resolve_unknown, source_warning
from formal_agent.storage import create, load, save


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="formal-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Analyze a Python file without executing it")
    start.add_argument("source", type=Path)
    start.add_argument("state", type=Path, help="New investigation JSON file")
    show = commands.add_parser("show", help="Display a saved investigation as JSON")
    show.add_argument("state", type=Path)
    answer = commands.add_parser("answer", help="Answer an open question")
    answer.add_argument("state", type=Path)
    answer.add_argument("unknown_id")
    answer.add_argument("--answer", help="Answer text; prompt if omitted")
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
        else:
            state = load(args.state)
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Start a new investigation.")
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
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Answer was not saved; start a new investigation.")
            save(args.state, state)
            print(f"Answer recorded for {args.unknown_id}.")
    except (OSError, UnicodeError, SyntaxError, ValueError, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
