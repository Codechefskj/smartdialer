# SmartDialer

A small, runnable SmartDialer prototype for a collections use case.
The design keeps predictive pacing separate from the safety boundary:

```text
Campaign
   -> Pacing Engine (Progressive / Predictive)
   -> Safety Controller
   -> Call Allocator
   -> Telecom Provider
```

## What is implemented

- Progressive dialing: one real agent is reserved before each outbound call.
- Predictive pacing: answer-rate/EWMA driven suggestions with provider-health input.
- Safety Controller: the pacing engine can only suggest; it cannot place calls.
- Shared predictive capacity ledger: concurrent workers cannot independently approve the same agent capacity.
- Agent and call state machines.
- Atomic per-agent and borrower reservations for the in-memory prototype.
- Duplicate and out-of-order provider-event handling.
- Provider A: fast/reliable mock.
- Provider B: slower mock with failures, timeouts, duplicate events and out-of-order events.
- Worker-crash-style lease reconciliation.
- Call setup timeout recovery when a provider never sends an event.
- Retry by returning failed/timed-out borrowers to `PENDING`.
- Predictive answer-time safety gate: an `ANSWERED`/`CONNECTED` event is only allowed to proceed after an agent is atomically reserved. If no agent is available, the call is cancelled and never enters a connected state without an agent.
- Scenarios A/B/C/D and a reservation load test for 100/1,000/10,000 agents.
- 24 automated tests.

## Requirements

- Python 3.10+
- `pytest` for tests
- No external runtime dependency

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Tests

```bash
python3 -m pytest -v
```

The suite covers state transitions, duplicate/out-of-order events, concurrent agent reservation, stale reservation recovery, predictive pacing bounds, shared safety capacity, provider-health reduction, provider timeout recovery, and short end-to-end runs.

## Simulation

Predictive, Scenario B, Provider B:

```bash
PYTHONPATH=. python3 -m smartdialer.simulator \
  --scenario B --mode predictive --agents 20 --borrowers 400 \
  --workers 3 --duration 12 --provider b
```

Progressive, Scenario A, Provider A:

```bash
PYTHONPATH=. python3 -m smartdialer.simulator \
  --scenario A --mode progressive --agents 20 --borrowers 400 \
  --workers 3 --duration 12 --provider a
```

All four assignment scenarios:

```bash
PYTHONPATH=. python3 -m smartdialer.simulator --all --mode predictive --provider b
```

Scenario definitions:

| Scenario | Answer rate | Avg talk time |
|---|---:|---:|
| A | 20% | 1.2 simulated seconds |
| B | 50% | 0.9 simulated seconds |
| C | 70% | 1.8 simulated seconds |
| D | changing | changing |

The talk times are intentionally scaled down for a laptop simulation; the README and scenario code keep the relative A/B/C relationships from the brief. Scenario D changes both answer rate and talk time over the run.

Each simulation reports calls initiated, connected, completed, failed, abandoned, average agent utilization, and Safety Controller decisions.

## Load test

```bash
PYTHONPATH=. python3 -m smartdialer.load_test
```

The benchmark exercises atomic agent reservation across 100, 1,000 and 10,000 agents with 1, 4 and 16 workers. The prototype deliberately uses a linear in-memory scan; `ARCHITECTURE.md` explains the production indexing/database change that removes this bottleneck.

## Safety invariant

The important invariant is:

> **A predictive call cannot enter `ANSWERED` or `CONNECTED` unless an agent has been atomically reserved for it.**

The Safety Controller also maintains a shared predictive-capacity ledger. A predictive approval consumes a capacity token; terminal completion/failure/cancellation releases it. This prevents multiple workers from each seeing the same available-agent count and independently approving the same capacity.

The answer-time gate is still required because agent availability can change after pacing approval. If a predictive call answers and no agent can be reserved, the worker cancels the call instead of recording an abandoned connected call.

## Failure handling

### Worker crash

Agent/borrower reservations carry a lease timestamp. The reconciliation sweep releases stale reservations after the TTL. Calls that were initiated but receive no provider event are also recovered by the call setup timeout.

### Provider outage

Provider health is fed into the Safety Controller. Poor health reduces new dialing volume. Existing calls are allowed to finish; calls that never produce a provider event are cancelled by the setup watchdog and their borrowers return to `PENDING` for a later retry.

### Agent availability drop

Availability is read on every worker tick. The safety capacity is therefore recalculated from fresh state rather than a cached agent count. The answer-time gate handles the final race if an agent disappears after approval.

### Duplicate/out-of-order events

Provider events are idempotent by event ID and call-state rank. Lower-rank late events and repeated states are ignored. Provider B intentionally produces duplicates and `CONNECTED` before `ANSWERED` on some calls.

## Project layout

```text
smartdialer_project/
  README.md
  ARCHITECTURE.md
  requirements.txt
  smartdialer/
    models.py
    events.py
    state_machines.py
    store.py
    safety_controller.py
    allocator.py
    worker.py
    campaign.py
    simulator.py
    load_test.py
    pacing/
      progressive.py
      predictive.py
    providers/
      base.py
      provider_a.py
      provider_b.py
  tests/
```

## Review order

1. `safety_controller.py`
2. `worker.py`
3. `state_machines.py`
4. `store.py`
5. `tests/test_concurrency.py`
6. `tests/test_end_to_end.py`
7. `ARCHITECTURE.md`
