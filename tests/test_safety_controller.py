from smartdialer.safety_controller import SafetyController


def test_never_exceeds_available_agents():
    sc = SafetyController()
    decision = sc.evaluate(requested=999, available_agents=50, abandon_rate=0.0, provider_health=1.0)
    assert decision.approved_count <= 50


def test_abandon_rate_triggers_fallback():
    sc = SafetyController(max_abandon_rate=0.03, cooldown_ticks=2)
    d1 = sc.evaluate(requested=30, available_agents=50, abandon_rate=0.10, provider_health=1.0)
    assert d1.fell_back_to_progressive or "disabling predictive" in " ".join(d1.reasons)
    d2 = sc.evaluate(requested=30, available_agents=50, abandon_rate=0.0, provider_health=1.0)
    assert d2.fell_back_to_progressive  # still in cooldown


def test_provider_health_reduces_dial_count():
    sc = SafetyController(min_provider_health=0.5)
    d = sc.evaluate(requested=20, available_agents=50, abandon_rate=0.0, provider_health=0.2)
    assert d.approved_count < 20


def test_ramp_limit():
    sc = SafetyController(max_ramp_per_tick=5)
    sc.evaluate(requested=0, available_agents=50, abandon_rate=0.0, provider_health=1.0)
    d = sc.evaluate(requested=40, available_agents=50, abandon_rate=0.0, provider_health=1.0)
    assert d.approved_count <= 5


def test_shared_predictive_capacity_prevents_multi_worker_overapproval():
    sc = SafetyController(max_ramp_per_tick=100)
    d1 = sc.evaluate(requested=10, available_agents=10, abandon_rate=0.0, provider_health=1.0,
                     mode="predictive")
    d2 = sc.evaluate(requested=10, available_agents=10, abandon_rate=0.0, provider_health=1.0,
                     mode="predictive")
    assert d1.approved_count == 10
    assert d2.approved_count == 0
    assert sc.predictive_in_flight == 10
    sc.release_predictive_capacity(10)
    assert sc.predictive_in_flight == 0


def test_provider_health_reduction_releases_unused_capacity():
    sc = SafetyController(max_ramp_per_tick=100)
    d = sc.evaluate(requested=10, available_agents=10, abandon_rate=0.0, provider_health=0.4,
                    mode="predictive")
    assert d.approved_count == 4
    assert sc.predictive_in_flight == 4
