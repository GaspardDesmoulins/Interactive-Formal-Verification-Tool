"""Control investigation transitions without claiming verification."""

from typing import Any


def require_active(state: dict[str, Any]) -> None:
    if state["workflow"]["phase"] != "active":
        raise ValueError("The investigation is not active; answer or resume it first.")


def ask(state: dict[str, Any], unknown_id: str | None = None) -> dict[str, Any]:
    require_active(state)
    unknowns = [item for item in state["unknowns"] if item["status"] == "open"]
    if unknown_id is None:
        properties = {item["id"]: item for item in state["properties"]}

        def score(item: dict[str, Any]) -> int:
            return sum(
                2 if properties[link["property_id"]]["status"] == "accepted" else 1
                for link in state["links"] if link["unknown_id"] == item["id"]
            )

        unknown = max(unknowns, key=score, default=None)
        if unknown is None:
            raise ValueError("There are no open questions.")
    else:
        unknown = next((item for item in unknowns if item["id"] == unknown_id), None)
        if unknown is None:
            raise ValueError(f"Unknown {unknown_id} does not exist or is not open.")
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


def finish(state: dict[str, Any], allow_open: bool) -> int:
    require_active(state)
    open_count = sum(item["status"] == "open" for item in state["unknowns"])
    if open_count and not allow_open:
        raise ValueError(f"{open_count} questions remain open; use --allow-open to finish.")
    state["workflow"] = {"phase": "finished", "pending_unknown_id": None}
    return open_count


def resume(state: dict[str, Any]) -> None:
    if state["workflow"]["phase"] == "active":
        raise ValueError("The investigation is already active.")
    state["workflow"] = {"phase": "active", "pending_unknown_id": None}
