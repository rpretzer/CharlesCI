"""Pipeline event emitter with outbound webhook support.

Emits structured events to events.jsonl and simultaneously POSTs them
to any URLs listed in PIPELINE_WEBHOOK_URLS (comma-separated).
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

EVENTS_LOG = Path(os.getenv("PIPELINE_EVENTS_LOG", "events.jsonl"))
WEBHOOK_URLS = [
    u.strip()
    for u in os.getenv("PIPELINE_WEBHOOK_URLS", "").split(",")
    if u.strip()
]
WEBHOOK_EVENTS = {
    e.strip()
    for e in os.getenv("PIPELINE_WEBHOOK_EVENTS", "").split(",")
    if e.strip()
}
WEBHOOK_TIMEOUT = int(os.getenv("PIPELINE_WEBHOOK_TIMEOUT", "10"))


class EventEmitter:
    """Append-only event emitter that fans out to webhooks."""

    def emit(self, event_type: str, **payload):
        """Emit an event to the log file and any configured webhooks.

        Args:
            event_type: The event name (e.g. "human_review", "gate_verdict").
            **payload: Arbitrary key-value pairs included in the event body.
        """
        event = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }

        # Append to local JSONL log
        with open(EVENTS_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")

        # Fan out to webhooks (if event type is in the allow-list, or no
        # allow-list is configured — meaning all events are forwarded).
        if WEBHOOK_EVENTS and event_type not in WEBHOOK_EVENTS:
            return

        for url in WEBHOOK_URLS:
            try:
                requests.post(
                    url,
                    json=event,
                    timeout=WEBHOOK_TIMEOUT,
                    headers={"Content-Type": "application/json"},
                )
            except requests.RequestException as exc:
                # Log but never block the pipeline on a webhook failure.
                print(f"[webhook] POST to {url} failed: {exc}")


# Module-level singleton for convenience.
emitter = EventEmitter()
