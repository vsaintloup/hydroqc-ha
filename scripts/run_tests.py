"""Load HA's HTTP aliases before third-party pytest plugins import httpx."""

import sys
from pathlib import Path

import homeassistant  # noqa: F401
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
raise SystemExit(pytest.main(sys.argv[1:]))
