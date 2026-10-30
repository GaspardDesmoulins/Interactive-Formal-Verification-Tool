"""CLI for starting, inspecting and resuming investigations."""

import argparse
import json
import sys
from pathlib import Path

from formal_agent.analysis import analyze
from formal_agent.assumptions import add_assumption, link_property
from formal_agent.conflicts import record_conflict, resolve_conflict
from formal_agent.duplicates import mark_duplicate, reopen_duplicate
from formal_agent.evidence import capture_evidence, discover_evidence, reference_at_line
from formal_agent.investigation import create_investigation, resolve_unknown, source_warning
from formal_agent.ollama import MODEL, OllamaProvider
from formal_agent.properties import accept_property, add_property
from formal_agent.reasoning import JsonFileProvider, apply_proposals
from formal_agent.reporting import render_report
from formal_agent.storage import create, load, load_snapshot, save
from formal_agent.workflow import answer_recorded, ask, finish, prepare_answer, require_active, resume


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="formal-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Analyze a Python file without executing it")
    start.add_argument("source", type=Path)
    start.add_argument("state", type=Path, help="New investigation JSON file")
    start.add_argument(
        "--evidence", type=Path, action="append", default=[],
        help="UTF-8 documentation or test file to consider as untrusted text; may be repeated",
    )
    start.add_argument(
        "--discover-evidence", action="store_true",
        help="Inspect conventional sibling documentation and test files",
    )
    show = commands.add_parser("show", help="Display a saved investigation as JSON")
    show.add_argument("state", type=Path)
    report = commands.add_parser("report", help="Render an investigation report")
    report.add_argument("state", type=Path)
    question = commands.add_parser("ask", help="Suspend while awaiting an open question")
    question.add_argument("state", type=Path)
    question.add_argument("unknown_id", nargs="?", help="Unknown ID; omit to select by priority")
    done = commands.add_parser("finish", help="Finish an investigation")
    done.add_argument("state", type=Path)
    done.add_argument("--allow-open", action="store_true", help="Explicitly retain open unknowns")
    restart = commands.add_parser("resume", help="Resume a paused or finished investigation")
    restart.add_argument("state", type=Path)
    duplicate = commands.add_parser("mark-duplicate", help="Record a duplicate question decision")
    duplicate.add_argument("state", type=Path)
    duplicate.add_argument("unknown_id")
    duplicate.add_argument("canonical_id")
    duplicate.add_argument("--reason", required=True, help="Reason for considering them equivalent")
    reopen = commands.add_parser("reopen-unknown", help="Undo a duplicate question decision")
    reopen.add_argument("state", type=Path)
    reopen.add_argument("unknown_id")
    conflict = commands.add_parser("record-conflict", help="Record a disagreement")
    conflict.add_argument("state", type=Path)
    conflict.add_argument("unknown_id")
    conflict.add_argument("--description", required=True, help="Describe the disagreement")
    resolve = commands.add_parser("resolve-conflict", help="Record a disagreement decision")
    resolve.add_argument("state", type=Path)
    resolve.add_argument("conflict_id")
    resolve.add_argument("--resolution", required=True, help="Describe the user decision")
    answer = commands.add_parser("answer", help="Answer an open question")
    answer.add_argument("state", type=Path)
    answer.add_argument("unknown_id")
    answer.add_argument("--answer", help="Answer text; prompt if omitted")
    answer.add_argument("--reference", type=Path, help="Previously recorded reference file")
    answer.add_argument("--line", type=int, help="Line containing supporting text")
    add = commands.add_parser("add-property", help="Propose a property from an answer")
    add.add_argument("state", type=Path)
    add.add_argument("unknown_id")
    add.add_argument("--statement", required=True, help="Candidate property")
    accept = commands.add_parser("accept-property", help="Accept a candidate specification")
    accept.add_argument("state", type=Path)
    accept.add_argument("property_id")
    link = commands.add_parser("link-property", help="Link an unknown to a property")
    link.add_argument("state", type=Path)
    link.add_argument("unknown_id")
    link.add_argument("property_id")
    assume = commands.add_parser("add-assumption", help="Record an explicit assumption")
    assume.add_argument("state", type=Path)
    assume.add_argument("unknown_id")
    assume.add_argument("property_id")
    assume.add_argument("--statement", required=True, help="Assumption based on the answer")
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
            paths = list(args.evidence)
            if args.discover_evidence:
                explicit = {path.resolve() for path in paths}
                paths.extend(path for path in discover_evidence(args.source) if path not in explicit)
            model["evidence_sources"] = capture_evidence(paths, model["source"])
            state = create_investigation(model)
            create(args.state, state)
            print(f"Investigation saved: {args.state}")
            print(f"Functions: {len(model['functions'])}; open questions: {len(state['unknowns'])}")
            print(f"Reference files: {len(model['evidence_sources'])}")
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
            changed = True
            proposal_result = None
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Start a new investigation.")
            if args.command == "answer":
                prepare_answer(state, args.unknown_id)
                if (args.reference is None) != (args.line is None):
                    raise ValueError("--reference and --line must be provided together.")
                reference = None
                if args.reference is not None and args.line is not None:
                    reference = reference_at_line(state["model"], args.reference, args.line)
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
                if reference is not None:
                    unknown["answer_reference"] = reference
                answer_recorded(state)
                message = f"Answer recorded for {args.unknown_id}."
            elif args.command == "ask":
                unknown = ask(state, args.unknown_id)
                message = (
                    f"Awaiting answer for {unknown['id']}: "
                    f"{json.dumps(unknown['question'], ensure_ascii=False)}"
                )
            elif args.command == "finish":
                counts = finish(state, args.allow_open)
                message = (
                    f"Investigation finished; {counts[0]} questions and "
                    f"{counts[1]} disagreements remain open."
                )
            elif args.command == "resume":
                resume(state)
                message = "Investigation resumed."
            elif args.command == "mark-duplicate":
                require_active(state)
                mark_duplicate(state, args.unknown_id, args.canonical_id, args.reason)
                message = f"Recorded {args.unknown_id} as a duplicate of {args.canonical_id}."
            elif args.command == "reopen-unknown":
                require_active(state)
                reopen_duplicate(state, args.unknown_id)
                message = f"Reopened {args.unknown_id}."
            elif args.command == "record-conflict":
                require_active(state)
                conflict_id = record_conflict(state, args.unknown_id, args.description)
                message = f"Recorded disagreement {conflict_id}."
            elif args.command == "resolve-conflict":
                require_active(state)
                resolve_conflict(state, args.conflict_id, args.resolution)
                message = f"Recorded decision for disagreement {args.conflict_id}."
            elif args.command == "add-property":
                require_active(state)
                property_id = add_property(state, args.unknown_id, args.statement)
                message = f"Candidate property {property_id} recorded."
            elif args.command == "accept-property":
                require_active(state)
                accept_property(state, args.property_id)
                message = f"Property {args.property_id} accepted as a specification, not proved."
            elif args.command == "link-property":
                require_active(state)
                link_property(state, args.unknown_id, args.property_id)
                message = f"Linked {args.unknown_id} to {args.property_id}."
            elif args.command == "add-assumption":
                require_active(state)
                assumption_id = add_assumption(
                    state, args.unknown_id, args.property_id, args.statement
                )
                message = f"Assumption {assumption_id} recorded; it has not been verified."
            elif args.command == "import-proposals":
                require_active(state)
                proposal_result = apply_proposals(state, JsonFileProvider(args.proposals), args.reasoner)
                message = (
                    f"Imported {proposal_result.properties_added} candidate properties "
                    f"and {proposal_result.unknowns_added} unknowns."
                )
            else:
                require_active(state)
                provider = OllamaProvider(timeout=args.timeout, max_tokens=args.max_tokens)
                proposal_result = apply_proposals(state, provider, f"ollama:{MODEL}")
                message = (
                    f"Generated {proposal_result.properties_added} candidate properties "
                    f"and {proposal_result.unknowns_added} unknowns."
                )
            if proposal_result is not None:
                changed = bool(proposal_result.properties_added or proposal_result.unknowns_added)
            warning = source_warning(state)
            if warning:
                raise ValueError(f"{warning}. Changes were not saved; start a new investigation.")
            if changed:
                save(args.state, state, revision)
            elif load_snapshot(args.state)[1] != revision:
                raise ValueError("Investigation changed while processing; changes were not saved.")
            print(message)
            if proposal_result is not None:
                for index, property_id in proposal_result.duplicate_properties:
                    print(f"Skipped exact property proposal #{index}; matches {property_id}.")
                for index, unknown_id in proposal_result.duplicate_unknowns:
                    print(f"Skipped exact unknown proposal #{index}; matches {unknown_id}.")
                for unknown_id, existing_id in proposal_result.similar_unknowns:
                    print(
                        f"Unknown {unknown_id} has the same normalized question as {existing_id}; "
                        "details differ, review both."
                    )
                if not changed:
                    print("No new proposals; investigation unchanged.")
    except (OSError, UnicodeError, SyntaxError, ValueError, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
