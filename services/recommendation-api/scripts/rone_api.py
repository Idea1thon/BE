"""한국부동산원 R-ONE 부동산통계정보 Open API 래퍼.

R-ONE 개발가이드의 ``SttsApiTbl.do``(통계표 목록)와
``SttsApiTblData.do``(통계자료)를 백엔드/배치에서 호출하기 위한
의존성 없는 클라이언트다. 인증키는 프로젝트 루트의 ``.env``에서
``RONE_API_KEY``로 읽고 URL·로그에 출력하지 않는다.

예시:
  .venv/bin/python3 scripts/rone_api.py tables --contains 임대료
  .venv/bin/python3 scripts/rone_api.py data \
      --statbl-id T244363134858603 --cycle QY \
      --cls-id 500002 --itm-id 100001 --summary

주의:
  R-ONE의 ``CLS_ID``는 지역/분류 코드, ``ITM_ID``는 지표 코드다.
  표마다 코드가 다를 수 있으므로 먼저 ``data``를 필터 없이 소량
  조회하거나 공식 통계코드 검색에서 확인한 뒤 사용한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation.env import load_env  # noqa: E402


def require(name: str) -> str:
    load_env()
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"환경변수 {name} 가 비어 있음. services/recommendation-api/.env를 확인하세요."
        )
    return value

BASE_URL = "https://www.reb.or.kr/r-one/openapi"
TABLES_ENDPOINT = f"{BASE_URL}/SttsApiTbl.do"
DATA_ENDPOINT = f"{BASE_URL}/SttsApiTblData.do"


class ROneApiError(RuntimeError):
    """R-ONE Open API 호출 실패."""


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("RONE_API_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def _request(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """JSON 응답을 반환한다. 인증/파라미터 오류는 재시도하지 않는다."""
    query_params = {"KEY": require("RONE_API_KEY"), "Type": "json", **params}
    query = urllib.parse.urlencode(
        {k: v for k, v in query_params.items() if v is not None}
    )
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )

    last_error: str | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=_timeout()) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ROneApiError("R-ONE 응답이 JSON object가 아닙니다.")
            return payload
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in (400, 401, 403, 404):
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

    raise ROneApiError(f"R-ONE API 호출 실패: {last_error}")


def list_tables(*, page: int = 1, size: int = 1000) -> dict[str, Any]:
    """R-ONE 공개 통계표 목록을 반환한다."""
    if not 1 <= page:
        raise ValueError("page는 1 이상이어야 합니다.")
    if not 1 <= size <= 1000:
        raise ValueError("size는 1~1000이어야 합니다.")
    return _request(TABLES_ENDPOINT, {"pIndex": page, "pSize": size})


def get_table_data(
    statbl_id: str,
    dtacycle_cd: str,
    *,
    cls_id: int | str | None = None,
    itm_id: int | str | None = None,
    start_wrt_time: str | None = None,
    end_wrt_time: str | None = None,
    page: int = 1,
    size: int = 100,
) -> dict[str, Any]:
    """통계표 자료를 반환한다.

    ``cls_id``·``itm_id``를 생략하면 표의 여러 지역/지표가 섞여 반환될
    수 있다. API 문서 권고대로 운영 호출에서는 가능한 한 두 값을 함께
    지정한다.
    """
    statbl_id = str(statbl_id).strip()
    dtacycle_cd = str(dtacycle_cd).strip()
    if not statbl_id or not dtacycle_cd:
        raise ValueError("statbl_id와 dtacycle_cd는 필수입니다.")
    if not 1 <= page:
        raise ValueError("page는 1 이상이어야 합니다.")
    if not 1 <= size <= 1000:
        raise ValueError("size는 1~1000이어야 합니다.")

    return _request(
        DATA_ENDPOINT,
        {
            "STATBL_ID": statbl_id,
            "DTACYCLE_CD": dtacycle_cd,
            "CLS_ID": cls_id,
            "ITM_ID": itm_id,
            "START_WRTTIME": start_wrt_time,
            "END_WRTTIME": end_wrt_time,
            "pIndex": page,
            "pSize": size,
        },
    )


def _table_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """R-ONE 표 목록의 중첩 JSON을 평탄화한다."""
    block = payload.get("SttsApiTbl", [])
    if not isinstance(block, list) or len(block) < 2:
        return []
    rows = block[1].get("row", [])
    return rows if isinstance(rows, list) else []


def _data_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """R-ONE 자료 응답의 중첩 JSON을 평탄화한다."""
    block = payload.get("SttsApiTblData", [])
    if not isinstance(block, list) or len(block) < 2:
        return []
    rows = block[1].get("row", [])
    return rows if isinstance(rows, list) else []


def _head(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    block = payload.get(key, [])
    if not isinstance(block, list) or not block:
        return []
    return block[0].get("head", []) if isinstance(block[0], dict) else []


def _summary(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind == "tables":
        key = "SttsApiTbl"
        rows = _table_rows(payload)
    else:
        key = "SttsApiTblData"
        rows = _data_rows(payload)
    return {
        "head": _head(payload, key),
        "row_count_returned": len(rows),
        "first_row": rows[0] if rows else None,
        "last_row": rows[-1] if rows else None,
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="R-ONE Open API 단일 호출")
    sub = ap.add_subparsers(dest="command", required=True)

    table_ap = sub.add_parser("tables", help="통계표 목록")
    table_ap.add_argument("--page", type=int, default=1)
    table_ap.add_argument("--size", type=int, default=1000)
    table_ap.add_argument("--contains", help="표명 필터(로컬 출력 필터)")
    table_ap.add_argument("--summary", action="store_true")

    data_ap = sub.add_parser("data", help="통계표 자료")
    data_ap.add_argument("--statbl-id", required=True)
    data_ap.add_argument("--cycle", dest="dtacycle_cd", required=True)
    data_ap.add_argument("--cls-id")
    data_ap.add_argument("--itm-id")
    data_ap.add_argument("--start")
    data_ap.add_argument("--end")
    data_ap.add_argument("--page", type=int, default=1)
    data_ap.add_argument("--size", type=int, default=100)
    data_ap.add_argument("--summary", action="store_true")

    args = ap.parse_args()
    if args.command == "tables":
        payload = list_tables(page=args.page, size=args.size)
        if args.contains:
            rows = [
                row for row in _table_rows(payload)
                if args.contains in str(row.get("STATBL_NM", ""))
            ]
            result: Any = rows
        elif args.summary:
            result = _summary(payload, kind="tables")
        else:
            result = payload
    else:
        payload = get_table_data(
            args.statbl_id,
            args.dtacycle_cd,
            cls_id=args.cls_id,
            itm_id=args.itm_id,
            start_wrt_time=args.start,
            end_wrt_time=args.end,
            page=args.page,
            size=args.size,
        )
        result = _summary(payload, kind="data") if args.summary else payload
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
