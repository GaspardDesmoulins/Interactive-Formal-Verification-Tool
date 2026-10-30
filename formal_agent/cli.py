"""CLI for starting, inspecting and resuming investigations."""

import argparse
import json
import sys
from pathlib import Path

from formal_agent.analysis import analyze
from formal_agent.assumptions import add_assumption, link_property
from formal_agent.conflicts import record_conflict, resolve_conflict
from formal_agent.duplicates import answered_question_matches, mark_duplicate, reopen_duplicate
from formal_agent.evidence import capture_evidence, discover_evidence, reference_at_line
from formal_agent.execution_policy import BUDGET_BOUNDS, ExecutionBudget, execution_readiness
from formal_agent.investigation import create_investigation, resolve_unknown, source_warning
from formal_agent.ollama import MODEL, SUPPORTED_MODELS, OllamaProvider
from formal_agent.properties import accept_property, add_property
from formal_agent.reasoning import JsonFileProvider, apply_proposals
from formal_agent.reporting import render_report
from formal_agent.scenario_suggestions import decide_suggestion, suggest_scenarios
from formal_agent.scenarios import ASSESSMENTS, add_scenario, review_scenario
from formal_agent.storage import create, load, load_snapshot, save
from formal_agent.reference_calls import analyze_test_calls
from formal_agent.workflow import (
    answer_recorded, ask, finish, prepare_answer, prevent_reask, require_active, resume,
)


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
    question.add_argument(
        "--allow-reask", action="store_true", help="Explicitly ask a question matching an answer"
    )
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
    answer.add_argument(
        "--allow-reask", action="store_true", help="Explicitly prompt for a question matching an answer"
    )
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
    scenario = commands.add_parser("add-scenario", help="Record a user-defined candidate scenario")
    scenario.add_argument("state", type=Path)
    scenario.add_argument("property_id")
    scenario.add_argument("--description", required=True, help="Inputs or situation to examine")
    scenario.add_argument("--expected", required=True, help="Expected outcome")
    review = commands.add_parser("review-scenario", help="Record an unverified coverage assessment")
    review.add_argument("state", type=Path)
    review.add_argument("scenario_id")
    review.add_argument("--assessment", required=True, choices=ASSESSMENTS)
    review.add_argument("--reason", required=True, help="Reason for the user assessment")
    review.add_argument("--reference", type=Path, help="Previously recorded reference file")
    review.add_argument("--line", type=int, help="Line containing possibly relevant text")
    suggest = commands.add_parser(
        "suggest-scenarios", help="Suggest parameter boundaries and search references as text"
    )
    suggest.add_argument("state", type=Path)
    suggest.add_argument("property_id")
    accept_suggestion = commands.add_parser(
        "accept-suggestion", help="Review and register a scenario suggestion"
    )
    accept_suggestion.add_argument("state", type=Path)
    accept_suggestion.add_argument("suggestion_id")
    accept_suggestion.add_argument("--expected", required=True, help="User-defined expected outcome")
    accept_suggestion.add_argument("--reason", required=True, help="Reason for accepting")
    accept_suggestion.add_argument("--description", help="User-revised scenario description")
    reject_suggestion = commands.add_parser("reject-suggestion", help="Reject a scenario suggestion")
    reject_suggestion.add_argument("state", type=Path)
    reject_suggestion.add_argument("suggestion_id")
    reject_suggestion.add_argument("--reason", required=True, help="Reason for rejecting")
    static_calls = commands.add_parser(
        "analyze-test-calls", help="Inspect same-name calls in declared Python references"
    )
    static_calls.add_argument("state", type=Path)
    static_calls.add_argument("property_id")
    readiness = commands.add_parser(
        "execution-readiness", help="Inspect a blocked test request without running it",
        description=(
            "Preview only: no isolation checks or tests run. Host memory must exceed "
            "guest memory by at least 128 MiB."
        ),
    )
    budget_defaults = ExecutionBudget()
    readiness.add_argument("state", type=Path)
    readiness.add_argument("scenario_id")
    for field, (minimum, maximum) in BUDGET_BOUNDS.items():
        default = getattr(budget_defaults, field)
        readiness.add_argument(
            "--" + field.replace("_", "-"), type=int, default=default,
            help=f"Requested {field}: {minimum}..{maximum} (default {default}); not enforced",
        )
    propose = commands.add_parser("import-proposals", help="Import untrusted LLM proposals")
    propose.add_argument("state", type=Path)
    propose.add_argument("proposals", type=Path)
    propose.add_argument("--reasoner", required=True, help="Name of the proposal provider")
    ollama = commands.add_parser("propose-ollama", help="Generate proposals with local Ollama")
    ollama.add_argument("state", type=Path)
    ollama.add_argument(
        "--model", default=MODEL,
        help=f"Local model name (supported: {', '.join(SUPPORTED_MODELS)}; default: {MODEL})",
    )
    ollama.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    ollama.add_argument("--max-tokens", type=int, default=1400, help="Output token budget (default: 1400)")
    ollama.add_argument(
        "--context-tokens", type=int, help="Request a context window size in tokens from Ollama"
    )
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
        elif args.command == "execution-readiness":
            state, revision = load_snapshot(args.state)
            budget = ExecutionBudget(
                wall_seconds=args.wall_seconds, cpu_seconds=args.cpu_seconds, vcpus=args.vcpus,
                guest_memory_mib=args.guest_memory_mib, host_memory_mib=args.host_memory_mib,
                storage_mib=args.storage_mib, output_kib=args.output_kib, host_pids=args.host_pids,
            )
            result = execution_readiness(state, revision, args.scenario_id, budget)
            if load_snapshot(args.state)[1] != revision:
                raise ValueError("Investigation changed while checking readiness; retry.")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("Execution blocked; no isolation checks or tests were run.", file=sys.stderr)
            return 1
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
                if (
                    args.answer is None
                    and state["workflow"]["pending_unknown_id"] != args.unknown_id
                ):
                    prevent_reask(state, args.unknown_id, args.allow_reask)
                text = args.answer if args.answer is not None else input(f"{unknown['question']}\n> ")
                resolve_unknown(state, args.unknown_id, text)
                if reference is not None:
                    unknown["answer_reference"] = reference
                answer_recorded(state)
                message = f"Answer recorded for {args.unknown_id}."
            elif args.command == "ask":
                unknown = ask(state, args.unknown_id, args.allow_reask)
                message = (
                    f"Awaiting answer for {unknown['id']}: "
                    f"{json.dumps(unknown['question'], ensure_ascii=False)}"
                )
            elif args.command == "finish":
                counts = finish(state, args.allow_open)
                pending = sum(not item["decisions"] for item in state["scenario_suggestions"])
                message = (
                    f"Investigation finished; {counts[0]} questions and "
                    f"{counts[1]} disagreements remain open; "
                    f"{pending} scenario suggestions await review."
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
            elif args.command == "add-scenario":
                require_active(state)
                scenario_id = add_scenario(state, args.property_id, args.description, args.expected)
                message = f"Candidate scenario {scenario_id} recorded; coverage not assessed."
            elif args.command == "review-scenario":
                require_active(state)
                review_scenario(
                    state, args.scenario_id, args.assessment, args.reason, args.reference, args.line
                )
                message = f"User assessment for {args.scenario_id} recorded; coverage not verified."
            elif args.command == "suggest-scenarios":
                require_active(state)
                added, existing = suggest_scenarios(state, args.property_id)
                changed = bool(added)
                message = (
                    f"Recorded {added} scenario suggestions; {existing} already recorded. "
                    "Text matches are unverified hints, not coverage."
                )
            elif args.command == "accept-suggestion":
                require_active(state)
                scenario_id = decide_suggestion(
                    state, args.suggestion_id, "accepted", args.reason,
                    expected=args.expected, description=args.description,
                )
                message = (
                    f"Suggestion {args.suggestion_id} accepted as scenario {scenario_id}; "
                    "coverage not verified."
                )
            elif args.command == "reject-suggestion":
                require_active(state)
                decide_suggestion(state, args.suggestion_id, "rejected", args.reason)
                message = f"Suggestion {args.suggestion_id} rejected; user reason recorded."
            elif args.command == "analyze-test-calls":
                require_active(state)
                analysis_id, changed, count = analyze_test_calls(state, args.property_id)
                action = "Recorded" if changed else "Already recorded"
                message = (
                    f"{action} static call analysis {analysis_id}: {count} same-name calls. "
                    "Call targets, execution and coverage are not established."
                )
            elif args.command == "import-proposals":
                require_active(state)
                proposal_result = apply_proposals(state, JsonFileProvider(args.proposals), args.reasoner)
                message = (
                    f"Imported {proposal_result.properties_added} candidate properties "
                    f"and {proposal_result.unknowns_added} unknowns."
                )
            else:
                require_active(state)
                provider = OllamaProvider(
                    timeout=args.timeout, max_tokens=args.max_tokens, model=args.model,
                    context_tokens=args.context_tokens,
                )
                proposal_result = apply_proposals(state, provider, f"ollama:{provider.model}")
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
            if args.command == "propose-ollama":
                print(
                    "Warning: Ollama context completeness is not independently verified; "
                    "server support for truncation controls is not checked.",
                    file=sys.stderr,
                )
            elif args.command == "ask":
                matches = answered_question_matches(state)
                if matches:
                    pairs = ", ".join(
                        f"{unknown_id} matches {', '.join(answered)}"
                        for unknown_id, answered in matches.items()
                    )
                    print(
                        f"Warning: open questions have the same normalized text as answered "
                        f"questions: {pairs}. Review them; equivalence is not assumed.",
                        file=sys.stderr,
                    )
    except (OSError, UnicodeError, SyntaxError, ValueError, EOFError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0
