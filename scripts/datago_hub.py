"""공공데이터포털 건축HUB(1613000) 클라이언트 — 건축물대장·건축인허가 공통.

  BldRgstHubService  — 건축물대장(현행 등록 상태). 표제부·층별개요·전유부·전유공용면적 등.
  ArchPmsHubService  — 건축인허가(세움터 허가 시점). 오래된 건물은 레코드 없을 수 있음.

키: .env 의 DATA_GO_KR_SERVICE_KEY (URL-encoded 값). 이 모듈이 unquote 해서 urllib 로
재인코딩한다 (encoded 원문을 그대로 붙이면 curl/urllib 에서 깨짐).
이 서비스들은 numOfRows 를 무시하고 페이지당 최대 100건만 반환한다. 503/빈응답이 잦다.
무료: 상세기능당 일 10,000회. 기본 가드 일 9,000 / 실행당 4,000.

사용:
    from datago_hub import fetch_all
    items = fetch_all("BldRgstHubService", "getBrFlrOulnInfo", sigungu_cd="11710", bjdong_cd="10100")
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _budget import Budget
from _env import require

HOST = "https://apis.data.go.kr/1613000/"
PAGE_SIZE = 100
MAX_RETRY = 9
ROOT = Path(__file__).resolve().parents[1]

# 서비스별 캐시 위치·예산 네임스페이스
_SERVICE = {
    "BldRgstHubService": ("건축물대장", "bldrgst"),
    "ArchPmsHubService": ("건축인허가", "archpms"),
}
_DAILY = int(os.environ.get("BLDHUB_DAILY_LIMIT", "9000"))
_PER_RUN = int(os.environ.get("BLDHUB_PER_RUN_LIMIT", "4000"))


def _key() -> str:
    return urllib.parse.unquote(require("DATA_GO_KR_SERVICE_KEY"))


def _ns(service: str) -> tuple[Path, str]:
    folder, short = _SERVICE.get(service, (service, service))
    return ROOT / "data" / folder / "_cache", short


def _budget(service: str, op: str) -> Budget:
    _, short = _ns(service)
    return Budget(f"{short}:{op}", monthly_limit=_DAILY * 31, per_run_limit=_PER_RUN, daily_limit=_DAILY)


def _get(service: str, op: str, params: dict, budget: Budget) -> dict:
    url = HOST + service + "/" + op + "?" + urllib.parse.urlencode(
        {"serviceKey": _key(), "_type": "json", **params})
    last_err: Exception | None = None
    for attempt in range(MAX_RETRY):
        budget.check()
        budget.commit()  # 실제 시도 직전에 카운트 (재시도·오류도 보수적으로 반영)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
            time.sleep(0.15)
            return out
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"{service}/{op} {params}: {MAX_RETRY}회 재시도 실패 — {last_err}")


def _items(payload: dict) -> tuple[list[dict], int]:
    body = payload.get("response", {}).get("body", {})
    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") not in ("00", "0", None):
        raise RuntimeError(f"API 오류: {header.get('resultCode')} {header.get('resultMsg')}")
    total = int(body.get("totalCount") or 0)
    raw = body.get("items")
    if not raw:
        return [], total
    item = raw.get("item") if isinstance(raw, dict) else raw
    if item is None:
        return [], total
    return (item if isinstance(item, list) else [item]), total


def fetch_all(service: str, op: str, *, sigungu_cd: str, bjdong_cd: str,
              extra: dict | None = None, use_cache: bool = True) -> list[dict]:
    """한 법정동의 op 전체 레코드를 페이지 순회로 수집. 결과를 _cache/ 에 저장·재사용."""
    cache_dir, _ = _ns(service)
    cache = cache_dir / op / f"{sigungu_cd}_{bjdong_cd}.json"
    if use_cache and cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))["items"]

    budget = _budget(service, op)
    base = {"sigunguCd": sigungu_cd, "bjdongCd": bjdong_cd, "numOfRows": str(PAGE_SIZE)}
    if extra:
        base.update(extra)
    first = _get(service, op, {**base, "pageNo": "1"}, budget)
    rows, total = _items(first)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    for page in range(2, pages + 1):
        more, _ = _items(_get(service, op, {**base, "pageNo": str(page)}, budget))
        rows.extend(more)

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"service": service, "op": op, "sigunguCd": sigungu_cd,
                                 "bjdongCd": bjdong_cd, "total_count": total, "fetched": len(rows),
                                 "items": rows}, ensure_ascii=False), encoding="utf-8")
    return rows


def budget_status(service: str, ops: list[str]) -> str:
    return " | ".join(_budget(service, op).status() for op in ops)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="건축HUB API 단건 확인")
    ap.add_argument("--service", default="BldRgstHubService")
    ap.add_argument("--op", default="getBrTitleInfo")
    ap.add_argument("--sigungu", default="11710")
    ap.add_argument("--bjdong", default="10100")
    a = ap.parse_args()
    got = fetch_all(a.service, a.op, sigungu_cd=a.sigungu, bjdong_cd=a.bjdong, use_cache=False)
    print(f"{a.service}/{a.op}: {len(got)}건")
    if got:
        print(json.dumps(got[0], ensure_ascii=False, indent=1))
