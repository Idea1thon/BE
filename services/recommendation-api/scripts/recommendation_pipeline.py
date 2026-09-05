"""기존 CLI·분석 스크립트 호환용 forwarding entrypoint.

입지 추천 로직의 정본은 recommendation.pipeline이다.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from recommendation.pipeline import *  # noqa: F401,F403
from recommendation.pipeline import main as _main


if __name__ == "__main__":
    raise SystemExit(_main())
