"""Recommendation API-specific dotenv loading."""
from __future__ import annotations

import os
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def load_env(path: str | Path | None = None) -> dict[str, str]:
    """Load a service-local dotenv file without overwriting process env."""
    configured = os.getenv("RECOMMENDATION_API_ENV_FILE", "").strip()
    env_path = Path(path or configured or (SERVICE_ROOT / ".env"))
    loaded: dict[str, str] = {}
    if not env_path.is_file():
        return loaded
    with env_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            loaded[key] = value
            os.environ.setdefault(key, value)
    return loaded
