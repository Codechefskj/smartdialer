from smartdialer.models import Call, CallState, AgentState
from smartdialer.state_machines import apply_call_event, agent_transition, InvalidAgentTransition


def make_call():
    return Call(id="c1", campaign_id="camp", borrower_id="b1", provider_name="p", mode="progressive")


def test_happy_path_progression():
    call = make_call()
    for i, state in enumerate([CallState.INITIATED, CallState.RINGING, CallState.ANSWERED,
                                CallState.CONNECTED, CallState.COMPLETED]):
        result = apply_call_event(call, f"evt-{i}", state)
        assert result.applied
        assert call.state == state


def test_duplicate_events_are_idempotent():
    call = make_call()
    apply_call_event(call, "e1", CallState.INITIATED)
    apply_call_event(call, "e2", CallState.RINGING)
    apply_call_event(call, "e3", CallState.ANSWERED)
    r1 = apply_call_event(call, "e3", CallState.ANSWERED)  # exact duplicate event id
    assert not r1.applied
    r2 = apply_call_event(call, "e4", CallState.ANSWERED)  # same state, different id
    assert not r2.applied
    assert call.state == CallState.ANSWERED


def test_out_of_order_events_never_go_backwards():
    call = make_call()
    apply_call_event(call, "e1", CallState.INITIATED)
    apply_call_event(call, "e2", CallState.RINGING)
    apply_call_event(call, "e3", CallState.ANSWERED)
    apply_call_event(call, "e4", CallState.CONNECTED)
    apply_call_event(call, "e5", CallState.COMPLETED)
    result = apply_call_event(call, "e6", CallState.RINGING)  # stale event shows up late
    assert not result.applied
    assert call.state == CallState.COMPLETED


def test_terminal_state_absorbs_everything_after():
    call = make_call()
    apply_call_event(call, "e1", CallState.FAILED)
    for i, s in enumerate([CallState.ANSWERED, CallState.CONNECTED, CallState.COMPLETED]):
        r = apply_call_event(call, f"post-{i}", s)
        assert not r.applied
    assert call.state == CallState.FAILED


def test_provider_b_style_sequence_completed_answered_ringing():
    call = make_call()
    apply_call_event(call, "e1", CallState.INITIATED)
    apply_call_event(call, "e2", CallState.RINGING)
    apply_call_event(call, "e3", CallState.ANSWERED)
    apply_call_event(call, "e4", CallState.CONNECTED)
    apply_call_event(call, "e5", CallState.COMPLETED)
    apply_call_event(call, "e6", CallState.ANSWERED)  # arrives late
    apply_call_event(call, "e7", CallState.RINGING)   # arrives even later
    assert call.state == CallState.COMPLETED  # never regresses


def test_agent_transitions_enforced():
    assert agent_transition(AgentState.AVAILABLE, AgentState.RESERVED) == AgentState.RESERVED
    try:
        agent_transition(AgentState.AVAILABLE, AgentState.CONNECTED)
        assert False, "should have raised"
    except InvalidAgentTransition:
        pass


def test_connected_before_answered_is_monotonic_and_terminally_consistent():
    call = make_call()
    apply_call_event(call, "e1", CallState.INITIATED)
    apply_call_event(call, "e2", CallState.RINGING)
    assert apply_call_event(call, "e3", CallState.CONNECTED).applied
    late = apply_call_event(call, "e4", CallState.ANSWERED)
    assert not late.applied
    assert call.state == CallState.CONNECTED
