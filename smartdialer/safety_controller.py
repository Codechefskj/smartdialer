"""Deterministic safety boundary between pacing and call placement.

The controller owns a shared predictive dial-capacity ledger.  A predictive
approval consumes a capacity token and the token is released when the call
terminates.  This closes the multi-worker race where several workers could
independently approve the same currently-available agents.
"""
from __future__ import annotations
from dataclasses import dataclass
import threading


@dataclass
class SafetyDecision:
    approved_count: int
    fell_back_to_progressive: bool
    reasons: list


class SafetyController:
    def __init__(self, max_abandon_rate: float = 0.03,
                 min_provider_health: float = 0.5,
                 max_ramp_per_tick: int = 10,
                 cooldown_ticks: int = 5):
        self.max_abandon_rate = max_abandon_rate
        self.min_provider_health = min_provider_health
        self.max_ramp_per_tick = max_ramp_per_tick
        self._last_approved = 0
        self._cooldown_remaining = 0
        self.cooldown_ticks = cooldown_ticks
        self.predictive_enabled = True
        self._predictive_in_flight = 0
        self._lock = threading.Lock()

    @property
    def predictive_in_flight(self) -> int:
        with self._lock:
            return self._predictive_in_flight

    def force_disable_predictive(self, ticks: int = 5) -> None:
        with self._lock:
            self.predictive_enabled = False
            self._cooldown_remaining = max(self._cooldown_remaining, ticks)

    def tick_cooldown(self) -> None:
        with self._lock:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                if self._cooldown_remaining == 0:
                    self.predictive_enabled = True

    def release_predictive_capacity(self, count: int = 1) -> None:
        with self._lock:
            self._predictive_in_flight = max(0, self._predictive_in_flight - count)

    def evaluate(self, requested: int, available_agents: int,
                 abandon_rate: float, provider_health: float,
                 mode: str = "predictive") -> SafetyDecision:
        reasons = []
        approved = max(0, requested)
        fell_back = False

        with self._lock:
            if abandon_rate > self.max_abandon_rate:
                self.predictive_enabled = False
                self._cooldown_remaining = max(self._cooldown_remaining, self.cooldown_ticks)
                reasons.append(
                    f"abandon_rate {abandon_rate:.2%} > {self.max_abandon_rate:.2%}, "
                    f"disabling predictive dialing for {self.cooldown_ticks} ticks")

            # Progressive calls always require a real agent reservation before
            # dialing. Predictive calls consume shared capacity tokens here so
            # multiple workers cannot each approve the same agent capacity.
            capacity = max(0, available_agents)
            if mode == "predictive" and self.predictive_enabled:
                capacity = max(0, available_agents - self._predictive_in_flight)
                if approved > capacity:
                    reasons.append(
                        f"capacity-limited to {capacity} (available_agents={available_agents}, "
                        f"predictive_in_flight={self._predictive_in_flight})")
                    approved = capacity
                self._predictive_in_flight += approved
            else:
                if mode == "predictive" and not self.predictive_enabled:
                    fell_back = True
                    reasons.append("predictive disabled - falling back to progressive (1:1) behaviour")
                if approved > capacity:
                    reasons.append(f"capped to available_agents={capacity} (requested {requested})")
                    approved = capacity

            if provider_health < self.min_provider_health:
                reduced = max(0, round(approved * provider_health))
                if reduced < approved:
                    reasons.append(
                        f"provider_health={provider_health:.2f} below threshold, "
                        f"reducing {approved} -> {reduced}")
                    if mode == "predictive" and self.predictive_enabled:
                        self._predictive_in_flight -= approved - reduced
                    approved = reduced

            if approved - self._last_approved > self.max_ramp_per_tick:
                capped = self._last_approved + self.max_ramp_per_tick
                reasons.append(f"ramp-limited {approved} -> {capped} "
                               f"(max_ramp_per_tick={self.max_ramp_per_tick})")
                if mode == "predictive" and self.predictive_enabled:
                    self._predictive_in_flight -= approved - capped
                approved = capped

            approved = max(0, approved)
            self._last_approved = approved

            if not reasons:
                reasons.append(f"approved as requested ({approved})")

        self.tick_cooldown()
        return SafetyDecision(approved_count=approved,
                              fell_back_to_progressive=fell_back,
                              reasons=reasons)
