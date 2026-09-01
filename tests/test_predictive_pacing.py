from smartdialer.pacing.predictive import PredictivePacer, PredictiveStats


def test_low_answer_rate_increases_ratio():
    pacer = PredictivePacer()
    stats = PredictiveStats(answer_rate=0.1)
    s = pacer.suggest(stats, available_agents=10, calls_in_flight=0, pending_borrowers=100, provider_health=1.0)
    assert s.count > 10  # low answer rate should push overdialing above 1:1


def test_high_answer_rate_stays_near_1_to_1():
    pacer = PredictivePacer()
    stats = PredictiveStats(answer_rate=0.95)
    s = pacer.suggest(stats, available_agents=10, calls_in_flight=0, pending_borrowers=100, provider_health=1.0)
    assert s.count <= 11


def test_bad_provider_health_reduces_suggestion():
    pacer = PredictivePacer()
    stats = PredictiveStats(answer_rate=0.2)
    healthy = pacer.suggest(stats, 10, 0, 100, provider_health=1.0)
    unhealthy = pacer.suggest(stats, 10, 0, 100, provider_health=0.2)
    assert unhealthy.count < healthy.count


def test_never_exceeds_pending_borrowers():
    pacer = PredictivePacer()
    stats = PredictiveStats(answer_rate=0.05)
    s = pacer.suggest(stats, available_agents=100, calls_in_flight=0, pending_borrowers=3, provider_health=1.0)
    assert s.count <= 3
