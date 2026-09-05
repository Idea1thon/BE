"""한국부동산원 R-ONE 상업용부동산 공실률 → 정규화 long 테이블. 프로파일 FC-21 보강.

임대료(`data/임대료/R-ONE_임대동향_분기.csv`)는 R-ONE 웹 다운로드 CSV(지역별 wide)를
파싱해 만들었지만, 공실률은 동일 형식의 다운로드 CSV를 보유하지 않아 R-ONE Open API
(`scripts/rone_api.py`)로 직접 호출해 같은 long 스키마로 맞춘다.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rone_api import get_table_data, _data_rows  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def fetch_all_rows(statbl_id: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        payload = get_table_data(statbl_id, "QY", page=page, size=PAGE_SIZE)
        page_rows = _data_rows(payload)
        rows.extend(page_rows)
        total = None
        try:
            total = payload["SttsApiTblData"][0]["head"][0]["list_total_count"]
        except (KeyError, IndexError, TypeError):
            pass
        if not page_rows or total is None or len(rows) >= int(total):
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
    all_rows: list[list] = []
    call_log = []
    for styp, statbl_id in STATBL_IDS.items():
        api_rows = fetch_all_rows(statbl_id)
        seoul_rows = parse_rows(styp, api_rows)
        all_rows += seoul_rows
        call_log.append({"상가유형": styp, "statbl_id": statbl_id,
                          "전국_응답행수": len(api_rows), "서울_행수": len(seoul_rows)})
        print(f"  {styp:8s} STATBL_ID={statbl_id}  전국 {len(api_rows):,}행 → 서울 {len(seoul_rows):,}행")

    if not all_rows:
        print("FAIL: 서울 공실률 행을 하나도 받지 못함")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상가유형", "지표", "grain", "권역", "R_ONE_상권", "기준_년분기_코드", "값"])
        w.writerows(sorted(all_rows))

    n_sang = len({r[4] for r in all_rows if r[2] == "상권"})
    n_gwon = len({r[3] for r in all_rows if r[2] == "권역"})
    qs = sorted({r[5] for r in all_rows})

    manifest = {
        "generated_by": "scripts/ingest_vacancy_rate.py",
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
