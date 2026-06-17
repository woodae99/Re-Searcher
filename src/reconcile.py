"""Pure reconciliation planner for the index ledger.

The planner compares source-adapter world state with the registry's recorded
ledger state. It does no I/O itself, except for the tiny orchestration helper
that asks enabled sources and the registry for their current snapshots.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from src.sources.base import DataSource, UnitState


@dataclass
class WorkPlan:
    """Diff result between current source state and recorded ledger state."""

    creates: List[UnitState] = field(default_factory=list)
    updates: List[UnitState] = field(default_factory=list)
    deletes: List[str] = field(default_factory=list)
    unchanged: int = 0

    def is_empty(self) -> bool:
        return not (self.creates or self.updates or self.deletes)

    def touched_identities(self) -> Set[Tuple[str, str]]:
        """Return source identities touched by creates/updates."""
        return {
            (unit.identity_field, unit.identity_value)
            for unit in (*self.creates, *self.updates)
        }


def reconcile(world: Dict[str, UnitState], ledger: Dict[str, str]) -> WorkPlan:
    """Diff source world state against ledger fingerprints."""
    plan = WorkPlan()
    for unit_id, state in world.items():
        previous = ledger.get(unit_id)
        if previous is None:
            plan.creates.append(state)
        elif previous != state.fingerprint:
            plan.updates.append(state)
        else:
            plan.unchanged += 1

    world_ids = set(world)
    plan.deletes = [unit_id for unit_id in ledger if unit_id not in world_ids]
    return plan


def build_work_plan(sources: List[DataSource], registry) -> WorkPlan:
    """Merge enabled source states and reconcile against the registry ledger."""
    world: Dict[str, UnitState] = {}
    for source in sources:
        if source.is_enabled():
            world.update(source.enumerate_state())
    return reconcile(world, registry.get_unit_states())
