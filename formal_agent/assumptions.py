"""Maintain explicit relationships between questions, properties and assumptions."""

from typing import Any


def link_property(state: dict[str, Any], unknown_id: str, property_id: str) -> None:
    unknown = next((item for item in state["unknowns"] if item["id"] == unknown_id), None)
    if unknown is None:
        raise ValueError(f"Unknown {unknown_id} does not exist.")
    prop = next((item for item in state["properties"] if item["id"] == property_id), None)
    if prop is None:
        raise ValueError(f"Property {property_id} does not exist.")
    if prop["origin"] == "user":
        source = next(item for item in state["unknowns"] if item["id"] == prop["source_unknown_id"])
        target = (source["function"], source["line"])
    else:
        target = (prop["source_function"]["name"], prop["source_function"]["line"])
    if (unknown["function"], unknown["line"]) != target:
        raise ValueError("The unknown and property refer to different functions.")
    link = {"unknown_id": unknown_id, "property_id": property_id}
    if link in state["links"]:
        raise ValueError("The unknown and property are already linked.")
    state["links"].append(link)


def add_assumption(
    state: dict[str, Any], unknown_id: str, property_id: str, statement: str
) -> str:
    if not statement.strip():
        raise ValueError("The assumption must not be empty.")
    unknown = next((item for item in state["unknowns"] if item["id"] == unknown_id), None)
    if unknown is None:
        raise ValueError(f"Unknown {unknown_id} does not exist.")
    if unknown["status"] != "answered":
        raise ValueError(f"Unknown {unknown_id} has not been answered.")
    link = {"unknown_id": unknown_id, "property_id": property_id}
    if link not in state["links"]:
        raise ValueError("Link the unknown to the property before adding an assumption.")
    next_id = max(
        (int(item["id"][1:]) for item in state["assumptions"]), default=0
    ) + 1
    assumption_id = f"A{next_id}"
    state["assumptions"].append({
        "id": assumption_id,
        "statement": statement.strip(),
        "status": "assumed",
        "origin": "user",
        "source_unknown_id": unknown_id,
        "property_id": property_id,
    })
    return assumption_id
