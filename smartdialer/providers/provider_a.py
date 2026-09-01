"""Provider A: fast, reliable, low failure rate - a well-behaved carrier."""
from __future__ import annotations
import random
import threading
import time
from ..events import new_event
from ..models import CallState
from .base import TelecomProvider, EventSink


class ProviderA(TelecomProvider):
    name = "provider-a"

    def __init__(self, failure_rate: float = 0.03, setup_delay=(0.05, 0.15)):
        self.failure_rate = failure_rate
        self.setup_delay = setup_delay
        self._recent_outcomes = []
        self._lock = threading.Lock()

    def health(self) -> float:
        with self._lock:
            if not self._recent_outcomes:
                return 1.0
            window = self._recent_outcomes[-50:]
            return sum(window) / len(window)

    def _record(self, ok: bool):
        with self._lock:
            self._recent_outcomes.append(1.0 if ok else 0.0)

    def place_call(self, call_id, phone_number, event_sink: EventSink,
                    expected_answer: bool, talk_time_seconds: float) -> None:
        def run():
            seq = 1
            event_sink(new_event(call_id, CallState.INITIATED, seq, time.monotonic()))
            time.sleep(random.uniform(*self.setup_delay))

            if random.random() < self.failure_rate:
                seq += 1
                event_sink(new_event(call_id, CallState.FAILED, seq, time.monotonic()))
                self._record(False)
                return

            seq += 1
            event_sink(new_event(call_id, CallState.RINGING, seq, time.monotonic()))
            time.sleep(random.uniform(0.1, 0.3))

            if not expected_answer:
                seq += 1
                event_sink(new_event(call_id, CallState.FAILED, seq, time.monotonic()))
                self._record(True)
                return

            seq += 1
            event_sink(new_event(call_id, CallState.ANSWERED, seq, time.monotonic()))
            seq += 1
            event_sink(new_event(call_id, CallState.CONNECTED, seq, time.monotonic()))
            time.sleep(talk_time_seconds)
            seq += 1
            event_sink(new_event(call_id, CallState.COMPLETED, seq, time.monotonic()))
            self._record(True)

        threading.Thread(target=run, daemon=True).start()
