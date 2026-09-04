"""기존 import 호환용 forwarding module."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from service.recommendation.llm_input_planner import *  # noqa: F401,F403
from service.recommendation.llm_input_planner import _normalize_remote_conditions
