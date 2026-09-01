"""Dialer worker: pacing -> safety -> allocation, plus defensive provider
webhook handling and recovery of calls that never receive a provider event."""
from __future__ import annotations
import random
import threading
import time
from typing import Callable, Optional

from .allocator import CallAllocator
from .events import ProviderEvent
from .models import AgentState, BorrowerState, CallState
from .pacing.predictive import PredictiveStats, PredictivePacer
from .pacing.progressive import ProgressivePacer
from .providers.base import TelecomProvider
from .safety_controller import SafetyController
from .state_machines import apply_call_event, is_terminal
from .store import Store


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls_initiated = 0
        self.calls_connected = 0
        self.calls_abandoned = 0
        self.calls_failed = 0
        self.calls_completed = 0
        self.calls_cancelled = 0
        self.safety_log = []
        self.timeline = []

    def inc(self, field_name: str, n: int = 1):
        with self.lock:
            setattr(self, field_name, getattr(self, field_name) + n)

    def record_safety(self, decision_reasons, requested, approved):
        with self.lock:
            self.safety_log.append({"t": time.monotonic(), "requested": requested,
                                    "approved": approved, "reasons": list(decision_reasons)})

    def sample_utilization(self, util: float):
        with self.lock:
            self.timeline.append((time.monotonic(), util))


class DialerWorker:
    def __init__(self, worker_id: str, campaign_id: str, store: Store,
                 provider: TelecomProvider, safety: SafetyController,
                 metrics: Metrics, mode: str = "predictive",
                 answer_rate_fn: Optional[Callable[[], float]] = None,
                 talk_time_fn: Optional[Callable[[], float]] = None,
                 tick_seconds: float = 0.5,
                 call_setup_timeout: float = 2.0):
        self.worker_id = worker_id
        self.campaign_id = campaign_id
        self.store = store
        self.provider = provider
        self.safety = safety
        self.metrics = metrics
        self.mode = mode
        self.answer_rate_fn = answer_rate_fn or (lambda: 0.3)
        self.talk_time_fn = talk_time_fn or (lambda: 2.0)
        self.tick_seconds = tick_seconds
        self.call_setup_timeout = call_setup_timeout

        self.allocator = CallAllocator(store, provider, worker_id, setup_timeout_seconds=call_setup_timeout)
        self.progressive_pacer = ProgressivePacer()
        self.predictive_pacer = PredictivePacer()
        self.stats = PredictiveStats()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _release_safety_capacity(self, call) -> None:
        if call.mode != "predictive" or call.safety_capacity_released:
            return
        call.safety_capacity_released = True
        self.safety.release_predictive_capacity(1)

    def _cancel_safely(self, call, reason: str) -> None:
        """Make a predictive call terminal WITHOUT ever entering ANSWERED or
        CONNECTED when no agent can be reserved. This is the deterministic
        safety boundary: an answered provider event cannot create an
        abandoned connected call."""
        result = apply_call_event(call, f"safety-cancel-{call.id}-{time.monotonic_ns()}", CallState.CANCELLED)
        if result.applied:
            self.metrics.inc("calls_cancelled")
            self._release_after_call(call, borrower_state=BorrowerState.PENDING)
            self._release_safety_capacity(call)
            self.store.event(f"{self.worker_id}: SAFETY CANCEL call={call.id}: {reason}")
        self.provider.cancel_call(call.id, self._event_sink)

    # -- provider event handling --------------------------------------------
    def _event_sink(self, event: ProviderEvent) -> None:
        call = self.store.get_call(event.call_id)
        if call is None:
            return
        # Provider webhooks and the timeout/recovery path can race. Serialize
        # the entire call decision so a terminal event cannot interleave with
        # an ANSWERED/CONNECTED agent reservation and leak capacity.
        with self.store.call_lock(call.id):
            if is_terminal(call.state):
                return

            # In predictive mode, reserve an actual agent BEFORE applying an
            # ANSWERED/CONNECTED event. If no agent exists, cancel instead of
            # allowing the call to enter a connected state without an agent.
            if (event.state in (CallState.ANSWERED, CallState.CONNECTED)
                    and call.mode == "predictive" and call.agent_id is None):
                agent = self.store.reserve_any_available_agent(self.campaign_id, self.worker_id)
                if agent is None:
                    self._cancel_safely(call, "provider answered/connected but no agent capacity was available")
                    return
                self.store.set_call_agent(call.id, agent.id)
                self.store.set_agent_state(agent.id, AgentState.CONNECTED)
                self.store.bind_agent_to_call(agent.id, call.id)
                call.connected_at = time.monotonic()
                self.stats.record_abandon(False)
                self.metrics.inc("calls_connected")

            result = apply_call_event(call, event.event_id, event.state)
            self.store.event(f"{self.worker_id}: event {event.state} for call {call.id} -> "
                             f"applied={result.applied} ({result.reason})")
            if not result.applied:
                return

            if event.state == CallState.ANSWERED:
                if call.connected_at is None:
                    call.connected_at = time.monotonic()
                    self.metrics.inc("calls_connected")
                if call.initiated_at:
                    self.stats.record_setup_time(time.monotonic() - call.initiated_at)
                self.stats.record_outcome(answered=True)
            elif event.state == CallState.FAILED:
                self.metrics.inc("calls_failed")
                if call.initiated_at:
                    self.stats.record_setup_time(time.monotonic() - call.initiated_at)
                self.stats.record_outcome(answered=False)
                self._release_after_call(call, borrower_state=BorrowerState.PENDING)
                self._release_safety_capacity(call)
            elif event.state == CallState.COMPLETED:
                self.metrics.inc("calls_completed")
                self._release_after_call(call, borrower_state=BorrowerState.CALLED)
                self._release_safety_capacity(call)
            elif event.state == CallState.CANCELLED:
                self.metrics.inc("calls_cancelled")
                self._release_after_call(call, borrower_state=BorrowerState.PENDING)
                self._release_safety_capacity(call)

    def _release_after_call(self, call, borrower_state: BorrowerState) -> None:
        self.store.release_borrower(call.borrower_id, borrower_state)
        if call.agent_id is not None:
            agent = self.store.get_agent(call.agent_id)
            if agent and agent.state == AgentState.CONNECTED:
                self.store.set_agent_state(call.agent_id, AgentState.WRAP_UP)
                threading.Timer(0.2, self._finish_wrap_up, args=(call.agent_id,)).start()
            elif agent and agent.state == AgentState.DIALING:
                self.store.set_agent_state(call.agent_id, AgentState.AVAILABLE)
            elif agent and agent.state == AgentState.RESERVED:
                self.store.set_agent_state(call.agent_id, AgentState.AVAILABLE)
        if call.connected_at:
            self.stats.record_talk_time(time.monotonic() - call.connected_at)

    def _finish_wrap_up(self, agent_id: str) -> None:
        agent = self.store.get_agent(agent_id)
        if agent and agent.state == AgentState.WRAP_UP:
            self.store.set_agent_state(agent_id, AgentState.AVAILABLE)

    def _recover_stuck_calls(self) -> None:
        now = time.monotonic()
        for call in self.store.active_calls(self.campaign_id):
            if is_terminal(call.state):
                continue
            started = call.initiated_at or call.created_at
            if now - started >= call.setup_timeout_seconds:
                self._cancel_safely(call, f"provider setup timeout after {call.setup_timeout_seconds:.1f}s")

    # -- main loop ------------------------------------------------------------
    def run_once(self) -> None:
        self.store.reconcile_stale_reservations()
        self._recover_stuck_calls()

        available = self.store.available_agent_count(self.campaign_id)
        pending = self.store.pending_borrower_count(self.campaign_id)
        in_flight = self.store.active_predictive_call_count(self.campaign_id)
        health = self.provider.health()

        if self.mode == "progressive":
            suggestion = self.progressive_pacer.suggest(available, pending)
        else:
            suggestion = self.predictive_pacer.suggest(
                self.stats, available_agents=available, calls_in_flight=in_flight,
                pending_borrowers=pending, provider_health=health,
            )

        decision = self.safety.evaluate(
            requested=suggestion.count, available_agents=available,
            abandon_rate=self.stats.abandon_rate, provider_health=health,
            mode=self.mode,
        )
        self.metrics.record_safety(decision.reasons, suggestion.count, decision.approved_count)
        self.store.event(f"{self.worker_id}: pacer({self.mode}) says '{suggestion.reasoning}' "
                         f"| safety approved={decision.approved_count} reasons={decision.reasons}")

        if decision.approved_count <= 0:
            return

        answer_fn = lambda: random.random() < self.answer_rate_fn()
        talk_fn = self.talk_time_fn

        if self.mode == "progressive" or decision.fell_back_to_progressive:
            started = self.allocator.start_progressive_calls(
                self.campaign_id, decision.approved_count, self._event_sink, answer_fn, talk_fn)
        else:
            started = self.allocator.start_predictive_calls(
                self.campaign_id, decision.approved_count, self._event_sink, answer_fn, talk_fn)
            unused = decision.approved_count - started
            if unused:
                self.safety.release_predictive_capacity(unused)
        self.metrics.inc("calls_initiated", started)

    def run(self, duration_seconds: float) -> None:
        end = time.monotonic() + duration_seconds
        while time.monotonic() < end and not self._stop.is_set():
            self.run_once()
            busy = self.store.agents_in_state(
                self.campaign_id, AgentState.DIALING, AgentState.CONNECTED, AgentState.WRAP_UP)
            total = busy + self.store.available_agent_count(self.campaign_id)
            self.metrics.sample_utilization(busy / total if total else 0.0)
            time.sleep(self.tick_seconds)
