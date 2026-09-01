"""Explicit state machines for Agent and Call lifecycles.

Design notes
------------
Telecom providers are not trusted to deliver events exactly-once or
in-order. The CallStateMachine therefore does NOT treat an incoming
event as a command that always fires a transition. Instead every
state is given a numeric "rank". An incoming event is only applied
if it would move the call to a state with rank >= the current rank
(monotonic progress). Anything else - a duplicate of the current
state, or a state we've already passed - is recorded as a no-op
("absorbed") rather than raising an error or corrupting state.
Terminal states never leave once entered.
"""
from __future__ import annotations
from dataclasses import dataclass
from .models import AgentState, Call, CallState

# ---- Agent transitions -----------------------------------------------

_AGENT_TRANSITIONS = {
    AgentState.OFFLINE: {AgentState.AVAILABLE},
    AgentState.AVAILABLE: {AgentState.RESERVED, AgentState.PAUSED, AgentState.OFFLINE},
    # RESERVED -> CONNECTED (skipping DIALING) happens in predictive
    # mode: the agent is only reserved once a borrower has *already*
    # answered, so there is no separate dialing phase for the agent.
    AgentState.RESERVED: {AgentState.DIALING, AgentState.CONNECTED, AgentState.AVAILABLE, AgentState.OFFLINE},
    AgentState.DIALING: {AgentState.CONNECTED, AgentState.AVAILABLE, AgentState.OFFLINE},
    AgentState.CONNECTED: {AgentState.WRAP_UP, AgentState.OFFLINE},
    AgentState.WRAP_UP: {AgentState.AVAILABLE, AgentState.PAUSED, AgentState.OFFLINE},
    AgentState.PAUSED: {AgentState.AVAILABLE, AgentState.OFFLINE},
}


class InvalidAgentTransition(Exception):
    pass


def agent_transition(current: AgentState, target: AgentState) -> AgentState:
    if target == current:
        return current
    allowed = _AGENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidAgentTransition(f"{current} -> {target} is not allowed")
    return target


# ---- Call transitions (rank based, idempotent, order tolerant) --------

_CALL_RANK = {
    CallState.QUEUED: 0,
    CallState.RESERVED: 1,
    CallState.INITIATED: 2,
    CallState.RINGING: 3,
    CallState.ANSWERED: 4,
    CallState.CONNECTED: 5,
    CallState.COMPLETED: 6,
    CallState.FAILED: 6,
    CallState.CANCELLED: 6,
    CallState.ABANDONED: 6,
}

_TERMINAL = {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED, CallState.ABANDONED}


@dataclass
class TransitionResult:
    applied: bool
    new_state: CallState
    reason: str


def is_terminal(state: CallState) -> bool:
    return state in _TERMINAL


def apply_call_event(call: Call, event_id: str, target_state: CallState) -> TransitionResult:
    """Apply a provider event to a call, defensively.

    Returns a TransitionResult describing whether the state actually
    changed. Never raises on a duplicate/out-of-order/unexpected
    event - that's the whole point: external systems misbehave, we
    don't crash, and we don't corrupt state.
    """
    if event_id in call.seen_event_ids:
        return TransitionResult(False, call.state, "duplicate event_id, ignored")
    call.seen_event_ids.add(event_id)

    if is_terminal(call.state):
        return TransitionResult(False, call.state, f"call already terminal ({call.state}), event ignored")

    current_rank = _CALL_RANK[call.state]
    target_rank = _CALL_RANK[target_state]

    if target_rank < current_rank:
        return TransitionResult(False, call.state,
                                 f"stale/out-of-order event {target_state} < {call.state}, ignored")

    if target_state == call.state:
        return TransitionResult(False, call.state, "repeat of current state, no-op")

    if target_rank == current_rank:
        # e.g. two different terminal-ish states racing - first one to
        # land wins, later same-rank events are dropped.
        return TransitionResult(False, call.state, f"same-rank conflicting event {target_state}, first result wins")

    call.state = target_state
    call.version += 1
    return TransitionResult(True, target_state, f"advanced to {target_state}")
