"""Record disagreements and explicit user decisions."""

from typing import Any


def record_conflict(state: dict[str, Any], unknown_id: str, description: str) -> str:
    if not description.strip():
        raise ValueError("The disagreement description must not be empty.")
    if not any(item["id"] == unknown_id for item in state["unknowns"]):
        raise ValueError(f"Unknown {unknown_id} does not exist.")
    next_id = max((int(item["id"][1:]) for item in state["conflicts"]), default=0) + 1
    conflict_id = f"C{next_id}"
    state["conflicts"].append({
        "id": conflict_id,
        "unknown_id": unknown_id,
        "description": description.strip(),
        "origin": "user",
        "status": "open",
        "resolution": None,
    })
    return conflict_id


def resolve_conflict(state: dict[str, Any], conflict_id: str, resolution: str) -> None:
    if not resolution.strip():
        raise ValueError("The disagreement resolution must not be empty.")
    conflict = next((item for item in state["conflicts"] if item["id"] == conflict_id), None)
    if conflict is None:
        raise ValueError(f"Disagreement {conflict_id} does not exist.")
    if conflict["status"] != "open":
        raise ValueError(f"Disagreement {conflict_id} is already resolved.")
    conflict["status"] = "resolved"
    conflict["resolution"] = resolution.strip()
