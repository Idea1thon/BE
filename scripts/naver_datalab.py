"""네이버 데이터랩 검색어 트렌드 API 래퍼 — 프로파일 FC-41·42.

    from naver_datalab import search_trend
    res = search_trend(
        [{"groupName": "성수동카페", "keywords": ["성수동 카페", "성수 카페"]},
         {"groupName": "커피",       "keywords": ["커피", "카페"]}],
        start="2023-01-01", end="2026-08-31", time_unit="month")

반환: {"results": [{"title","keywords","data":[{"period","ratio"}...]}...]}
  - ratio 는 요청 구간 전체에서 최댓값=100 으로 정규화된 상대지수 (절대 검색량 아님).
  - 지역별 분해는 openapi 로 불가(웹 전용). 지역명·업종명을 키워드로 넣어 추세만.
  - 최대 5개 키워드그룹. 검색어 트렌드 무료 구간 = 월 30,000건.

무료 구간 보호: 매 호출 전 scripts/_budget.py 로 월 호출수를 확인하고
  NAVER_DATALAB_MONTHLY_LIMIT(.env, 기본 28000 — 무료 30,000 안쪽 버퍼) 초과 시 즉시 중단.
  성공한 호출만 카운트. 실행당 200회 상한.

키: .env 의 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET.

인증 방식 2가지를 순서대로 시도(2025년 검색 API가 NAVER API HUB(NCP)로 이관됨):
  1) 신규(NCP HUB): 헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
  2) 레거시(개발자센터): 헤더 X-Naver-Client-Id / X-Naver-Client-Secret
엔드포인트는 NAVER_DATALAB_ENDPOINTS(.env, 콤마구분) 로 덮어쓸 수 있음.

CLI:
  .venv/bin/python3 scripts/naver_datalab.py 2023-01-01 2026-08-31 month "성수동 카페" "송파구 맛집"
  .venv/bin/python3 scripts/naver_datalab.py --budget      # 이번 달 사용량 확인
"""
from __future__ import annotations
import json, os, sys, time, urllib.error, urllib.request

from _env import load_env, require
from _budget import Budget

TIME_UNITS = {"date", "week", "month"}

load_env()
_LIMIT = int(os.environ.get("NAVER_DATALAB_MONTHLY_LIMIT", "28000"))
_BUDGET = Budget("naver_datalab", monthly_limit=_LIMIT, per_run_limit=200)

# (엔드포인트, 헤더셋) 후보. 신규(NCP HUB) → 레거시 순.
_DEFAULT_ENDPOINTS = [
    "https://naverapihub.apigw.ntruss.com/search-trend/v1/search",
    "https://openapi.naver.com/v1/datalab/search",
]
_ENDPOINTS = [e.strip() for e in os.environ.get(
    "NAVER_DATALAB_ENDPOINTS", ",".join(_DEFAULT_ENDPOINTS)).split(",") if e.strip()]


def search_trend(keyword_groups: list[dict], start: str, end: str,
                 time_unit: str = "month", device: str = "",
                 ages: list[str] | None = None, gender: str = "") -> dict:
    if not 1 <= len(keyword_groups) <= 5:
        raise ValueError("키워드그룹은 1~5개")
    if time_unit not in TIME_UNITS:
        raise ValueError(f"time_unit 는 {TIME_UNITS}")
    body = {"startDate": start, "endDate": end, "timeUnit": time_unit,
            "keywordGroups": keyword_groups}
    if device:
        body["device"] = device
    if gender:
        body["gender"] = gender
    if ages:
        body["ages"] = ages
    cid = require("NAVER_CLIENT_ID")
    csec = require("NAVER_CLIENT_SECRET")
    headers = {
        "Content-Type": "application/json",
        # 두 방식 헤더를 함께 보냄(무해). 엔드포인트가 필요한 쪽만 읽음.
        "X-NCP-APIGW-API-KEY-ID": cid,
        "X-NCP-APIGW-API-KEY": csec,
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": csec,
    }
    payload = json.dumps(body).encode("utf-8")
    _BUDGET.check()   # 무료 한도 초과면 여기서 SystemExit

    last_err = None
    for endpoint in _ENDPOINTS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(endpoint, data=payload, method="POST",
                                             headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    out = json.loads(r.read().decode("utf-8"))
                _BUDGET.commit()   # 성공한 호출만 카운트
                return out
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last_err = f"{endpoint} → {e.code}: {detail}"
                if e.code in (401, 403, 404):
                    break            # 이 엔드포인트는 포기, 다음 후보로
                if attempt == 2:
                    break
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_err = f"{endpoint} → {e}"
                if attempt == 2:
                    break
                time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"네이버 데이터랩 호출 실패 (모든 엔드포인트). 마지막: {last_err}")


def keyword_trend(name: str, keywords: list[str], start: str, end: str,
                  time_unit: str = "month") -> list[dict]:
    """단일 그룹 편의 함수. [{period, ratio}...] 반환."""
    res = search_trend([{"groupName": name, "keywords": keywords}],
                       start, end, time_unit)
    return res.get("results", [{}])[0].get("data", [])


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--budget":
        print(_BUDGET.status())
        sys.exit(0)
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    start, end, unit = sys.argv[1], sys.argv[2], sys.argv[3]
    groups = [{"groupName": kw, "keywords": [kw]} for kw in sys.argv[4:9]]
    res = search_trend(groups, start, end, unit)
    for r in res.get("results", []):
        pts = r["data"]
        print(f"\n[{r['title']}]  {len(pts)}개 시점")
        for p in pts:
            bar = "█" * int(p["ratio"] / 2)
            print(f"  {p['period']}  {p['ratio']:6.2f} {bar}")
