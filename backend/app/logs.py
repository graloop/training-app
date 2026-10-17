"""Where the app's own messages go: every module logs under "trainer.<name>"
(trainer.garmin, trainer.accounts...), printed to the console with the time,
so `docker logs` or the `npm start` terminal shows what happened and when.
Uvicorn prints its own lines (requests, crashes) next to them.

LOG_LEVEL=DEBUG also shows what Garmin didn't answer during a sync (weather,
one day's readiness...), which is normal now and then."""

import logging

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str) -> None:
    logger = logging.getLogger("trainer")
    logger.setLevel(level.upper())
    if not any(getattr(h, "_trainer", False) for h in logger.handlers):  # once, even with --reload
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
        handler._trainer = True
        logger.addHandler(handler)
