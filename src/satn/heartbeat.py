"""Periodic, structured progress signals for long-running compiler work."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0


class StageHeartbeat:
    """Log the current compiler stage at a bounded interval until stopped.

    Milestone logs remain the primary audit trail.  This small daemon thread only
    supplies liveness information while a remote acquisition or computationally
    expensive stage is in progress.
    """

    def __init__(
        self,
        logger: logging.Logger,
        stage: str,
        context: Mapping[str, Any],
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval_seconds must be positive")
        self._logger = logger
        self._stage = stage
        self._context = dict(context)
        self._interval_seconds = interval_seconds
        self._started = 0.0
        self._stop_event = threading.Event()
        self._stage_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """Whether the heartbeat thread is currently active."""
        return self._thread is not None

    def start(self) -> StageHeartbeat:
        """Start periodic logging; repeated starts are harmless."""
        if self._thread is not None:
            return self
        self._started = time.perf_counter()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="satn-stage-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def set_stage(self, stage: str) -> None:
        """Update the stage reported by subsequent heartbeats."""
        with self._stage_lock:
            self._stage = stage

    def update_context(self, context: Mapping[str, Any]) -> None:
        """Merge ephemeral operational context into later heartbeat records.

        This context belongs only to liveness logs.  Callers must not use it to
        construct reproducible compiler or publication artifacts.
        """
        with self._stage_lock:
            self._context.update(dict(context))

    def context_snapshot(self) -> dict[str, Any]:
        """Return a defensive snapshot of the current operational context."""
        with self._stage_lock:
            return deepcopy(self._context)

    def stop(self) -> None:
        """Stop and join the thread, including when the guarded work failed."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join()
        self._thread = None

    def __enter__(self) -> StageHeartbeat:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            with self._stage_lock:
                stage = self._stage
                context = json.dumps(self._context, sort_keys=True, default=str)
            self._logger.info(
                "event=satn_heartbeat stage=%s elapsed_seconds=%.1f context=%s",
                stage,
                time.perf_counter() - self._started,
                context,
            )
