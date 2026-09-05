"""한국부동산원 R-ONE 상업용부동산 공실률 → 정규화 long 테이블. 프로파일 FC-21 보강.

임대료(`data/임대료/R-ONE_임대동향_분기.csv`)는 R-ONE 웹 다운로드 CSV(지역별 wide)를
파싱해 만들었지만, 공실률은 동일 형식의 다운로드 CSV를 보유하지 않아 R-ONE Open API
(`services/recommendation-api/scripts/rone_api.py`)로 직접 호출해 같은 long 스키마로 맞춘다.

원천: R-ONE Open API `SttsApiTblData.do` (`RONE_API_KEY` 필요, `.env`)
  - 중대형 상가 공실률: STATBL_ID=T249633134845544
  - 소규모 상가 공실률: STATBL_ID=T241833134686576
  - 집합 상가 공실률:   STATBL_ID=T243283134931290
  (오피스 공실률 TT244763134428698은 상가 소매 분석과 무관해 제외.
   임대료 쪽 "통합상가"는 지수 전용 파생값이라 공실률 API에 대응 표가 없음.)
  CLS_ID를 생략해 전국 전체 응답(표당 약 2,400행, 페이지네이션 pSize=1000)을 받고
  `CLS_FULLNM`이 "서울"로 시작하는 행만 남긴다. `WRTTIME_IDTFR_ID`(YYYYQQ, 분기월 2자리)를
  기존 임대료 CSV와 같은 `기준_년분기_코드`(YYYYQ)로 변환한다.

산출: data/임대료/R-ONE_공실률_분기.csv (long, 임대료 CSV와 같은 컬럼)
  상가유형,지표,grain,권역,R_ONE_상권,기준_년분기_코드,값
  임대료 CSV와 원천이 다르므로(웹 다운로드 vs 실시간 API) 별도 파일로 유지하고
  결합은 `scripts/build_commercial_cost_layer.py`에서 한다.

실행: PYTHONPATH=scripts .venv/bin/python3 scripts/ingest_vacancy_rate.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recommendation.env import load_env  # noqa: E402
from recommendation.paths import find_project_root  # noqa: E402
from rone_api import get_table_data, _data_rows  # noqa: E402

load_env()
ROOT = str(find_project_root(__file__))
OUT = os.path.join(ROOT, "data", "임대료", "R-ONE_공실률_분기.csv")
MANIFEST = os.path.join(ROOT, "data", "임대료", "manifest_공실률.json")

STATBL_IDS = {
    "중대형상가": "T249633134845544",
    "소규모상가": "T241833134686576",
    "집합상가": "T243283134931290",
}
PAGE_SIZE = 1000


def q_code(wrttime_idtfr_id: str) -> str | None:
    """"202403" -> "20243" (기존 임대료 CSV의 기준_년분기_코드 형식과 동일)."""
    s = str(wrttime_idtfr_id).strip()
    if len(s) != 6 or not s.isdigit():
        return None
    year, q2 = s[:4], s[4:6]
    q = str(int(q2))
    if q not in ("1", "2", "3", "4"):
        return None
    return f"{year}{q}"


class VacancyFetchError(RuntimeError):
    """R-ONE API가 오류/무자료 envelope를 반환함 — 빈 응답/페이지 종료와 구분해야 한다(PR #14 리뷰 P2)."""


def _result_code(payload: dict) -> str | None:
    """R-ONE 응답의 RESULT.CODE를 찾는다.

    정상 응답은 ``SttsApiTblData[0].head[*].RESULT``에 있지만, 오류·무자료
    응답은 그 래퍼 자체가 없이 최상위에 바로 ``{"RESULT": {...}}``로 온다
    (실호출로 확인: 잘못된 STATBL_ID → ``{"RESULT": {"CODE": "INFO-200", ...}}``).
    두 위치 모두 확인해야 오류를 빈 페이지로 오인하지 않는다.
    """
    top = payload.get("RESULT")
    if isinstance(top, dict):
        return top.get("CODE")
    blocks = payload.get("SttsApiTblData")
    if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
        for h in blocks[0].get("head", []):
            if isinstance(h, dict) and isinstance(h.get("RESULT"), dict):
                return h["RESULT"].get("CODE")
    return None


def fetch_all_rows(statbl_id: str) -> list[dict]:
    """Fetch every row and reject an incomplete but syntactically normal response.

    R-ONE can return a successful envelope with ``list_total_count`` larger
    than the rows returned by a later page. Treating that page as the end of
    pagination silently creates a partial snapshot, so the first page's total
    is used as an invariant for every subsequent page.
    """
    rows: list[dict] = []
    page = 1
    expected_total: int | None = None
    while True:
        payload = get_table_data(statbl_id, "QY", page=page, size=PAGE_SIZE)
        # 오류/무자료 envelope(RESULT.CODE != INFO-000)를 빈 페이지·정상 종료로
        # 취급하면(이전 버그) 일부 상가유형만 실패해도 나머지로 기존 스냅샷을
        # 덮어써서 그 유형의 공실률이 통째로 사라진다.
        result_code = _result_code(payload)
        if result_code != "INFO-000":
            raise VacancyFetchError(
                f"R-ONE API 정상 코드 누락/오류(STATBL_ID={statbl_id}, page={page}): {result_code}"
            )
        page_rows = _data_rows(payload)
        total_value = None
        try:
            total_value = payload["SttsApiTblData"][0]["head"][0]["list_total_count"]
            total = int(total_value)
        except (KeyError, IndexError, TypeError, ValueError):
            raise VacancyFetchError(
                f"R-ONE 응답의 list_total_count가 없습니다/올바르지 않습니다 "
                f"(STATBL_ID={statbl_id}, page={page})"
            ) from None
        if total < 0:
            raise VacancyFetchError(
                f"R-ONE 응답의 list_total_count가 음수입니다 "
                f"(STATBL_ID={statbl_id}, page={page}, total={total})"
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise VacancyFetchError(
                f"R-ONE 페이지별 list_total_count가 다릅니다 "
                f"(STATBL_ID={statbl_id}, page={page}, expected={expected_total}, got={total})"
            )

        rows.extend(page_rows)
        if len(rows) > expected_total:
            raise VacancyFetchError(
                f"R-ONE 응답 행수가 전체 건수를 초과했습니다 "
                f"(STATBL_ID={statbl_id}, page={page}, expected={expected_total}, got={len(rows)})"
            )
        if not page_rows and len(rows) < expected_total:
            raise VacancyFetchError(
                f"R-ONE 페이지가 비어 있지만 전체 건수를 채우지 못했습니다 "
                f"(STATBL_ID={statbl_id}, page={page}, expected={expected_total}, got={len(rows)})"
            )
        if len(rows) == expected_total:
            break
        page += 1
        time.sleep(0.2)
    return rows


def parse_rows(styp: str, api_rows: list[dict]) -> list[list]:
    out = []
    for r in api_rows:
        full = str(r.get("CLS_FULLNM") or "")
        parts = [p.strip() for p in full.split(">") if p.strip()]
        if not parts or parts[0] != "서울":
            continue
        if len(parts) == 1:
            grain, gwon, sang = "서울전체", "", ""
        elif len(parts) == 2:
            grain, gwon, sang = "권역", parts[1], ""
        else:
            grain, gwon, sang = "상권", parts[1], parts[2]
        qc = q_code(r.get("WRTTIME_IDTFR_ID"))
        val = r.get("DTA_VAL")
        if qc is None or val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        out.append([styp, "공실률", grain, gwon, sang, qc, val])
    return out


def main() -> int:
    # PR #14 리뷰(ziholee) P2: 상가유형 중 하나라도 API가 실패하면 절대 기존 스냅샷을
    # 덮어쓰지 않는다 — 성공한 유형만으로 파일을 만들면 실패한 유형의 공실률이
    # 조용히 사라진다(추천 파이프라인은 이 CSV가 3개 유형 다 있다고 가정). 전부
    # 성공했을 때만 임시 파일에 쓰고 os.replace()로 원자적 교체한다.
    all_rows: list[list] = []
    call_log = []
    failures: list[str] = []
    for styp, statbl_id in STATBL_IDS.items():
        try:
            api_rows = fetch_all_rows(statbl_id)
        except VacancyFetchError as exc:
            print(f"  {styp:8s} STATBL_ID={statbl_id}  FAIL: {exc}")
            failures.append(styp)
            continue
        if not api_rows:
            print(f"  {styp:8s} STATBL_ID={statbl_id}  FAIL: 정상 응답이지만 원천 행이 0개")
            failures.append(styp)
            continue
        seoul_rows = parse_rows(styp, api_rows)
        if not seoul_rows:
            print(f"  {styp:8s} STATBL_ID={statbl_id}  FAIL: 서울 공실률 행이 0개")
            failures.append(styp)
            continue
        all_rows += seoul_rows
        call_log.append({"상가유형": styp, "statbl_id": statbl_id,
                          "전국_응답행수": len(api_rows), "서울_행수": len(seoul_rows)})
        print(f"  {styp:8s} STATBL_ID={statbl_id}  전국 {len(api_rows):,}행 → 서울 {len(seoul_rows):,}행")

    if failures:
        print(f"FAIL: {', '.join(failures)} API 호출 실패 — 기존 스냅샷 유지, 파일 갱신 안 함")
        return 1
    if not all_rows:
        print("FAIL: 서울 공실률 행을 하나도 받지 못함")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp_out = f"{OUT}.tmp"
    with open(tmp_out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상가유형", "지표", "grain", "권역", "R_ONE_상권", "기준_년분기_코드", "값"])
        w.writerows(sorted(all_rows))
    os.replace(tmp_out, OUT)

    n_sang = len({r[4] for r in all_rows if r[2] == "상권"})
    n_gwon = len({r[3] for r in all_rows if r[2] == "권역"})
    qs = sorted({r[5] for r in all_rows})

    manifest = {
        "generated_by": "services/recommendation-api/scripts/ingest_vacancy_rate.py",
        "source": "R-ONE Open API SttsApiTblData.do",
        "statbl_ids": STATBL_IDS,
        "dtacycle_cd": "QY",
        "call_log": call_log,
        "row_count": len(all_rows),
        "grain": {"서울전체": 1, "권역": n_gwon, "상권": n_sang},
        "quarter_range": [qs[0], qs[-1]] if qs else None,
        "quarters": qs,
        "limitations": [
            "오피스 공실률은 상가 소매 분석과 무관해 제외",
            "통합상가는 공실률 API에 대응 표가 없어 임대료 지수 전용으로만 존재",
            "R-ONE 상권 grain은 서울시 상권분석 1,650 상권과 다른 조사구역 — "
            "output/crosswalks/crosswalk_rone_trdar.csv의 join_eligible=yes 매핑 경유 필요",
        ],
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n→ {os.path.relpath(OUT, ROOT)}  {len(all_rows):,}행")
    print(f"  상가유형 {len(STATBL_IDS)}, grain 서울전체/권역({n_gwon})/상권({n_sang})")
    print(f"  분기 {qs[0]}~{qs[-1]} ({len(qs)})")
    print(f"→ {os.path.relpath(MANIFEST, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
