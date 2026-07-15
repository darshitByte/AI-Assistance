"""Shared stdout logger (visible via `docker compose logs -f backend`)."""
import logging
import sys

logger = logging.getLogger("grocerzy")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False
