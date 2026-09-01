"""Provider interface. The dialer never depends on a concrete
provider - only on this contract - so mock providers and a real
Plivo/Twilio/etc adapter are interchangeable without touching any
pacing, safety, or allocation code."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable
from ..events import ProviderEvent
from ..models import CallState

EventSink = Callable[[ProviderEvent], None]


class TelecomProvider(ABC):
    name: str

    @abstractmethod
    def place_call(self, call_id: str, phone_number: str, event_sink: EventSink,
                    expected_answer: bool, talk_time_seconds: float) -> None:
        """Start a call asynchronously. Delivers ProviderEvents to
        event_sink over time from a background thread - the same way
        a real provider would deliver webhooks."""
        raise NotImplementedError

    def cancel_call(self, call_id: str, event_sink: EventSink) -> None:
        """Best-effort cancellation. A real provider adapter would call its
        cancel/hangup API. The prototype emits a terminal CANCELLED event."""
        from ..events import new_event
        event_sink(new_event(call_id, CallState.CANCELLED, 999999, __import__("time").monotonic()))

    @abstractmethod
    def health(self) -> float:
        """0.0 (fully down) .. 1.0 (fully healthy) rolling health score."""
        raise NotImplementedError
