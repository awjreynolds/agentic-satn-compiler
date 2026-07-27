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
    """Log compiler phase transitions and bounded liveness until stopped.

    Progress records are operational CLI feedback only. They do not alter
    reproducible compiler or publication artifacts.
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
        self._log_progress("started")
        self._thread.start()
        return self

    def set_stage(self, stage: str) -> None:
        """Report a phase transition and update subsequent heartbeats."""
        with self._stage_lock:
            changed = stage != self._stage
            self._stage = stage
        if changed and self._thread is not None:
            self._log_progress("running")

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

    def stop(self, *, status: str = "stopped") -> None:
        """Stop and join the thread, then report the terminal status."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join()
        self._thread = None
        self._log_progress(status)

    def __enter__(self) -> StageHeartbeat:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> None:
        if exception_type is None:
            status = "completed"
        elif issubclass(exception_type, KeyboardInterrupt):
            status = "interrupted"
        else:
            status = "failed"
        self.stop(status=status)

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

    def _log_progress(self, status: str) -> None:
        with self._stage_lock:
            stage = self._stage
            context = json.dumps(self._context, sort_keys=True, default=str)
        self._logger.info(
            "event=satn_progress status=%s stage=%s elapsed_seconds=%.1f context=%s",
            status,
            stage,
            time.perf_counter() - self._started,
            context,
        )
