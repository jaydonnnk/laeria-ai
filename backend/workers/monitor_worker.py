"""Monitor worker — long-running background job for Mode 3.

Runs on the Hetzner VPS as a systemd service (see infra/systemd/).
Polls each active monitored item on its cadence, evaluates signal via the
alert engine, and persists alerts. Do NOT poll continuously — the 4-6h
cadence keeps PRAW well within the free-tier rate limit.

Phase 3 implements run_cycle(). This file currently just defines the loop
skeleton so the systemd unit has a valid target.
"""

from __future__ import annotations

import time

from core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

POLL_SLEEP_SECONDS = 60 * 30  # wake every 30 min, act only on due items


def run_cycle() -> None:
    """One pass over all active monitored items. Phase 3."""
    raise NotImplementedError("Phase 3: monitor cycle")


def main() -> None:
    logger.info("Monitor worker starting (skeleton — Phase 3 not yet implemented)")
    while True:
        try:
            run_cycle()
        except NotImplementedError:
            logger.info("run_cycle not implemented yet; sleeping")
        except Exception as exc:  # noqa: BLE001
            logger.error("Monitor cycle error: %s", exc)
        time.sleep(POLL_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
