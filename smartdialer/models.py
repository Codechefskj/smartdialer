"""Core domain models: Agent, Borrower, Call and their states."""
from __future__ import annotations
import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set


class AgentState(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    WRAP_UP = "WRAP_UP"
    PAUSED = "PAUSED"


class CallState(str, Enum):
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"  # predictive-mode only: answered but no agent was free


class BorrowerState(str, Enum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"
    CALLED = "CALLED"
    DO_NOT_CALL = "DO_NOT_CALL"


_ids = itertools.count(1)


def next_id(prefix: str) -> str:
    return f"{prefix}-{next(_ids)}"


@dataclass
class Agent:
    id: str
    campaign_id: str
    state: AgentState = AgentState.AVAILABLE
    version: int = 0
    reserved_by: Optional[str] = None
    reserved_call_id: Optional[str] = None
    reserved_at: Optional[float] = None
    last_heartbeat: float = field(default_factory=time.monotonic)


@dataclass
class Borrower:
    id: str
    campaign_id: str
    phone: str
    state: BorrowerState = BorrowerState.PENDING
    version: int = 0
    reserved_by: Optional[str] = None
    reserved_at: Optional[float] = None
    attempts: int = 0


@dataclass
class Call:
    id: str
    campaign_id: str
    borrower_id: str
    provider_name: str
    mode: str  # "progressive" | "predictive"
    agent_id: Optional[str] = None
    state: CallState = CallState.QUEUED
    version: int = 0
    created_at: float = field(default_factory=time.monotonic)
    connected_at: Optional[float] = None
    ended_at: Optional[float] = None
    seen_event_ids: Set[str] = field(default_factory=set)
    worker_id: Optional[str] = None
    initiated_at: Optional[float] = None
    setup_timeout_seconds: float = 2.0
    safety_capacity_released: bool = False
