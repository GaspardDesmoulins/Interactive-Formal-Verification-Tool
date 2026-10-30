"""Describe bounded execution requests without starting a backend."""

from dataclasses import asdict, dataclass
from typing import Any

from formal_agent.investigation import source_warning
from formal_agent.storage import valid_sha256


BUDGET_BOUNDS = {
    "wall_seconds": (1, 60),
    "cpu_seconds": (1, 60),
    "vcpus": (1, 2),
    "guest_memory_mib": (128, 512),
    "host_memory_mib": (256, 768),
    "storage_mib": (16, 128),
    "output_kib": (1, 256),
    "host_pids": (16, 128),
}


@dataclass(frozen=True)
class ExecutionBudget:
    wall_seconds: int = 30
    cpu_seconds: int = 10
    vcpus: int = 1
    guest_memory_mib: int = 128
    host_memory_mib: int = 256
    storage_mib: int = 64
    output_kib: int = 64
    host_pids: int = 64

    def validate(self) -> None:
        for field, (minimum, maximum) in BUDGET_BOUNDS.items():
            value = getattr(self, field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be an integer from {minimum} to {maximum}.")
        if self.host_memory_mib < self.guest_memory_mib + 128:
            raise ValueError("host_memory_mib must exceed guest_memory_mib by at least 128 MiB.")


def execution_readiness(
    state: dict[str, Any], revision: str, scenario_id: str, budget: ExecutionBudget
) -> dict[str, Any]:
    budget.validate()
    if not valid_sha256(revision):
        raise ValueError("Invalid investigation revision.")
    scenario = next((item for item in state["scenarios"] if item["id"] == scenario_id), None)
    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} does not exist.")
    blockers = [
        {
            "code": "test_bundle_unavailable",
            "message": "Scenario descriptions are not executable test bundles.",
        },
        {
            "code": "isolated_backend_unavailable",
            "message": "No isolated execution backend is implemented or qualified.",
        },
    ]
    warning = source_warning(state)
    if warning:
        blockers.insert(0, {"code": "input_snapshot_unavailable", "message": warning})
    return {
        "kind": "test_execution_readiness",
        "version": 1,
        "status": "blocked",
        "execution_started": False,
        "isolation_checks": "not_performed",
        "investigation_revision": revision,
        "source_sha256": state["model"]["sha256"],
        "property_id": scenario["property_id"],
        "scenario_id": scenario_id,
        "requested_budget": asdict(budget),
        "blockers": blockers,
    }
