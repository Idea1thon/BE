"""R-ONE 임대료·공실률(R-ONE 상권 grain) → 서울시 상권분석 상권(TRDAR_CD) "비용 부담" 근거.
metric-contract.md "비용 부담"(FC-20 임대료, FC-21 공실률) 차원 구현.

원천 2개(제공 grain이 서로 다른 proxy이므로 crosswalk 경유 결합):
  - data/임대료/R-ONE_임대동향_분기.csv       (임대료_천원㎡·임대가격지수, R-ONE 상권 72개)
  - data/임대료/R-ONE_공실률_분기.csv         (공실률, R-ONE 상권 72개, scripts/ingest_vacancy_rate.py)
  - output/crosswalks/crosswalk_rone_trdar.csv (R-ONE 상권 ↔ 서울시 상권분석 TRDAR_CD proxy,
    scripts/build_rone_trdar_crosswalk.py) — **join_eligible=yes만 사용**(52개 상권).

이 레이어는 스코어링(성공확률) 피처가 아니라 "비용 부담" 근거 차원 값이다
([[rent_excluded_from_scoring]] — 통합 다변량 회귀에서 매출 고유 기여 없음이 이미 확정됨).
`entry_health_v1`이나 후보 순위 산식에 넣지 말고 후보 카드에 "비용 신호"로만 표시한다.

분위는 **join_eligible로 매칭된 상권 집단(최대 52개) 안에서만** 계산한다 — 서울 전체
1,650개 상권 대표 분위가 아니다(metric-contract.md "지역별 표본이 너무 적으면 순위를
만들지 않고 근거 부족으로 표시" 원칙에 따라 표본 크기를 manifest·컬럼에 명시).

산출: output/cost_dimension/상권_비용부담_분기.csv + manifest.json
실행: .venv/bin/python3 scripts/build_commercial_cost_layer.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENT_CSV = os.path.join(ROOT, "data", "임대료", "R-ONE_임대동향_분기.csv")
VACANCY_CSV = os.path.join(ROOT, "data", "임대료", "R-ONE_공실률_분기.csv")
CROSSWALK_CSV = os.path.join(ROOT, "output", "crosswalks", "crosswalk_rone_trdar.csv")
OUT_DIR = os.path.join(ROOT, "output", "cost_dimension")
OUT_CSV = os.path.join(OUT_DIR, "상권_비용부담_분기.csv")
MANIFEST = os.path.join(OUT_DIR, "manifest.json")
MIN_SAMPLE_FOR_PERCENTILE = 10  # metric-contract.md "표본이 너무 적으면 순위를 만들지 않고 근거 부족으로 표시"


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_crosswalk() -> dict[str, dict]:
    rows = read_csv(CROSSWALK_CSV)
    eligible = [r for r in rows if r["join_eligible"] == "yes"]
    return {r["R_ONE_상권"]: r for r in eligible}


def percentile_rank(value: float, population: list[float]) -> float:
    """population 안에서 value 이하인 비율(0~100). population에 value 포함."""
    if not population:
        return float("nan")
    n_le = sum(1 for v in population if v <= value)
    return round(100.0 * n_le / len(population), 1)


def main() -> int:
    if not (os.path.isfile(RENT_CSV) and os.path.isfile(VACANCY_CSV) and os.path.isfile(CROSSWALK_CSV)):
        missing = [p for p in (RENT_CSV, VACANCY_CSV, CROSSWALK_CSV) if not os.path.isfile(p)]
        print(f"FAIL: 원천 없음 — {missing}")
        return 1

    crosswalk = load_crosswalk()
    rent_rows = [r for r in read_csv(RENT_CSV) if r["grain"] == "상권"]
    vac_rows = [r for r in read_csv(VACANCY_CSV) if r["grain"] == "상권"]

    # key: (상가유형, R_ONE_상권, 기준_년분기_코드) -> {임대료_천원㎡, 임대가격지수, 공실률}
    merged: dict[tuple, dict] = defaultdict(dict)
    for r in rent_rows:
        key = (r["상가유형"], r["R_ONE_상권"], r["기준_년분기_코드"])
        field = "임대료_천원㎡" if r["지표"] == "임대료_천원㎡" else "임대가격지수"
        merged[key][field] = float(r["값"])
    for r in vac_rows:
        key = (r["상가유형"], r["R_ONE_상권"], r["기준_년분기_코드"])
        merged[key]["공실률"] = float(r["값"])

    joined = []
    unmatched_sangkwon = set()
    for (styp, sang, qc), vals in merged.items():
        cw = crosswalk.get(sang)
        if cw is None:
            unmatched_sangkwon.add(sang)
            continue
        joined.append({
            "TRDAR_CD": cw["TRDAR_CD"],
            "TRDAR_CD_N": cw["TRDAR_CD_N"],
            "SIGNGU_NM": cw["SIGNGU_NM"],
            "ADSTRD_NM": cw["ADSTRD_NM"],
            "R_ONE_상권": sang,
            "상가유형": styp,
            "기준_년분기_코드": qc,
            "임대료_천원㎡": vals.get("임대료_천원㎡"),
            "임대가격지수": vals.get("임대가격지수"),
            "공실률": vals.get("공실률"),
            "mapping_method": cw["mapping_method"],
            "mapping_confidence": cw["mapping_confidence"],
        })

    # 분위: (상가유형, 기준분기) 그룹 안에서 join된 TRDAR_CD 집단 기준
    by_group_rent: dict[tuple, list[float]] = defaultdict(list)
    by_group_vac: dict[tuple, list[float]] = defaultdict(list)
    for row in joined:
        gkey = (row["상가유형"], row["기준_년분기_코드"])
        if row["임대료_천원㎡"] is not None:
            by_group_rent[gkey].append(row["임대료_천원㎡"])
        if row["공실률"] is not None:
            by_group_vac[gkey].append(row["공실률"])

    for row in joined:
        gkey = (row["상가유형"], row["기준_년분기_코드"])
        pop_rent = by_group_rent.get(gkey, [])
        pop_vac = by_group_vac.get(gkey, [])
        row["매칭상권_임대료표본수"] = len(pop_rent)
        row["매칭상권_공실률표본수"] = len(pop_vac)
        row["임대료_표본부족"] = len(pop_rent) < MIN_SAMPLE_FOR_PERCENTILE
        row["공실률_표본부족"] = len(pop_vac) < MIN_SAMPLE_FOR_PERCENTILE
        row["임대료_분위_매칭상권내"] = (
            percentile_rank(row["임대료_천원㎡"], pop_rent)
            if row["임대료_천원㎡"] is not None and not row["임대료_표본부족"] else None
        )
        row["공실률_분위_매칭상권내"] = (
            percentile_rank(row["공실률"], pop_vac)
            if row["공실률"] is not None and not row["공실률_표본부족"] else None
        )
        row["join_eligible"] = True
        row["grain_is_proxy"] = True

    os.makedirs(OUT_DIR, exist_ok=True)
    fieldnames = [
        "TRDAR_CD", "TRDAR_CD_N", "SIGNGU_NM", "ADSTRD_NM", "R_ONE_상권", "상가유형",
        "기준_년분기_코드", "임대료_천원㎡", "임대가격지수", "공실률",
        "매칭상권_임대료표본수", "매칭상권_공실률표본수", "임대료_표본부족", "공실률_표본부족",
        "임대료_분위_매칭상권내", "공실률_분위_매칭상권내",
        "mapping_method", "mapping_confidence", "join_eligible", "grain_is_proxy",
    ]
    joined.sort(key=lambda r: (r["상가유형"], r["기준_년분기_코드"], r["TRDAR_CD"]))
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(joined)

    n_trdar = len({r["TRDAR_CD"] for r in joined})
    n_rent_val = sum(1 for r in joined if r["임대료_천원㎡"] is not None)
    n_vac_val = sum(1 for r in joined if r["공실률"] is not None)
    qs = sorted({r["기준_년분기_코드"] for r in joined})
    styps = sorted({r["상가유형"] for r in joined})

    manifest = {
        "generated_by": "scripts/build_commercial_cost_layer.py",
        "sources": {
            "임대료·지수": os.path.relpath(RENT_CSV, ROOT),
            "공실률": os.path.relpath(VACANCY_CSV, ROOT),
            "crosswalk": os.path.relpath(CROSSWALK_CSV, ROOT),
        },
        "join_rule": "crosswalk join_eligible=yes만 사용 (52개 R-ONE 상권 → TRDAR_CD)",
        "row_count": len(joined),
        "matched_trdar_count": n_trdar,
        "매칭상권_실측임대료_보유행": n_rent_val,
        "매칭상권_공실률_보유행": n_vac_val,
        "상가유형": styps,
        "quarter_range": [qs[0], qs[-1]] if qs else None,
        "unmatched_r_one_상권_count": len(unmatched_sangkwon),
        "unmatched_r_one_상권_examples": sorted(unmatched_sangkwon)[:10],
        "scoring_use": "금지 — 비용 부담 근거 차원(표시용)에만 사용. entry_health_v1·후보 정렬 입력 아님",
        "limitations": [
            f"분위는 매칭된 상권 집단({n_trdar}개, 서울 전체 1,650개 상권의 일부)만의 내부 분위 — "
            "서울 전체 대표 분위 아님. 표본 밖 상권은 비용 근거 자체가 없음(missing_features 표시).",
            f"(상가유형×분기) 그룹 표본이 {MIN_SAMPLE_FOR_PERCENTILE}개 미만이면 분위를 계산하지 않고 "
            "임대료_표본부족·공실률_표본부족=true로 표시한다(metric-contract.md 근거 부족 원칙).",
            "R-ONE 상권 ≠ 서울시 상권분석 상권. crosswalk는 1차 명칭 proxy(grain_is_proxy=true).",
            "임대료_천원㎡(실측값)는 소규모상가만 존재. 중대형·집합상가는 임대가격지수(상대지수)뿐.",
            "공실률은 소규모·중대형·집합상가 3종. 오피스·통합상가는 없음.",
            "분기 갱신 주기: R-ONE 분기 공표. 최신 분기는 실행 시점에 따라 서울시 상권분석 분기(예: 20261)보다 늦게 갱신될 수 있음.",
        ],
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"→ {os.path.relpath(OUT_CSV, ROOT)}  {len(joined):,}행")
    print(f"  매칭 상권(TRDAR_CD) {n_trdar}개 · 상가유형 {styps} · 분기 {qs[0]}~{qs[-1]}")
    print(f"  실측임대료 보유 {n_rent_val}행 · 공실률 보유 {n_vac_val}행")
    print(f"  미매칭 R-ONE 상권 {len(unmatched_sangkwon)}개 (review/unresolved, crosswalk join_eligible=no)")
    print(f"→ {os.path.relpath(MANIFEST, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
