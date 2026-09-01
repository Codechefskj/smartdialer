"""In-memory store standing in for a durable backend.

Every mutating operation here is written as an atomic
compare-and-swap guarded by a per-record lock. In production this
maps directly onto:

  - Postgres:  UPDATE ... WHERE id = ? AND state = 'AVAILABLE'
               (optimistic check), or SELECT ... FOR UPDATE / SKIP
               LOCKED (pessimistic row lock) for the same effect.
  - Redis:     SET key value NX PX <ttl>  (a lease), or a small Lua
               script doing check-then-set atomically.

Keeping the *shape* of this API identical to what a DB/Redis client
would offer means swapping the backend later doesn't change any
caller code - only this file. See ARCHITECTURE.md for the full
reasoning on why this is enough for the prototype and what changes
at higher scale.
"""
from __future__ import annotations
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional

from .models import Agent, AgentState, Borrower, BorrowerState, Call, next_id
from .state_machines import agent_transition


class ConflictError(Exception):
    """Raised when a reservation attempt loses a race to another worker."""


RESERVATION_TTL_SECONDS = 5.0  # lease length; see reconcile_stale_reservations()


class Store:
    def __init__(self):
        self._agents: Dict[str, Agent] = {}
        self._borrowers: Dict[str, Borrower] = {}
        self._calls: Dict[str, Call] = {}
        self._agent_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._borrower_queue_lock = threading.Lock()
        self._calls_lock = threading.Lock()
        self._call_locks: Dict[str, threading.RLock] = defaultdict(threading.RLock)
        self.log: List[str] = []
        self._log_lock = threading.Lock()

    # -- setup -----------------------------------------------------------
    def add_agent(self, campaign_id: str) -> Agent:
        agent = Agent(id=next_id("agent"), campaign_id=campaign_id)
        self._agents[agent.id] = agent
        return agent

    def add_borrower(self, campaign_id: str, phone: str) -> Borrower:
        b = Borrower(id=next_id("borrower"), campaign_id=campaign_id, phone=phone)
        self._borrowers[b.id] = b
        return b

    def event(self, msg: str) -> None:
        with self._log_lock:
            self.log.append(f"[{time.monotonic():.3f}] {msg}")

    # -- reads -------------------------------------------------------------
    def available_agent_count(self, campaign_id: str) -> int:
        return sum(1 for a in self._agents.values()
                    if a.campaign_id == campaign_id and a.state == AgentState.AVAILABLE)

    def agents_in_state(self, campaign_id: str, *states: AgentState) -> int:
        return sum(1 for a in self._agents.values()
                    if a.campaign_id == campaign_id and a.state in states)

    def pending_borrower_count(self, campaign_id: str) -> int:
        return sum(1 for b in self._borrowers.values()
                    if b.campaign_id == campaign_id and b.state == BorrowerState.PENDING)

    def get_call(self, call_id: str) -> Optional[Call]:
        return self._calls.get(call_id)

    def call_lock(self, call_id: str) -> threading.RLock:
        """Per-call lock used to serialize provider events and timeout recovery."""
        return self._call_locks[call_id]

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        return self._agents.get(agent_id)

    def active_calls(self, campaign_id: str):
        with self._calls_lock:
            return [c for c in self._calls.values() if c.campaign_id == campaign_id]

    def active_predictive_call_count(self, campaign_id: str) -> int:
        from .state_machines import is_terminal
        with self._calls_lock:
            return sum(1 for c in self._calls.values()
                       if c.campaign_id == campaign_id and c.mode == "predictive" and not is_terminal(c.state))

    # -- agent reservation (SAFE path: this is what real code uses) --------
    def reserve_any_available_agent(self, campaign_id: str, worker_id: str) -> Optional[Agent]:
        """Atomically claim ONE available agent for this campaign.

        We only ever mutate a record while holding *that record's*
        lock, and re-check its state under the lock before mutating
        (check-then-act made atomic). This is what prevents two
        workers/threads/nodes from reserving the same agent - see
        tests/test_concurrency.py for a direct A/B proof against the
        deliberately-unsafe version below.
        """
        for agent in list(self._agents.values()):
            if agent.campaign_id != campaign_id or agent.state != AgentState.AVAILABLE:
                continue
            lock = self._agent_locks[agent.id]
            if not lock.acquire(blocking=False):
                continue
            try:
                if agent.state != AgentState.AVAILABLE:
                    continue  # lost the race between the check above and acquiring the lock
                agent.state = AgentState.RESERVED
                agent.reserved_by = worker_id
                agent.reserved_at = time.monotonic()
                agent.version += 1
                return agent
            finally:
                lock.release()
        return None

    def reserve_agent_unsafe(self, agent_id: str, worker_id: str) -> Agent:
        """Deliberately UNSAFE reservation, used only in tests to
        demonstrate the double-booking bug that per-record locking
        (reserve_any_available_agent) prevents. The real dialer code
        never calls this."""
        agent = self._agents[agent_id]
        if agent.state != AgentState.AVAILABLE:
            raise ConflictError(f"{agent_id} not available")
        time.sleep(0.01)  # widen the race window on purpose
        agent.state = AgentState.RESERVED
        agent.reserved_by = worker_id
        agent.version += 1
        return agent

    def set_agent_state(self, agent_id: str, target: AgentState) -> Agent:
        lock = self._agent_locks[agent_id]
        with lock:
            agent = self._agents[agent_id]
            agent.state = agent_transition(agent.state, target)
            agent.version += 1
            if target == AgentState.AVAILABLE:
                agent.reserved_by = None
                agent.reserved_call_id = None
                agent.reserved_at = None
            return agent

    def bind_agent_to_call(self, agent_id: str, call_id: str) -> None:
        lock = self._agent_locks[agent_id]
        with lock:
            agent = self._agents[agent_id]
            agent.reserved_call_id = call_id

    # -- borrower reservation ------------------------------------------------
    def reserve_next_borrower(self, campaign_id: str, worker_id: str) -> Optional[Borrower]:
        with self._borrower_queue_lock:
            for b in self._borrowers.values():
                if b.campaign_id == campaign_id and b.state == BorrowerState.PENDING:
                    b.state = BorrowerState.RESERVED
                    b.reserved_by = worker_id
                    b.reserved_at = time.monotonic()
                    b.version += 1
                    b.attempts += 1
                    return b
            return None

    def release_borrower(self, borrower_id: str, target: BorrowerState) -> None:
        with self._borrower_queue_lock:
            b = self._borrowers[borrower_id]
            b.state = target
            b.reserved_by = None
            b.version += 1

    # -- calls -----------------------------------------------------------------
    def create_call(self, campaign_id: str, borrower_id: str, provider_name: str,
                     mode: str, worker_id: str, agent_id: Optional[str] = None) -> Call:
        with self._calls_lock:
            call = Call(id=next_id("call"), campaign_id=campaign_id, borrower_id=borrower_id,
                        provider_name=provider_name, mode=mode, agent_id=agent_id, worker_id=worker_id)
            self._calls[call.id] = call
            return call

    def set_call_agent(self, call_id: str, agent_id: str) -> None:
        with self._calls_lock:
            self._calls[call_id].agent_id = agent_id

    # -- reconciliation (crash recovery) ---------------------------------------
    def reconcile_stale_reservations(self, now: Optional[float] = None) -> int:
        """Sweep for agents/borrowers whose reservation lease expired
        without the reserving worker completing the handoff (worker
        crash, agent going offline mid-setup, etc). Returns the
        number released. This lease/TTL pattern is what recovers the
        system without needing distributed transactions - see the
        'Worker crash' failure case in ARCHITECTURE.md.
        """
        now = now if now is not None else time.monotonic()
        released = 0
        for agent in list(self._agents.values()):
            if agent.state == AgentState.RESERVED and agent.reserved_at and \
               now - agent.reserved_at > RESERVATION_TTL_SECONDS:
                lock = self._agent_locks[agent.id]
                with lock:
                    if agent.state == AgentState.RESERVED and agent.reserved_at and \
                       now - agent.reserved_at > RESERVATION_TTL_SECONDS:
                        agent.state = AgentState.AVAILABLE
                        agent.reserved_by = None
                        agent.reserved_call_id = None
                        agent.reserved_at = None
                        agent.version += 1
                        released += 1
                        self.event(f"reconcile: released stale agent {agent.id}")
        with self._borrower_queue_lock:
            for b in self._borrowers.values():
                if b.state == BorrowerState.RESERVED and b.reserved_at and \
                   now - b.reserved_at > RESERVATION_TTL_SECONDS:
                    b.state = BorrowerState.PENDING
                    b.reserved_by = None
                    released += 1
                    self.event(f"reconcile: requeued stale borrower {b.id}")
        return released
