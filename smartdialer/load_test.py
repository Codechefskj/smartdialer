"""A basic load test - not a real 10k-call/sec test (out of scope for
a laptop prototype), but a way to show WHERE the current in-memory
design starts to contend, per the assignment's request to reason
about scale from 100 -> 1,000 -> 10,000 agents. Every predictive-mode
answer event and every progressive dial performs an agent
reservation, so that operation is the core scalability lever, which
is why this benchmarks exactly that."""
from __future__ import annotations
import threading
import time

from .models import AgentState
from .store import Store


def bench_reserve_agent(num_agents: int, num_workers: int, attempts_per_worker: int) -> None:
    store = Store()
    campaign_id = "load-camp"
    for _ in range(num_agents):
        store.add_agent(campaign_id)

    results = {"success": 0, "fail": 0}
    lock = threading.Lock()

    def worker(_):
        local_success = 0
        local_fail = 0
        for _ in range(attempts_per_worker):
            agent = store.reserve_any_available_agent(campaign_id, worker_id="load")
            if agent:
                local_success += 1
                store.set_agent_state(agent.id, AgentState.AVAILABLE)
            else:
                local_fail += 1
        with lock:
            results["success"] += local_success
            results["fail"] += local_fail

    start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    total_ops = num_workers * attempts_per_worker
    print(f"agents={num_agents:>6} workers={num_workers:>4} ops={total_ops:>7} "
          f"time={elapsed:.3f}s throughput={total_ops / elapsed:,.0f} ops/sec "
          f"(success={results['success']}, contention_misses={results['fail']})")


def main():
    print("Reservation throughput vs. agent-pool size and worker concurrency:\n")
    for num_agents in (100, 1000, 10000):
        for num_workers in (1, 4, 16):
            bench_reserve_agent(num_agents, num_workers, attempts_per_worker=200)
        print()


if __name__ == "__main__":
    main()
