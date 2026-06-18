"""
MVP demo "test": emit random INFO log lines to stdout and to an artifact file,
once every 10 seconds for ~1 minute, then exit 0 (PASSED).

This stands in for a real framework container. The orchestrator streams the
stdout lines live to the web UI (via docker logs -> RabbitMQ fanout -> WebSocket)
and collects the artifact file written under /artifacts.
"""
from __future__ import annotations

import logging
import os
import random
import sys
import time

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/artifacts")
ITERATIONS = 6          # one line every 10s
INTERVAL_SECONDS = 10

MESSAGES = [
    "Initializing DUT connection",
    "Running self-check suite",
    "Measuring downlink throughput",
    "Supply voltage within nominal range",
    "Packet loss measured at 0.0%",
    "Board temperature stable",
    "Calibrating RF sensor",
    "Heartbeat acknowledged by DUT",
    "Flashing firmware image",
    "Verifying checksum",
]


def _build_logger() -> logging.Logger:
    """Log to stdout (streamed to the UI) and to an artifact file."""
    log = logging.getLogger("mvp-test")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)

    try:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(ARTIFACT_DIR, "run.log"))
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError:
        # No writable artifact mount — stdout streaming still works.
        log.warning("artifact dir %s not writable; logging to stdout only", ARTIFACT_DIR)

    return log


def main() -> int:
    log = _build_logger()
    log.info("=== MVP test starting (%d iterations, %ds apart) ===", ITERATIONS, INTERVAL_SECONDS)
    for i in range(1, ITERATIONS + 1):
        log.info("[%d/%d] %s (metric=%d)", i, ITERATIONS, random.choice(MESSAGES),
                 random.randint(0, 1000))
        if i < ITERATIONS:
            time.sleep(INTERVAL_SECONDS)
    log.info("=== MVP test complete: PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
