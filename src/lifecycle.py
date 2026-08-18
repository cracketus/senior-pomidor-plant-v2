"""Lifecycle event delivery and replay."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from src.config import Settings
from src.network.event_sender import MqttEventSender
from src.utils.event_storage import delete_event_file, list_pending_events, load_event_file, save_event
from src.utils.events import format_lifecycle_event

EVENT_REPLAY_BATCH_SIZE = 10
ReplayCallable = Callable[[Settings, MqttEventSender], int]


class LifecycleReplayWorker:
    """Replay lifecycle events without blocking collector progress."""

    def __init__(
        self,
        settings: Settings,
        sender: MqttEventSender,
        *,
        replay: ReplayCallable,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.sender = sender
        self.replay = replay
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lifecycle-event-replay", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                self.logger.error("Lifecycle replay worker did not stop within %.1f seconds", timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.replay(self.settings, self.sender)
            except Exception as exc:
                self.logger.exception("Lifecycle replay worker failed; retrying after the next sample: %s", exc)


def emit_lifecycle_event(
    settings: Settings,
    event_type: str,
    *,
    reason: str | None = None,
    sender: MqttEventSender | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    logger = logger or logging.getLogger(__name__)
    sender = sender or MqttEventSender(settings, logger=logger)
    replay_pending_events(settings, sender, logger=logger)

    event = format_lifecycle_event(settings, event_type, reason=reason)
    saved_path = save_event(settings, event, logger=logger)
    delivered = sender.publish(event)
    if delivered and saved_path is not None:
        delete_event_file(saved_path, logger=logger)
    return delivered


def replay_pending_events(
    settings: Settings,
    sender: MqttEventSender,
    *,
    logger: logging.Logger | None = None,
) -> int:
    logger = logger or logging.getLogger(__name__)
    delivered_count = 0
    for path in list_pending_events(settings)[:EVENT_REPLAY_BATCH_SIZE]:
        event = load_event_file(path, logger=logger)
        if event is None:
            continue
        if not _deliver_event(event, sender):
            logger.error("Queued lifecycle event delivery failed; replay will retry later: %s", path)
            break
        delete_event_file(path, logger=logger)
        delivered_count += 1
    return delivered_count


def _deliver_event(event: dict[str, Any], sender: MqttEventSender) -> bool:
    return sender.publish(event)
