"""Record user-proposed properties without claiming verification."""

from typing import Any


def next_property_id(state: dict[str, Any]) -> str:
    next_id = max(
        (int(item["id"][1:]) for item in state["properties"]),
        default=0,
    ) + 1
    return f"P{next_id}"


def add_property(state: dict[str, Any], unknown_id: str, statement: str) -> str:
    if not statement.strip():
        raise ValueError("The property statement must not be empty.")
    unknown = next(
        (item for item in state["unknowns"] if item["id"] == unknown_id),
        None,
    )
    if unknown is None:
        raise ValueError(f"Unknown {unknown_id} does not exist.")
    if unknown["status"] != "answered":
        raise ValueError(f"Unknown {unknown_id} has not been answered.")

    property_id = next_property_id(state)
    state["properties"].append({
        "id": property_id,
        "statement": statement.strip(),
        "status": "candidate",
        "origin": "user",
        "source_unknown_id": unknown_id,
        "accepted_by": None,
    })
    return property_id


def accept_property(state: dict[str, Any], property_id: str) -> None:
    property_item = next(
        (item for item in state["properties"] if item["id"] == property_id),
        None,
    )
    if property_item is None:
        raise ValueError(f"Property {property_id} does not exist.")
    if property_item["status"] != "candidate":
        raise ValueError(f"Property {property_id} is already accepted.")
    property_item["status"] = "accepted"
    property_item["accepted_by"] = "user"
