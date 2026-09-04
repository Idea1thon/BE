"""데모 가맹점 20개 선정 + 상권×업종 실측 market_data 조립.

산출: artifacts/risk-siren/demo/market_baseline.json
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import INDUSTRY_LABELS
from .scenarios import SCENARIO_ASSIGNMENT
from .seoul_data import (
    load_closure_panel,
    load_market_sales,
    load_permits_near,
    load_trade_area_meta,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "artifacts/risk-siren/demo"

TARGET_GU = {
    "강남구", "서초구", "마포구", "송파구", "종로구",
    "영등포구", "용산구", "성동구", "광진구", "중구",
}
TARGET_CATEGORY = {"발달상권", "골목상권", "관광특구"}
INDUSTRIES = [f"CS1000{n:02d}" for n in range(1, 11)]

# 데모 창: 2024-04 ~ 2026-03. 앵커에 필요한 최소 분기: 2023Q1(YoY) ~ 2026Q1
REQUIRED_SALES_QUARTERS = {
    f"{y}Q{q}" for y in (2023, 2024, 2025) for q in (1, 2, 3, 4)
} | {"2026Q1"}
COMPETITION_RADIUS_M = 250.0


def _brand_name(gu: str, area_name: str, industry_label: str, idx: int) -> str:
    return f"데모 {industry_label} {area_name}점"


def select_branches() -> list[dict]:
    meta = load_trade_area_meta()
    panel = load_closure_panel()
    sales = load_market_sales()

    scored: list[tuple] = []
    for (area, ind), rows in panel.items():
        if ind not in INDUSTRIES:
            continue
        m = meta.get(area)
        if not m or m["gu_name"] not in TARGET_GU or m["category"] not in TARGET_CATEGORY:
            continue
        full_q = sum(1 for r in rows if r["quarter_status"] == "완전")
        sq = {s["quarter"] for s in sales.get((area, ind), [])}
        if full_q < 18 or not REQUIRED_SALES_QUARTERS.issubset(sq):
            continue
        # 최근 8분기 내 영업중 5 미만 (폐업률 분모 0 위험) 이면 제외
        recent = [r for r in rows if r["quarter"] >= "2024Q1"]
        if any(r["active_count_end"] < 5 for r in recent) or len(recent) < 8:
            continue
        # 시장 매출 셀이 너무 작거나 분기 변동이 극단적이면 제외 (마이크로 셀 배제)
        svals = [s["amount_krw"] for s in sales.get((area, ind), []) if s["quarter"] >= "2024Q1"]
        if len(svals) < 6 or sum(svals) / len(svals) < 300_000_000:
            continue
        extreme = any(
            abs(svals[i] - svals[i - 1]) / svals[i - 1] > 0.7
            for i in range(1, len(svals))
            if svals[i - 1] > 0
        )
        if extreme:
            continue
        # deterministic score: prefer 발달/관광, more full quarters, stable order
        cat_rank = {"발달상권": 0, "관광특구": 1, "골목상권": 2}[m["category"]]
        scored.append((cat_rank, -full_q, m["gu_name"], area, ind))
    scored.sort()

    # 업종 2개씩 · 자치구 분산 라운드로빈 (부족하면 제약 완화해 20개 보장)
    by_ind: dict[str, list[tuple]] = {i: [] for i in INDUSTRIES}
    for row in scored:
        by_ind[row[4]].append(row)

    def _emit(row: tuple) -> dict:
        _, _, gu_name, area, industry = row
        m = meta[area]
        return {
            "trade_area_code": area,
            "trade_area_name": m["name"],
            "trade_area_category": m["category"],
            "gu_code": m["gu_code"],
            "gu_name": gu_name,
            "admin_dong_code": m["admin_dong_code"],
            "admin_dong_name": m["admin_dong_name"],
            "industry_code": industry,
            "industry_label": INDUSTRY_LABELS[industry],
            "x_5181": m["x_5181"],
            "y_5181": m["y_5181"],
        }

    picked: list[dict] = []
    used: set = set()
    seen_gu: dict[str, int] = {}
    for gu_cap in (3, 6, 99):  # 완화 라운드
        for target_per_ind in (2, 3):
            for ind in INDUSTRIES:
                take = sum(1 for p in picked if p["industry_code"] == ind)
                for row in by_ind[ind]:
                    if len(picked) >= 20 or take >= target_per_ind:
                        break
                    if (row[3], row[4]) in used:
                        continue
                    if seen_gu.get(row[2], 0) >= gu_cap:
                        continue
                    picked.append(_emit(row))
                    used.add((row[3], row[4]))
                    seen_gu[row[2]] = seen_gu.get(row[2], 0) + 1
                    take += 1
            if len(picked) >= 20:
                break
        if len(picked) >= 20:
            break

    picked = picked[:20]
    for idx, b in enumerate(picked):
        b["branch_id"] = f"demo-br-{idx + 1:02d}"
        b["franchise_id"] = f"demo-fr-{(idx % 6) + 1:02d}"
        b["brand_name"] = _brand_name(
            b["gu_name"], b["trade_area_name"], b["industry_label"], idx
        )
        b["scenario"] = SCENARIO_ASSIGNMENT[idx]
        # 상권 중심에서 결정론적 소폭 이동 (동일 상권 중복 방지용 표시)
        b["x_5181"] = round(b["x_5181"] + (idx % 5 - 2) * 12.0, 2)
        b["y_5181"] = round(b["y_5181"] + (idx // 5 - 2) * 12.0, 2)
    return picked


def build_baseline() -> dict:
    branches = select_branches()
    panel = load_closure_panel()
    sales = load_market_sales()

    centers = [
        {"branch_id": b["branch_id"], "x_5181": b["x_5181"], "y_5181": b["y_5181"]}
        for b in branches
    ]
    permits = load_permits_near(centers, max_radius_m=350.0)

    for b in branches:
        key = (b["trade_area_code"], b["industry_code"])
        b["closure_quarters"] = panel.get(key, [])
        b["sales_quarters"] = sales.get(key, [])
        b["permits"] = permits.get(b["branch_id"], [])

    return {
        "generated_for": "risk-siren demo (대회)",
        "source": "seoul_open_data:인허가_상권분기_패널+추정매출+음식점_인허가_서울",
        "demo_window": {"start": "2024-04", "end": "2026-03"},
        "competition_radius_m": COMPETITION_RADIUS_M,
        "branch_count": len(branches),
        "branches": branches,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    baseline = build_baseline()
    path = OUT_DIR / "market_baseline.json"
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path} ({len(baseline['branches'])} branches)")
    for b in baseline["branches"]:
        print(
            f"  {b['branch_id']} {b['scenario']:<20} {b['gu_name']} "
            f"{b['trade_area_name']} / {b['industry_label']} "
            f"(closure {len(b['closure_quarters'])}q, sales {len(b['sales_quarters'])}q, "
            f"permits≤700m {len(b['permits'])})"
        )


if __name__ == "__main__":
    main()
