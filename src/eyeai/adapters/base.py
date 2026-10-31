"""Shared threaded adapter base.

Every adapter owns one daemon thread. The thread runs `_session()` (one
connection lifetime) in a loop with exponential backoff. Exceptions never
escape into the caller's thread: they are recorded in `status()["last_error"]`
and the adapter reconnects.
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ..schema import Emit, Event, utcnow

log = logging.getLogger("eyeai.adapters")


def resolve_source_ts(value: str | None) -> tuple[datetime, str]:
    """Return (UTC datetime, ts_source marker).

    * ISO 8601 with offset (or 'Z')   -> converted to UTC, marker 'source'.
    * ISO 8601 without any offset     -> interpreted as UTC, marker 'source_naive_utc'.
    * absent / unparsable             -> arrival time, marker 'arrival'.
    """
    if value:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return utcnow(), "arrival"
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc), "source_naive_utc"
        return dt.astimezone(timezone.utc), "source"
    return utcnow(), "arrival"


class ThreadedAdapter(ABC):
    """Base class: start/stop, reconnect with backoff, status, safe emit."""

    def __init__(
        self,
        site_id: str,
        device_id: str,
        emit: Emit,
        *,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        if not site_id or not device_id:
            raise ValueError("site_id and device_id are required")
        self.site_id = site_id
        self.device_id = device_id
        self._emit = emit
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._connected = False
        self._last_event_ts: datetime | None = None
        self._last_error: str | None = None
        self._session_was_up = False

    # ----- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run_forever, name=f"adapter-{self.device_id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._set_connected(False)

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "device_id": self.device_id,
                "connected": self._connected,
                "last_event_ts": self._last_event_ts,
                "last_error": self._last_error,
            }

    # ----- subclass contract ----------------------------------------------
    @abstractmethod
    def _session(self) -> None:
        """Run one connection lifetime. Return normally on clean end.

        Must return promptly once `self._stop_requested` is True. Call
        `self._set_connected(True)` once the source is usable.
        """

    # ----- helpers for subclasses -----------------------------------------
    @property
    def _stop_requested(self) -> bool:
        return self._stop_evt.is_set()

    def _wait(self, seconds: float) -> bool:
        """Sleep, returning True if a stop was requested meanwhile."""
        return self._stop_evt.wait(seconds)

    def _set_connected(self, value: bool) -> None:
        with self._state_lock:
            self._connected = value
            if value:
                self._session_was_up = True

    def _set_error(self, message: str) -> None:
        with self._state_lock:
            self._last_error = message

    def _emit_event(self, event: Event) -> None:
        """Deliver an event. Never raises; errors are recorded."""
        with self._state_lock:
            self._last_event_ts = event.ts_utc
        try:
            self._emit(event)
        except Exception as exc:  # noqa: BLE001 - consumer errors must not kill the adapter
            self._set_error(f"emit failed: {exc!r}")
            log.exception("emit callback failed for %s", self.device_id)

    # ----- loop -----------------------------------------------------------
    def _run_forever(self) -> None:
        delay = self._backoff_initial
        while not self._stop_requested:
            self._session_was_up = False
            try:
                self._session()
                if not self._stop_requested:
                    self._set_error("session ended; reconnecting")
            except Exception as exc:  # noqa: BLE001 - adapter must survive any source fault
                self._set_error(f"{type(exc).__name__}: {exc}")
                log.warning("adapter %s session error: %s", self.device_id, exc)
            finally:
                self._set_connected(False)
            if self._stop_requested:
                break
            if self._session_was_up:
                delay = self._backoff_initial
            if self._wait(delay):
                break
            delay = min(delay * 2, self._backoff_max)
