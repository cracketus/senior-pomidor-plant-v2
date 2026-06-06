"""Application logging setup."""

from __future__ import annotations

import logging
import sys


def configure_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("senior-pomidor-edge")
