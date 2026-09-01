"""CallAllocator: the only component that talks to a TelecomProvider.
Given a SafetyController-approved count, it claims borrowers (and,
in progressive mode, agents) from the store and starts calls."""
from __future__ import annotations
import time
from .models import AgentState, CallState
from .providers.base import TelecomProvider
from .store import Store


class CallAllocator:
    def __init__(self, store: Store, provider: TelecomProvider, worker_id: str, setup_timeout_seconds: float = 2.0):
        self.store = store
        self.provider = provider
        self.worker_id = worker_id
        self.setup_timeout_seconds = setup_timeout_seconds

    def start_progressive_calls(self, campaign_id: str, count: int,
                                 event_sink, answer_fn, talk_time_fn) -> int:
        """Progressive mode binds one agent to one borrower BEFORE
        dialing - this is what makes it inherently safe: we can never
        have more agent-bound outbound calls than agents."""
        started = 0
        for _ in range(count):
            agent = self.store.reserve_any_available_agent(campaign_id, self.worker_id)
            if agent is None:
                self.store.event(f"{self.worker_id}: no agent available, stopping progressive batch")
                break
            borrower = self.store.reserve_next_borrower(campaign_id, self.worker_id)
            if borrower is None:
                self.store.set_agent_state(agent.id, AgentState.AVAILABLE)
                self.store.event(f"{self.worker_id}: no borrower available, released agent {agent.id}")
                break

            call = self.store.create_call(campaign_id, borrower.id, self.provider.name,
                                           mode="progressive", worker_id=self.worker_id, agent_id=agent.id)
            call.state = CallState.RESERVED
            call.initiated_at = time.monotonic()
            call.setup_timeout_seconds = self.setup_timeout_seconds
            self.store.set_agent_state(agent.id, AgentState.DIALING)
            self.store.bind_agent_to_call(agent.id, call.id)
            self.store.event(f"{self.worker_id}: progressive dial call={call.id} "
                              f"agent={agent.id} borrower={borrower.id}")

            self.provider.place_call(
                call_id=call.id, phone_number=borrower.phone, event_sink=event_sink,
                expected_answer=answer_fn(), talk_time_seconds=talk_time_fn(),
            )
            started += 1
        return started

    def start_predictive_calls(self, campaign_id: str, count: int,
                                event_sink, answer_fn, talk_time_fn) -> int:
        """Predictive mode deliberately does NOT bind an agent up
        front - that's the whole point of overdialing. The agent is
        claimed only if/when the call is answered - see
        worker.py:_bind_agent_on_answer - and abandoned immediately
        if none is free."""
        started = 0
        for _ in range(count):
            borrower = self.store.reserve_next_borrower(campaign_id, self.worker_id)
            if borrower is None:
                break
            call = self.store.create_call(campaign_id, borrower.id, self.provider.name,
                                           mode="predictive", worker_id=self.worker_id, agent_id=None)
            call.state = CallState.RESERVED
            call.initiated_at = time.monotonic()
            call.setup_timeout_seconds = self.setup_timeout_seconds
            self.store.event(f"{self.worker_id}: predictive dial call={call.id} borrower={borrower.id}")
            self.provider.place_call(
                call_id=call.id, phone_number=borrower.phone, event_sink=event_sink,
                expected_answer=answer_fn(), talk_time_seconds=talk_time_fn(),
            )
            started += 1
        return started
