"""Load HA's HTTP aliases before third-party pytest plugins import httpx."""

import sys
from pathlib import Path

import homeassistant  # noqa: F401
import pytest
from homeassistant.components import logger

# The published fixture package targets HA 2026.10.4. HA 2026.11 moved logger
# overrides into LoggerDomainConfig; retain the old test cleanup's semantics.
# This compatibility hook exists only in the test process, not the integration.
if not hasattr(logger, "_clear_logger_overwrites"):
    def _clear_logger_overwrites(hass):
        if (data := hass.data.get(logger.DATA_LOGGER)) is not None:
            data.overrides.clear()

    logger._clear_logger_overwrites = _clear_logger_overwrites

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
raise SystemExit(pytest.main(sys.argv[1:]))
