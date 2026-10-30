"""Record user-reviewed duplicate questions without inferring equivalence."""

from typing import Any


def mark_duplicate(
    state: dict[str, Any], unknown_id: str, canonical_id: str, reason: str
) -> None:
    if not reason.strip():
        raise ValueError("A reason for the duplicate decision is required.")
    unknowns = {item["id"]: item for item in state["unknowns"]}
    original = unknowns.get(unknown_id)
    canonical = unknowns.get(canonical_id)
    if original is None or canonical is None:
        raise ValueError("Both unknowns must exist.")
    if original["status"] != "open" or canonical["status"] not in ("open", "answered"):
        raise ValueError("The duplicate must be open and its target open or answered.")
    if unknown_id == canonical_id:
        raise ValueError("An unknown cannot be a duplicate of itself.")
    if (original["function"], original["line"]) != (canonical["function"], canonical["line"]):
        raise ValueError("Duplicate questions must concern the same function.")
    if any(item.get("duplicate_of") == unknown_id for item in state["unknowns"]):
        raise ValueError("Resolve dependent duplicate decisions first.")
    for link in list(state["links"]):
        if link["unknown_id"] == unknown_id:
            target = {"unknown_id": canonical_id, "property_id": link["property_id"]}
            if target not in state["links"]:
                state["links"].append(target)
    original["status"] = "duplicate"
    original["duplicate_of"] = canonical_id
    original["duplicate_reason"] = reason.strip()


def reopen_duplicate(state: dict[str, Any], unknown_id: str) -> None:
    unknown = next((item for item in state["unknowns"] if item["id"] == unknown_id), None)
    if unknown is None or unknown["status"] != "duplicate":
        raise ValueError(f"Unknown {unknown_id} is not a recorded duplicate.")
    unknown["status"] = "open"
    del unknown["duplicate_of"]
    del unknown["duplicate_reason"]
