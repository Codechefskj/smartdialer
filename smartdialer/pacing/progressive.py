"""Progressive pacer: never suggest more outbound calls than there
are free agents right now. 1 available agent -> at most 1 outbound
call. This alone makes progressive mode safe by construction; the
SafetyController still reviews it for consistency."""
from dataclasses import dataclass


@dataclass
class PacingSuggestion:
    count: int
    reasoning: str


class ProgressivePacer:
    def suggest(self, available_agents: int, pending_borrowers: int) -> PacingSuggestion:
        count = max(0, min(available_agents, pending_borrowers))
        return PacingSuggestion(
            count=count,
            reasoning=f"progressive 1:1 - available_agents={available_agents}, "
                      f"pending_borrowers={pending_borrowers} -> {count}",
        )
