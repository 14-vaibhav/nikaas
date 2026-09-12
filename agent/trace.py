"""Trace log (diagram: 'TRACE LOG - every step emits an event - retries and
dead ends included, not hidden').

Every step of the loop calls `emit` exactly once. Abandoned branches, a
route that failed and was retried, an abstention - all of it emits. This
is the proof of agency the UI renders and the demo is pulled from.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Callable, Optional

from agent.contracts import Source, TraceEvent, TraceType, Verdict


class TraceLog:
    """Accumulates events for one run and optionally streams them to a
    subscriber (the SSE endpoint) as they're emitted."""

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None):
        self._events: list[TraceEvent] = []
        self._on_event = on_event
        self._step = 0

    def emit(
        self,
        type: TraceType,
        label: str,
        *,
        tool: Optional[str] = None,
        input: Optional[dict] = None,
        output: Optional[dict] = None,
        verdict: Optional[Verdict] = None,
        reason: Optional[str] = None,
        budget: Optional[dict] = None,
        source: Optional[Source] = None,
        duration_ms: Optional[int] = None,
    ) -> TraceEvent:
        self._step += 1
        event = TraceEvent(
            step=self._step,
            type=type,
            label=label,
            tool=tool,
            input=input,
            output=output,
            verdict=verdict,
            reason=reason,
            budget=budget,
            source=source,
            duration_ms=duration_ms,
        )
        self._events.append(event)
        if self._on_event is not None:
            self._on_event(asdict(event))
        return event

    def timed(self, type: TraceType, label: str, **kwargs):
        return _TimedEmit(self, type, label, kwargs)

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def route_sequence(self) -> list[str]:
        """The ordered list of routes DECIDE chose - the golden-trace tests
        assert on this to prove action selection is dynamic."""
        seq = []
        for e in self._events:
            if e.type == "decide" and e.output and "route" in e.output:
                seq.append(e.output["route"])
        return seq


class _TimedEmit:
    def __init__(self, tracer: TraceLog, type: TraceType, label: str, kwargs: dict):
        self._tracer = tracer
        self._type = type
        self._label = label
        self._kwargs = kwargs
        self._start = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        duration_ms = int((time.monotonic() - self._start) * 1000)
        self._tracer.emit(self._type, self._label, duration_ms=duration_ms, **self._kwargs)
        return False


# Back-compat alias: older imports say `Tracer`.
Tracer = TraceLog
