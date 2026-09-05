"""Kakao Local REST API 래퍼.

후보 seed 확장에 필요한 장소 키워드·카테고리 검색과 좌표→주소 변환만
제공한다. 인증 키는 프로젝트 루트의 ``.env``에서 ``KAKAO_REST_API_KEY``로
읽으며, URL이나 로그에 키를 넣지 않는다.

예시:
  .venv/bin/python3 scripts/kakao_local.py --query 잠실역 --size 3
  .venv/bin/python3 scripts/kakao_local.py --category CE7 \
      --x 127.1000 --y 37.5133 --radius 1000 --size 3
  .venv/bin/python3 scripts/kakao_local.py --category FD6 \
      --rect 127.09,37.50,127.10,37.51 --size 15

반환값은 Kakao Local API 원본 JSON이다. 후보용 CSV 정규화는
``scripts/ingest_kakao_poi.py``가 담당한다.

호출 예산은 ``scripts/_budget.py``의 영속 카운터로 보호한다. 공식 무료
쿼터(2026-09-02 확인)는 키워드/카테고리 검색 각각 일 100,000건, 전체 API
월 3,000,000건이다. 기본값은 이를 넘지 않는 일 90,000·월 2,700,000건이며
실제 HTTP 시도(재시도·HTTP 오류 포함)를 보수적으로 센다.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from _budget import Budget
from _env import load_env, require

BASE_URL = "https://dapi.kakao.com/v2/local"
KEYWORD_ENDPOINT = f"{BASE_URL}/search/keyword.json"
CATEGORY_ENDPOINT = f"{BASE_URL}/search/category.json"
COORD2ADDRESS_ENDPOINT = f"{BASE_URL}/geo/coord2address.json"

# Kakao Local category_group_code 중 후보 주변의 업종·생활 편의 seed에
# 직접 사용할 가능성이 높은 코드만 문서화한다. 검색기는 임의 코드도 허용한다.
CATEGORY_NAMES = {
    "FD6": "음식점",
    "CE7": "카페",
    "CS2": "편의점",
    "MT1": "대형마트",
    "PK6": "주차장",
    "SW8": "지하철역",
}

KAKAO_LOCAL_DAILY_FREE_LIMIT = 100_000
KAKAO_ALL_API_MONTHLY_FREE_LIMIT = 3_000_000
KAKAO_LOCAL_SAFE_DAILY_LIMIT = 90_000
KAKAO_LOCAL_SAFE_MONTHLY_LIMIT = 2_700_000
KAKAO_LOCAL_SAFE_PER_RUN_LIMIT = 500
_BUDGET: Budget | None = None


class KakaoLocalError(RuntimeError):
    """Kakao Local API 호출 실패."""


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("KAKAO_LOCAL_TIMEOUT", "20")))
    except ValueError:
        return 20.0


def _limit_from_env(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name}는 정수여야 합니다: {raw!r}") from exc
    if not 1 <= value <= maximum:
        raise SystemExit(f"{name}는 1~{maximum:,} 범위여야 합니다: {value:,}")
    return value


def _budget() -> Budget:
    """프로세스 전체에서 공유하는 Kakao Local 호출 가드."""
    global _BUDGET
    if _BUDGET is None:
        load_env()
        daily_limit = _limit_from_env(
            "KAKAO_LOCAL_DAILY_LIMIT",
            KAKAO_LOCAL_SAFE_DAILY_LIMIT,
            KAKAO_LOCAL_DAILY_FREE_LIMIT,
        )
        monthly_limit = _limit_from_env(
            "KAKAO_LOCAL_MONTHLY_LIMIT",
            KAKAO_LOCAL_SAFE_MONTHLY_LIMIT,
            KAKAO_ALL_API_MONTHLY_FREE_LIMIT,
        )
        per_run_limit = _limit_from_env(
            "KAKAO_LOCAL_PER_RUN_LIMIT",
            KAKAO_LOCAL_SAFE_PER_RUN_LIMIT,
            daily_limit,
        )
        _BUDGET = Budget(
            "kakao_local",
            monthly_limit=monthly_limit,
            daily_limit=daily_limit,
            per_run_limit=per_run_limit,
        )
    return _BUDGET


def budget_status() -> str:
    """네트워크 호출 없이 현재 Kakao Local 예산 사용량을 반환한다."""
    return _budget().status()


def budget_snapshot() -> dict:
    """manifest에 보존할 현재 Kakao Local 예산 상태."""
    return _budget().snapshot()


def _validate_common(page: int, size: int) -> None:
    if not 1 <= page <= 45:
        raise ValueError("page는 1~45여야 합니다.")
    if not 1 <= size <= 15:
        raise ValueError("size는 1~15여야 합니다.")


def _with_location(params: dict, *, x: float | None, y: float | None,
                   radius: int | None, rect: str | None) -> None:
    if (x is None) != (y is None):
        raise ValueError("x와 y는 함께 지정해야 합니다.")
    if x is not None:
        params["x"] = f"{float(x):.8f}"
        params["y"] = f"{float(y):.8f}"
    if radius is not None:
        if x is None or y is None:
            raise ValueError("radius를 사용할 때는 x와 y가 필요합니다.")
        if not 0 <= int(radius) <= 20000:
            raise ValueError("radius는 0~20000m여야 합니다.")
        params["radius"] = int(radius)
    if rect:
        if x is not None or y is not None:
            raise ValueError("rect와 x/y는 동시에 지정할 수 없습니다.")
        params["rect"] = rect


def _call(endpoint: str, params: dict) -> dict:
    """JSON API를 호출하고 일시 오류만 재시도한다."""
    key = require("KAKAO_REST_API_KEY")
    budget = _budget()
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"Authorization": f"KakaoAK {key}", "Accept": "application/json"},
        method="GET",
    )

    last_error: str | None = None
    for attempt in range(3):
        try:
            # 응답 수신 여부와 무관하게 실제 전송 시도를 세어 쿼터 초과를 막는다.
            budget.check()
            budget.commit()
            with urllib.request.urlopen(request, timeout=_timeout()) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise KakaoLocalError("Kakao Local 응답이 JSON object가 아닙니다.")
            return payload
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last_error = f"HTTP {exc.code}: {detail}"
            # 인증·파라미터 오류는 재시도해도 결과가 바뀌지 않는다.
            if exc.code in (400, 401, 403, 404):
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

    raise KakaoLocalError(f"Kakao Local API 호출 실패: {last_error}")


def search_keyword(query: str, *, x: float | None = None, y: float | None = None,
                   radius: int | None = None, rect: str | None = None,
                   page: int = 1, size: int = 15,
                   sort: str = "accuracy") -> dict:
    """장소명·업종명 키워드 검색."""
    query = str(query).strip()
    if not query:
        raise ValueError("query가 비어 있습니다.")
    if sort not in {"accuracy", "distance"}:
        raise ValueError("sort는 accuracy 또는 distance여야 합니다.")
    _validate_common(page, size)
    params = {"query": query, "page": page, "size": size, "sort": sort}
    _with_location(params, x=x, y=y, radius=radius, rect=rect)
    return _call(KEYWORD_ENDPOINT, params)


def search_category(category_group_code: str, *, x: float | None = None,
                    y: float | None = None, radius: int | None = None,
                    rect: str | None = None, page: int = 1, size: int = 15,
                    sort: str = "accuracy") -> dict:
    """카테고리 장소 검색.

    Kakao 문서대로 ``x/y/radius`` 중심·반경 또는 ``rect`` 사각형 범위를
    쓴다. ``distance`` 정렬은 중심 좌표가 있을 때만 허용한다.
    """
    code = str(category_group_code).strip().upper()
    if not code:
        raise ValueError("category_group_code가 비어 있습니다.")
    if sort not in {"accuracy", "distance"}:
        raise ValueError("sort는 accuracy 또는 distance여야 합니다.")
    _validate_common(page, size)
    params = {"category_group_code": code, "page": page, "size": size, "sort": sort}
    _with_location(params, x=x, y=y, radius=radius, rect=rect)
    return _call(CATEGORY_ENDPOINT, params)


def coord2address(x: float, y: float, *, input_coord: str = "WGS84") -> dict:
    """WGS84 등 좌표를 Kakao 도로명·지번 주소로 변환."""
    if not input_coord.strip():
        raise ValueError("input_coord가 비어 있습니다.")
    return _call(COORD2ADDRESS_ENDPOINT, {
        "x": f"{float(x):.8f}",
        "y": f"{float(y):.8f}",
        "input_coord": input_coord,
    })


def _main() -> None:
    ap = argparse.ArgumentParser(description="Kakao Local REST API 단일 호출");
    ap.add_argument("--budget", action="store_true", help="네트워크 호출 없이 일·월 예산 사용량 표시")
    group = ap.add_mutually_exclusive_group(required=False)
    group.add_argument("--query", help="장소 키워드")
    group.add_argument("--category", help="카테고리 코드(예: CE7, FD6)")
    group.add_argument("--coord2address", nargs=2, metavar=("X", "Y"),
                       help="WGS84 좌표를 주소로 변환")
    ap.add_argument("--x", type=float, help="검색 중심 경도(WGS84)")
    ap.add_argument("--y", type=float, help="검색 중심 위도(WGS84)")
    ap.add_argument("--radius", type=int, default=None, help="반경(m), 최대 20000")
    ap.add_argument("--rect", help="검색 사각형 x1,y1,x2,y2")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--size", type=int, default=15)
    ap.add_argument("--sort", choices=["accuracy", "distance"], default="accuracy")
    ap.add_argument("--input-coord", default="WGS84")
    args = ap.parse_args()

    if args.budget:
        if args.query or args.category or args.coord2address:
            ap.error("--budget은 검색·좌표변환 옵션과 함께 사용할 수 없습니다.")
        print(budget_status())
        return
    if not (args.query or args.category or args.coord2address):
        ap.error("--query, --category, --coord2address 중 하나가 필요합니다.")

    if args.coord2address:
        result = coord2address(float(args.coord2address[0]), float(args.coord2address[1]),
                               input_coord=args.input_coord)
    elif args.query:
        result = search_keyword(args.query, x=args.x, y=args.y, radius=args.radius,
                                rect=args.rect, page=args.page, size=args.size,
                                sort=args.sort)
    else:
        if args.rect:
            if args.x is not None or args.y is not None or args.radius is not None:
                ap.error("--category에서 --rect는 --x/--y/--radius와 함께 사용할 수 없습니다.")
            result = search_category(args.category, rect=args.rect, page=args.page,
                                     size=args.size, sort=args.sort)
        else:
            if args.x is None or args.y is None:
                ap.error("--category에는 --x/--y 또는 --rect가 필요합니다.")
            result = search_category(args.category, x=args.x, y=args.y,
                                     radius=args.radius if args.radius is not None else 1000,
                                     page=args.page, size=args.size, sort=args.sort)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
