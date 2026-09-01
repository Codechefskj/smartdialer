"""Provider event definitions."""
from __future__ import annotations
import itertools
from dataclasses import dataclass
from .models import CallState

_event_ids = itertools.count(1)


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    call_id: str
    state: CallState
    sequence: int  # the provider's own notion of ordering - NOT trusted blindly
    emitted_at: float


def new_event(call_id: str, state: CallState, sequence: int, emitted_at: float) -> ProviderEvent:
    return ProviderEvent(
        event_id=f"evt-{next(_event_ids)}",
        call_id=call_id,
        state=state,
        sequence=sequence,
        emitted_at=emitted_at,
    )
