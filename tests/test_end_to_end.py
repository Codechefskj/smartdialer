"""Integration test: run a short simulation and assert the core
safety invariants hold - that matters more than any particular
throughput number."""
from smartdialer.simulator import run_scenario


def test_predictive_run_keeps_safety_invariants():
    metrics = run_scenario("B", mode="predictive", num_agents=10, num_borrowers=200,
                            num_workers=3, duration=3.0, provider_name="b", verbose=False)
    assert metrics.calls_initiated > 0
    assert all(0.0 <= u <= 1.0 for _, u in metrics.timeline)
    assert metrics.calls_abandoned == 0


def test_progressive_run_starts_calls():
    metrics = run_scenario("B", mode="progressive", num_agents=10, num_borrowers=200,
                            num_workers=3, duration=3.0, provider_name="a", verbose=False)
    assert metrics.calls_initiated > 0


class SilentProvider:
    name = "silent"

    def place_call(self, call_id, phone_number, event_sink, expected_answer, talk_time_seconds):
        pass

    def cancel_call(self, call_id, event_sink):
        from smartdialer.events import new_event
        from smartdialer.models import CallState
        event_sink(new_event(call_id, CallState.CANCELLED, 999999, 0.0))

    def health(self):
        return 1.0


def test_provider_timeout_is_reconciled_and_capacity_released():
    from smartdialer.campaign import build_campaign
    from smartdialer.safety_controller import SafetyController
    from smartdialer.store import Store
    from smartdialer.worker import DialerWorker, Metrics
    import time

    store = Store()
    build_campaign(store, "camp-timeout", 1, 2)
    safety = SafetyController(max_ramp_per_tick=100)
    metrics = Metrics()
    worker = DialerWorker("w1", "camp-timeout", store, SilentProvider(), safety, metrics,
                          mode="predictive", tick_seconds=0.01, call_setup_timeout=0.05)
    worker.run_once()
    time.sleep(0.07)
    worker._recover_stuck_calls()
    assert metrics.calls_cancelled == 1
    assert safety.predictive_in_flight == 0
    assert store.pending_borrower_count("camp-timeout") == 2


def test_predictive_answer_race_never_enters_connected_without_agent():
    from smartdialer.campaign import build_campaign
    from smartdialer.safety_controller import SafetyController
    from smartdialer.store import Store
    from smartdialer.worker import DialerWorker, Metrics
    from smartdialer.models import CallState
    from smartdialer.events import new_event

    store = Store()
    build_campaign(store, "camp-race", 1, 2)
    safety = SafetyController(max_ramp_per_tick=100)
    metrics = Metrics()
    worker = DialerWorker("w1", "camp-race", store, SilentProvider(), safety, metrics, mode="predictive")
    borrower = store.reserve_next_borrower("camp-race", "w1")
    call = store.create_call("camp-race", borrower.id, "silent", "predictive", "w1")
    call.state = CallState.RINGING
    worker._event_sink(new_event(call.id, CallState.ANSWERED, 1, 0.0))
    assert call.state in (CallState.ANSWERED, CallState.CONNECTED)
    assert call.agent_id is not None
    assert metrics.calls_abandoned == 0
