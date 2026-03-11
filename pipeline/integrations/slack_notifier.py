"""Slack notifier — Phase 1 (notifications only).

Receives pipeline webhook POSTs and reformats them as Slack Block Kit
messages, then forwards them to a Slack Incoming Webhook URL.

Deployment options:
  - As a standalone Flask/FastAPI micro-service
  - As an AWS Lambda behind API Gateway
  - As a GitHub Actions workflow_dispatch receiver

For Phase 1, this module can also be used directly by events.py:
just set PIPELINE_WEBHOOK_URLS to your Slack Incoming Webhook URL
and this formatter will be bypassed — Slack will receive the raw JSON.

For richer messages, run this as a small HTTP service and point
PIPELINE_WEBHOOK_URLS at it instead.
"""

import json
import os

import requests

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
REPORT_BASE_URL = os.getenv(
    "PIPELINE_REPORT_BASE_URL", "https://your-ci.example.com/reports"
)


def format_human_review(event: dict) -> dict:
    """Build a Slack Block Kit payload for a human_review event.

    Args:
        event: The raw event dict from events.py.

    Returns:
        A dict ready to POST to a Slack Incoming Webhook.
    """
    wave = event.get("wave", "?")
    units = event.get("units", [])

    unit_lines = []
    for u in units:
        uid = u.get("id", "unknown")
        name = u.get("name", uid)
        link = f"{REPORT_BASE_URL}/{uid}"
        unit_lines.append(f"• <{link}|{name}> (`{uid}`)")

    unit_text = "\n".join(unit_lines) if unit_lines else "_No units listed._"

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"⏸ Wave {wave} awaiting human review",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Wave {wave}* has paused and needs a reviewer.\n\n"
                        f"*Units to review:*\n{unit_text}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Review the reports above, then run "
                            "`pipeline advance` to continue."
                        ),
                    }
                ],
            },
        ],
    }


# Map event types to their formatters.
FORMATTERS = {
    "human_review": format_human_review,
}


def notify(event: dict) -> bool:
    """Format and send a pipeline event to Slack.

    Args:
        event: The raw event dict (must include an "event" key).

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set — skipping notification.")
        return False

    event_type = event.get("event")
    formatter = FORMATTERS.get(event_type)
    if formatter is None:
        # No formatter for this event type — forward raw JSON as fallback.
        payload = {"text": f"Pipeline event: `{event_type}`\n```{json.dumps(event, indent=2)}```"}
    else:
        payload = formatter(event)

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[slack] Failed to send notification: {exc}")
        return False


# --- Minimal receiver for use as a micro-service ---

def create_app():
    """Create a minimal Flask app that receives webhook POSTs from events.py
    and forwards them to Slack.

    Usage:
        SLACK_WEBHOOK_URL=https://hooks.slack.com/... flask --app pipeline.integrations.slack_notifier:create_app run
    """
    try:
        from flask import Flask, request as req, jsonify
    except ImportError:
        raise RuntimeError(
            "Flask is required to run the slack_notifier as a service. "
            "Install it with: pip install flask"
        )

    app = Flask(__name__)

    @app.post("/pipeline-event")
    def receive_event():
        event = req.get_json(force=True)
        ok = notify(event)
        return jsonify({"ok": ok}), 200 if ok else 502

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
