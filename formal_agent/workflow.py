"""Control investigation transitions without claiming verification."""

from typing import Any

from formal_agent.duplicates import answered_question_matches


def require_active(state: dict[str, Any]) -> None:
    if state["workflow"]["phase"] != "active":
        raise ValueError("The investigation is not active; answer or resume it first.")


def prevent_reask(state: dict[str, Any], unknown_id: str, allow_reask: bool) -> None:
    matches = answered_question_matches(state).get(unknown_id, [])
    if matches and not allow_reask:
        raise ValueError(
            f"Unknown {unknown_id} has the same normalized question as answered "
            f"{', '.join(matches)}. Review it or use --allow-reask explicitly."
        )


def ask(
    state: dict[str, Any], unknown_id: str | None = None, allow_reask: bool = False
) -> dict[str, Any]:
    require_active(state)
    unknowns = [item for item in state["unknowns"] if item["status"] == "open"]
    if unknown_id is None:
        if allow_reask:
            raise ValueError("--allow-reask requires an explicit unknown ID.")
        if not unknowns:
            raise ValueError("There are no open questions.")
        matches = answered_question_matches(state)
        unknowns = [item for item in unknowns if item["id"] not in matches]
        if not unknowns:
            pairs = ", ".join(
                f"{item_id} matches {', '.join(answered)}"
                for item_id, answered in matches.items()
            )
            raise ValueError(
                f"All open questions may repeat answered questions: {pairs}. "
                "Review them or ask a specific ID with --allow-reask."
            )
        properties = {item["id"]: item for item in state["properties"]}

        def score(item: dict[str, Any]) -> int:
            return sum(
                2 if properties[link["property_id"]]["status"] == "accepted" else 1
                for link in state["links"] if link["unknown_id"] == item["id"]
            )

        unknown = max(unknowns, key=score)
    else:
        unknown = next((item for item in unknowns if item["id"] == unknown_id), None)
        if unknown is None:
            raise ValueError(f"Unknown {unknown_id} does not exist or is not open.")
        prevent_reask(state, unknown_id, allow_reask)
    state["workflow"] = {"phase": "awaiting_answer", "pending_unknown_id": unknown["id"]}
    return unknown


def prepare_answer(state: dict[str, Any], unknown_id: str) -> None:
    workflow = state["workflow"]
    if workflow["phase"] == "finished":
        raise ValueError("The investigation is finished; resume it before answering.")
    if workflow["phase"] == "awaiting_answer" and workflow["pending_unknown_id"] != unknown_id:
        raise ValueError(f"Answer {workflow['pending_unknown_id']} or resume the investigation.")


def answer_recorded(state: dict[str, Any]) -> None:
    state["workflow"] = {"phase": "active", "pending_unknown_id": None}


def finish(state: dict[str, Any], allow_open: bool) -> tuple[int, int]:
    require_active(state)
    open_count = sum(item["status"] == "open" for item in state["unknowns"])
    conflict_count = sum(item["status"] == "open" for item in state["conflicts"])
    if (open_count or conflict_count) and not allow_open:
        raise ValueError(
            f"{open_count} questions and {conflict_count} disagreements remain open; "
            "use --allow-open to finish."
        )
    state["workflow"] = {"phase": "finished", "pending_unknown_id": None}
    return open_count, conflict_count


def resume(state: dict[str, Any]) -> None:
    if state["workflow"]["phase"] == "active":
        raise ValueError("The investigation is already active.")
    state["workflow"] = {"phase": "active", "pending_unknown_id": None}
