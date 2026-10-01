"""Event barriers order committed policy changes without timing-based race assumptions."""

import asyncio
from dataclasses import dataclass, field

from experiments.mutations import Mutations
from experiments.scenario_types import DirectRevoke


@dataclass
class Ordering:
    """Coordinate one action with the model or release boundary on another connection."""

    action: DirectRevoke
    mutations: Mutations
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    committed: asyncio.Event = field(default_factory=asyncio.Event)
    commits: list[str] = field(default_factory=list)

    async def change(self) -> None:
        """Publish completion only after the independent mutation transaction commits."""
        _ = await self.reached.wait()
        await self.mutations.apply(self.action)
        self.commits.append(self.action.id)
        self.committed.set()

    async def boundary(self, action_id: str, event: str) -> None:
        """Block the selected boundary until its ordered mutation has committed."""
        barrier = self.action.barrier
        if barrier is not None and barrier.ask_id == action_id and barrier.event == event:
            self.reached.set()
            _ = await self.committed.wait()
