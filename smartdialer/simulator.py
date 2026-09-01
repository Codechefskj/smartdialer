"""Simulation harness: runs a campaign under a chosen scenario and
reports the metrics requested by the assignment - agent utilization,
calls initiated/connected, pacing, and safety-controller decisions."""
from __future__ import annotations
import argparse
import threading
import time
from dataclasses import dataclass

from .campaign import build_campaign
from .providers.provider_a import ProviderA
from .providers.provider_b import ProviderB
from .safety_controller import SafetyController
from .store import Store
from .worker import DialerWorker, Metrics


@dataclass
class Scenario:
    name: str
    answer_rate: float
    talk_time_seconds: float
    changing: bool = False


SCENARIOS = {
    "A": Scenario("A", 0.20, 1.2),
    "B": Scenario("B", 0.50, 0.9),
    "C": Scenario("C", 0.70, 1.8),
    "D": Scenario("D", 0.40, 1.2, changing=True),
}


def run_scenario(scenario_key: str, mode: str = "predictive", num_agents: int = 20,
                  num_borrowers: int = 400, num_workers: int = 3, duration: float = 12.0,
                  provider_name: str = "b", verbose: bool = True) -> Metrics:
    scenario = SCENARIOS[scenario_key]
    store = Store()
    campaign_id = "camp-1"
    build_campaign(store, campaign_id, num_agents, num_borrowers)

    provider = ProviderB() if provider_name == "b" else ProviderA()
    safety = SafetyController()
    metrics = Metrics()

    current_answer_rate = [scenario.answer_rate]
    tick_counter = [0]

    def answer_rate_fn():
        if scenario.changing:
            tick_counter[0] += 1
            # drift the answer rate up and down over the run to
            # exercise the feedback loop (simulates a shifting
            # campaign / time-of-day effect)
            direction = 1 if (tick_counter[0] // 8) % 2 == 0 else -1
            current_answer_rate[0] = max(0.05, min(0.9, current_answer_rate[0] + direction * 0.01))
        return current_answer_rate[0]

    def talk_time_fn():
        if scenario.changing:
            # Change talk time in the opposite half of the cycle so D
            # exercises both changing answer rate and changing duration.
            phase = (tick_counter[0] // 8) % 3
            return (0.9, 1.2, 1.8)[phase]
        return scenario.talk_time_seconds

    workers = [
        DialerWorker(f"worker-{i + 1}", campaign_id, store, provider, safety, metrics,
                     mode=mode, answer_rate_fn=answer_rate_fn, talk_time_fn=talk_time_fn,
                     tick_seconds=0.4)
        for i in range(num_workers)
    ]

    threads = [threading.Thread(target=w.run, args=(duration,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    time.sleep(0.5)  # allow final provider events from short simulated calls
    if verbose:
        print_report(scenario_key, mode, provider.name, metrics, num_agents)
    return metrics


def print_report(scenario_key, mode, provider_name, metrics: Metrics, num_agents: int) -> None:
    util = [u for _, u in metrics.timeline]
    avg_util = sum(util) / len(util) if util else 0.0
    print(f"\n=== Scenario {scenario_key} | mode={mode} | provider={provider_name} | agents={num_agents} ===")
    print(f"calls_initiated   : {metrics.calls_initiated}")
    print(f"calls_connected   : {metrics.calls_connected}")
    print(f"calls_completed   : {metrics.calls_completed}")
    print(f"calls_failed      : {metrics.calls_failed}")
    print(f"calls_abandoned   : {metrics.calls_abandoned}")
    print(f"avg agent utilization: {avg_util:.1%}")
    print(f"safety decisions logged: {len(metrics.safety_log)}")
    if metrics.safety_log:
        last = metrics.safety_log[-1]
        print(f"last safety decision: requested={last['requested']} approved={last['approved']}")
        print(f"  reasons: {last['reasons']}")


def main():
    parser = argparse.ArgumentParser(description="SmartDialer simulator")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="B")
    parser.add_argument("--mode", choices=["progressive", "predictive"], default="predictive")
    parser.add_argument("--agents", type=int, default=20)
    parser.add_argument("--borrowers", type=int, default=400)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--provider", choices=["a", "b"], default="b")
    parser.add_argument("--all", action="store_true", help="run all 4 scenarios back to back")
    args = parser.parse_args()

    if args.all:
        for key in SCENARIOS:
            run_scenario(key, args.mode, args.agents, args.borrowers, args.workers, args.duration, args.provider)
    else:
        run_scenario(args.scenario, args.mode, args.agents, args.borrowers, args.workers, args.duration, args.provider)


if __name__ == "__main__":
    main()
