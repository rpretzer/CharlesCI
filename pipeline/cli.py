"""Pipeline CLI — human review pause point with event emission.

This module contains the wave-based pipeline runner. When a wave
reaches the human-review gate, it pauses execution and emits a
``human_review`` event so that configured integrations (e.g. Slack)
can notify reviewers.
"""

import sys

from pipeline.events import emitter


def pause_for_human_review(wave_num: int, units: list[dict]):
    """Pause the pipeline and notify reviewers.

    Args:
        wave_num: The current wave number awaiting review.
        units: List of unit dicts, each with at least ``id`` and ``name``.
    """
    unit_ids = [u["id"] for u in units]

    # Emit a structured event so webhooks (Slack, etc.) are notified.
    emitter.emit(
        "human_review",
        unit_id=None,
        wave=wave_num,
        units=units,
    )

    print(f"\n⏸  Wave {wave_num} is waiting for human review.")
    print(f"   Units: {', '.join(str(uid) for uid in unit_ids)}")
    print("   Review the reports, then run `pipeline advance` to continue.\n")


def main():
    """Minimal CLI entry point (placeholder)."""
    print("CharlesCI pipeline CLI")
    print("Usage: pipeline [run | advance | status]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
