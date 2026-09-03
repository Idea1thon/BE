"""의존성 없는 .env 로더. 다른 스크립트에서 `from _env import load_env, require`.

    from _env import load_env, require
    load_env()                                  # 프로젝트 루트의 .env 읽어 os.environ 에 주입
    key = require("VWORLD_API_KEY")

.env 형식: KEY=VALUE 한 줄에 하나. #으로 시작하는 줄과 빈 줄은 무시.
값의 양쪽 따옴표(" 또는 ')는 제거. 이미 os.environ 에 있으면 덮어쓰지 않음.
"""
from __future__ import annotations
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str | None = None) -> dict[str, str]:
    path = path or os.path.join(ROOT, ".env")
    loaded: dict[str, str] = {}
    if not os.path.isfile(path):
        return loaded
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            loaded[k] = v
            os.environ.setdefault(k, v)
    return loaded


def require(name: str) -> str:
    load_env()
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(
            f"환경변수 {name} 가 비어 있음. `cp .env.example .env` 후 값을 채우세요."
        )
    return val


if __name__ == "__main__":
    got = load_env()
    print(f".env 에서 {len(got)}개 키 로드:",
          ", ".join(f"{k}={'*' * min(len(v), 6) if v else '(빈값)'}" for k, v in got.items()))
