"""Predictive pacer.

This module NEVER touches an agent, a call, or a provider. It only
produces a *suggestion*: "I think we can start N more calls." That
suggestion is inert until SafetyController approves it (see
safety_controller.py) - the predictive algorithm has no code path
capable of placing a call itself.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


def _ewma(prev: float, new: float, alpha: float) -> float:
    return alpha * new + (1 - alpha) * prev


@dataclass
class PacingSuggestion:
    count: int
    reasoning: str


@dataclass
class PredictiveStats:
    """Rolling statistics the predictive pacer bases its guess on.
    Updated live by the worker as calls resolve - see worker.py."""
    answer_rate: float = 0.3          # EWMA of answered / dialed
    avg_talk_time: float = 120.0      # seconds
    avg_setup_time: float = 3.0       # seconds from dial to answer/fail
    alpha: float = 0.2
    recent_abandon_flags: List[int] = field(default_factory=list)

    def record_outcome(self, answered: bool):
        self.answer_rate = _ewma(self.answer_rate, 1.0 if answered else 0.0, self.alpha)

    def record_talk_time(self, seconds: float):
        self.avg_talk_time = _ewma(self.avg_talk_time, seconds, self.alpha)

    def record_setup_time(self, seconds: float):
        self.avg_setup_time = _ewma(self.avg_setup_time, seconds, self.alpha)

    def record_abandon(self, abandoned: bool):
        self.recent_abandon_flags.append(1 if abandoned else 0)
        self.recent_abandon_flags = self.recent_abandon_flags[-100:]

    @property
    def abandon_rate(self) -> float:
        if not self.recent_abandon_flags:
            return 0.0
        return sum(self.recent_abandon_flags) / len(self.recent_abandon_flags)


class PredictivePacer:
    """Classic pacing-ratio formula, kept intentionally simple and
    explainable rather than an opaque ML model:

        pacing_ratio = clamp(1 / answer_rate, min_ratio, max_ratio) * margin * provider_health

    If only 1 in `answer_rate` calls gets answered, we need to dial
    roughly that many times our agent capacity to keep agents busy -
    minus calls already in flight (dialing/ringing, not yet
    answered) so we don't double count them - shaved down by a
    safety margin and by provider health.
    """

    def __init__(self, max_ratio: float = 4.0, safety_margin: float = 0.85,
                 min_ratio: float = 1.0):
        self.max_ratio = max_ratio
        self.safety_margin = safety_margin
        self.min_ratio = min_ratio

    def suggest(self, stats: PredictiveStats, available_agents: int,
                calls_in_flight: int, pending_borrowers: int,
                provider_health: float) -> PacingSuggestion:
        eps = 0.02
        raw_ratio = 1.0 / max(stats.answer_rate, eps)
        ratio = min(self.max_ratio, max(self.min_ratio, raw_ratio)) * self.safety_margin
        ratio *= max(0.25, provider_health)

        target_concurrent_dials = available_agents * ratio
        count = round(target_concurrent_dials - calls_in_flight)
        count = max(0, min(count, pending_borrowers))

        reasoning = (
            f"answer_rate={stats.answer_rate:.2f} -> raw_ratio={raw_ratio:.2f}, "
            f"clamped_ratio={ratio:.2f} (margin={self.safety_margin}, "
            f"provider_health={provider_health:.2f}), "
            f"available_agents={available_agents}, in_flight={calls_in_flight} "
            f"=> target={target_concurrent_dials:.1f} -> suggest {count} "
            f"(pending_borrowers={pending_borrowers}, abandon_rate={stats.abandon_rate:.2%})"
        )
        return PacingSuggestion(count=count, reasoning=reasoning)
