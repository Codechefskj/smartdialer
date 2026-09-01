"""Provider B: slower, occasional timeouts, duplicate events, and
events that arrive out of order - a badly-behaved carrier we still
have to work with correctly."""
from __future__ import annotations
import random
import threading
import time
from ..events import new_event
from ..models import CallState
from .base import TelecomProvider, EventSink


class ProviderB(TelecomProvider):
    name = "provider-b"

    def __init__(self, failure_rate: float = 0.08, timeout_rate: float = 0.07,
                 duplicate_rate: float = 0.25, reorder_rate: float = 0.20,
                 setup_delay=(0.3, 0.8)):
        self.failure_rate = failure_rate
        self.timeout_rate = timeout_rate
        self.duplicate_rate = duplicate_rate
        self.reorder_rate = reorder_rate
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

    def _emit(self, event_sink, call_id, state, seq):
        event_sink(new_event(call_id, state, seq, time.monotonic()))
        if random.random() < self.duplicate_rate:
            # a genuine duplicate delivery of the SAME logical event
            time.sleep(random.uniform(0.01, 0.05))
            event_sink(new_event(call_id, state, seq, time.monotonic()))

    def place_call(self, call_id, phone_number, event_sink: EventSink,
                    expected_answer: bool, talk_time_seconds: float) -> None:
        def run():
            if random.random() < self.timeout_rate:
                self._record(False)
                return  # no events ever arrive - simulates a hung/lost webhook

            seq = 1
            self._emit(event_sink, call_id, CallState.INITIATED, seq)
            time.sleep(random.uniform(*self.setup_delay))

            if random.random() < self.failure_rate:
                seq += 1
                self._emit(event_sink, call_id, CallState.FAILED, seq)
                self._record(False)
                return

            seq += 1
            self._emit(event_sink, call_id, CallState.RINGING, seq)
            time.sleep(random.uniform(0.2, 0.6))

            if not expected_answer:
                seq += 1
                self._emit(event_sink, call_id, CallState.FAILED, seq)
                self._record(True)
                return

            answered_seq = seq + 1
            connected_seq = seq + 2
            completed_seq = seq + 3

            if random.random() < self.reorder_rate:
                # fire CONNECTED before ANSWERED - the state machine's
                # rank check has to cope with this without breaking.
                self._emit(event_sink, call_id, CallState.CONNECTED, connected_seq)
                self._emit(event_sink, call_id, CallState.ANSWERED, answered_seq)
            else:
                self._emit(event_sink, call_id, CallState.ANSWERED, answered_seq)
                self._emit(event_sink, call_id, CallState.CONNECTED, connected_seq)

            time.sleep(talk_time_seconds)
            self._emit(event_sink, call_id, CallState.COMPLETED, completed_seq)
            self._record(True)

        threading.Thread(target=run, daemon=True).start()
