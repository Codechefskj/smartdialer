import threading
import time

from smartdialer.models import AgentState
from smartdialer.store import Store, ConflictError


def test_unsafe_reservation_can_double_book():
    """Demonstrates the bug that would exist WITHOUT per-record
    locking: two workers reading the same 'AVAILABLE' state before
    either has written 'RESERVED' back."""
    store = Store()
    agent = store.add_agent("camp")
    successes = []
    lock = threading.Lock()

    def worker():
        try:
            store.reserve_agent_unsafe(agent.id, "w")
            with lock:
                successes.append(1)
        except ConflictError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(successes) > 1  # double-booking reproduced


def test_safe_reservation_never_double_books():
    store = Store()
    agent = store.add_agent("camp")
    results = []
    lock = threading.Lock()

    def worker():
        got = store.reserve_any_available_agent("camp", "w")
        with lock:
            results.append(got is not None)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1  # exactly one worker wins
    assert agent.state == AgentState.RESERVED


def test_reconciliation_releases_stale_reservation():
    store = Store()
    agent = store.add_agent("camp")
    store.reserve_any_available_agent("camp", "w1")
    released = store.reconcile_stale_reservations(now=time.monotonic() + 999)
    assert released >= 1
    assert agent.state == AgentState.AVAILABLE
