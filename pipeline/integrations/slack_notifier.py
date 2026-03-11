"""Slack notifier — Phase 1 + Phase 2 (notifications + interactive buttons).

Phase 1: Receives pipeline webhook POSTs and reformats them as Slack Block Kit
messages, then forwards them to a Slack Incoming Webhook URL.

Phase 2: Adds Approve/Reject buttons to review messages. When a reviewer clicks
a button, Slack POSTs to /slack/actions. The handler updates the original
message (via Bot Token) and emits a gate_verdict event to advance or reject
the pipeline.

Deployment: run as a long-running Flask service.

    SLACK_WEBHOOK_URL=https://hooks.slack.com/... \\
    SLACK_BOT_TOKEN=xoxb-... \\
    flask --app pipeline.integrations.slack_notifier:create_app run
"""

import hashlib
import hmac
import json
import os
import time

import requests

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
REPORT_BASE_URL = os.getenv(
    "PIPELINE_REPORT_BASE_URL", "https://your-ci.example.com/reports"
)


# ---------------------------------------------------------------------------
# Block Kit formatters
# ---------------------------------------------------------------------------

def format_human_review(event: dict) -> dict:
    """Build a Slack Block Kit payload for a human_review event.

    Includes Approve / Reject buttons when SLACK_BOT_TOKEN is configured.
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

    blocks = [
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
    ]

    # Phase 2: add interactive buttons if bot token is available
    if SLACK_BOT_TOKEN:
        blocks.append({
            "type": "actions",
            "block_id": f"review_wave_{wave}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "approve_wave",
                    "value": json.dumps({"wave": wave, "units": units}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "action_id": "reject_wave",
                    "value": json.dumps({"wave": wave, "units": units}),
                },
            ],
        })

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    "Click a button above or run "
                    "`pipeline advance` to continue."
                ) if SLACK_BOT_TOKEN else (
                    "Review the reports above, then run "
                    "`pipeline advance` to continue."
                ),
            }
        ],
    })

    return {"blocks": blocks}


FORMATTERS = {
    "human_review": format_human_review,
}


# ---------------------------------------------------------------------------
# Outbound: send to Slack
# ---------------------------------------------------------------------------

def notify(event: dict) -> bool:
    """Format and send a pipeline event to Slack.

    Returns True if the message was sent successfully.
    """
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL not set — skipping notification.")
        return False

    event_type = event.get("event")
    formatter = FORMATTERS.get(event_type)
    if formatter is None:
        payload = {
            "text": f"Pipeline event: `{event_type}`\n```{json.dumps(event, indent=2)}```"
        }
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


# ---------------------------------------------------------------------------
# Phase 2: handle interactive button clicks
# ---------------------------------------------------------------------------

def _verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Verify the request came from Slack using the signing secret."""
    if abs(time.time() - float(timestamp)) > 300:
        return False
    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def _update_message(response_url: str, blocks: list) -> bool:
    """Replace the original Slack message via the response_url."""
    try:
        resp = requests.post(
            response_url,
            json={"replace_original": "true", "blocks": blocks},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[slack] Failed to update message: {exc}")
        return False


def handle_action(payload: dict) -> dict:
    """Process an interactive button click from Slack.

    Args:
        payload: The parsed JSON payload from Slack's action request.

    Returns:
        A dict with the action result.
    """
    from pipeline.events import emitter

    action = payload["actions"][0]
    action_id = action["action_id"]
    value = json.loads(action["value"])
    user = payload.get("user", {}).get("username", "unknown")
    response_url = payload.get("response_url", "")

    wave = value.get("wave", "?")
    units = value.get("units", [])
    verdict = "approved" if action_id == "approve_wave" else "rejected"

    # Emit event so the pipeline can react
    emitter.emit(
        "gate_verdict",
        wave=wave,
        units=units,
        verdict=verdict,
        reviewer=user,
    )

    # Update the original Slack message to show the decision
    status_emoji = "✅" if verdict == "approved" else "❌"
    updated_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji} Wave {wave} {verdict}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Wave {wave}* was *{verdict}* by <@{user}>.",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Decision recorded. Pipeline will {'continue' if verdict == 'approved' else 'halt'}.",
                }
            ],
        },
    ]

    if response_url:
        _update_message(response_url, updated_blocks)

    return {"verdict": verdict, "wave": wave, "reviewer": user}


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app():
    """Create a Flask app with Phase 1 notifications and Phase 2 interactivity.

    Endpoints:
        POST /pipeline-event   — receive events from events.py, forward to Slack
        POST /slack/actions     — receive button clicks from Slack
        GET  /health            — health check
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

    @app.post("/slack/actions")
    def slack_actions():
        # Slack sends interactive payloads as application/x-www-form-urlencoded
        # with a single "payload" field containing JSON.
        raw_payload = req.form.get("payload")
        if not raw_payload:
            return jsonify({"error": "missing payload"}), 400

        # Verify request signature if signing secret is configured
        if SLACK_SIGNING_SECRET:
            timestamp = req.headers.get("X-Slack-Request-Timestamp", "0")
            signature = req.headers.get("X-Slack-Signature", "")
            if not _verify_slack_signature(
                SLACK_SIGNING_SECRET, timestamp, req.get_data(), signature
            ):
                return jsonify({"error": "invalid signature"}), 403

        payload = json.loads(raw_payload)
        result = handle_action(payload)
        return jsonify(result), 200

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
