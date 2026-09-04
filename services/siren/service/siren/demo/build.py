"""데모 전체 파이프라인 실행.

  python -m service.siren.demo.build            # 전체 생성 + 실행
  python -m service.siren.demo.build --reuse    # 기존 market_baseline.json 재사용

산출: artifacts/risk-siren/demo/{market_baseline.json, branch_reports/, requests/,
      results/, timeline/, hq_summary.json, SUMMARY.md}
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from ..hq_summary import summarize
from ..models import INDUSTRY_LABELS
from ..pipeline import analyze
from .baseline import OUT_DIR, build_baseline
from .generate import DEMO_START, generate_branch
from .scenarios import MONTHS, SCENARIOS

# 유사업종 매핑 (SR-03) — provisional, 계약 승인 대상
SIMILAR_INDUSTRY = {
    "CS100001": ["CS100008", "CS100002"],
    "CS100002": ["CS100001", "CS100003"],
    "CS100003": ["CS100004", "CS100002"],
    "CS100004": ["CS100003", "CS100006"],
    "CS100005": ["CS100010"],
    "CS100006": ["CS100008", "CS100007"],
    "CS100007": ["CS100009", "CS100006"],
    "CS100008": ["CS100001", "CS100006"],
    "CS100009": ["CS100007", "CS100001"],
    "CS100010": ["CS100005"],
}
COMPETITION_RADIUS_M = 250.0


def _month_at(t: int) -> tuple[int, int]:
    idx = (DEMO_START[0] * 12 + DEMO_START[1] - 1) + t
    return idx // 12, idx % 12 + 1


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _shift_month(d: date, months: int) -> date:
    idx = d.year * 12 + d.month - 1 + months
    y, m = idx // 12, idx % 12 + 1
    return date(y, m, 1)


def competition_block(branch: dict, as_of: date) -> dict:
    same = branch["industry_code"]
    similar = set(SIMILAR_INDUSTRY.get(same, []))
    r2 = COMPETITION_RADIUS_M ** 2
    month_start = as_of.replace(day=1)
    recent_hi = _shift_month(month_start, 1)
    recent_lo = _shift_month(month_start, -2)
    prev_hi = recent_lo
    prev_lo = _shift_month(recent_lo, -3)

    def count(codes: set[str], lo: date, hi: date) -> int:
        n = 0
        for p in branch["permits"]:
            if not p["permit_date"]:
                continue
            dx = p["x_5181"] - branch["x_5181"]
            dy = p["y_5181"] - branch["y_5181"]
            if dx * dx + dy * dy > r2:
                continue
            if p["industry_code"] not in codes:
                continue
            try:
                pd = date.fromisoformat(p["permit_date"])
            except ValueError:
                continue
            if not (lo <= pd < hi):
                continue
            if p["close_date"]:
                try:
                    if date.fromisoformat(p["close_date"]) <= as_of:
                        continue
                except ValueError:
                    pass
            n += 1
        return n

    return {
        "radius_m": COMPETITION_RADIUS_M,
        "same_industry_new_recent_3m": count({same}, recent_lo, recent_hi),
        "same_industry_new_previous_3m": count({same}, prev_lo, prev_hi),
        "similar_industry_new_recent_3m": count(similar, recent_lo, recent_hi) if similar else 0,
        "similar_industry_new_previous_3m": count(similar, prev_lo, prev_hi) if similar else 0,
        "similar_industry_codes": sorted(similar),
        "active_only": True,
        "source": "seoul_open_data:음식점_인허가_서울",
    }


def _completed_quarter(as_of: date) -> str:
    qn = (as_of.month - 1) // 3 + 1
    end_month = qn * 3
    end_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    if as_of.month == end_month and as_of.day >= end_day:
        return f"{as_of.year}Q{qn}"
    prev = as_of.year * 4 + (qn - 1) - 1
    return f"{prev // 4}Q{prev % 4 + 1}"


def make_request(branch: dict, generated: dict, as_of: date) -> dict:
    reports = [r for r in generated["branch_reports"] if r["month"] <= f"{as_of.year:04d}-{as_of.month:02d}"]
    last_q = _completed_quarter(as_of)
    keep_q = lambda rows: [q for q in rows if q["quarter"] <= last_q]  # noqa: E731
    return {
        "request_id": f"demo-{branch['branch_id']}-{as_of.isoformat()}",
        "franchise_id": branch["franchise_id"],
        "branch_id": branch["branch_id"],
        "brand_name": branch["brand_name"],
        "as_of": as_of.isoformat(),
        "industry_code": branch["industry_code"],
        "location": {
            "gu_code": branch["gu_code"],
            "admin_dong_code": branch["admin_dong_code"] or None,
            "trade_area_code": branch["trade_area_code"],
            "x_5181": branch["x_5181"],
            "y_5181": branch["y_5181"],
        },
        "market_data": {
            "source": "seoul_open_data",
            "closure_source": "seoul_open_data:음식점_상권분기_패널",
            "sales_source": "seoul_open_data:추정매출-상권",
            "as_of_quarter": last_q,
            "closure_quarters": keep_q(branch["closure_quarters"]),
            "sales_quarters": keep_q(branch["sales_quarters"]),
            "competition": competition_block(branch, as_of),
        },
        "branch_reports": reports,
        "reviews": generated["reviews"],
        "options": {"llm_mode": "explanation_only", "send_notifications": False},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="기존 market_baseline.json 재사용")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "branch_reports").mkdir(exist_ok=True)
    (OUT_DIR / "requests").mkdir(exist_ok=True)
    (OUT_DIR / "results").mkdir(exist_ok=True)
    (OUT_DIR / "timeline").mkdir(exist_ok=True)

    baseline_path = OUT_DIR / "market_baseline.json"
    if args.reuse and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    else:
        baseline = build_baseline()
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    final_as_of = _month_end(*_month_at(MONTHS - 1))
    final_results: list[dict] = []
    summary_rows: list[tuple] = []

    for branch in baseline["branches"]:
        generated = generate_branch(branch)
        (OUT_DIR / "branch_reports" / f"{branch['branch_id']}.json").write_text(
            json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 최종 스냅샷
        req = make_request(branch, generated, final_as_of)
        (OUT_DIR / "requests" / f"{branch['branch_id']}.json").write_text(
            json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = analyze(req)
        (OUT_DIR / "results" / f"{branch['branch_id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        final_results.append(result)

        # 롤링 타임라인 (6개월차부터)
        timeline = []
        first_fire = None
        for t in range(5, MONTHS):
            y, m = _month_at(t)
            as_of = _month_end(y, m)
            r = analyze(make_request(branch, generated, as_of))
            timeline.append({
                "as_of": as_of.isoformat(),
                "score": r["risk"]["score"],
                "grade": r["risk"]["grade"],
                "calculation_status": r["risk"]["calculation_status"],
                "market_risk": r["layers"]["market_risk"]["score"],
                "branch_risk": r["layers"]["branch_risk"]["score"],
                "review_sub_score": r["review_signal"].get("sub_score"),
                "should_fire": r["alert"]["should_fire"],
            })
            if first_fire is None and r["alert"]["should_fire"]:
                first_fire = as_of.isoformat()
        (OUT_DIR / "timeline" / f"{branch['branch_id']}.json").write_text(
            json.dumps({"branch_id": branch["branch_id"], "scenario": branch["scenario"],
                        "timeline": timeline}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        rk = result["risk"]
        summary_rows.append((
            branch["branch_id"], branch["scenario"], branch["gu_name"],
            branch["industry_label"], rk["score"], rk["grade"],
            rk["calculation_status"], result["review_signal"].get("status"),
            first_fire or "-",
        ))

    hq = summarize({
        "request_id": "demo-hq-001",
        "franchise_id": "demo-all",
        "as_of": final_as_of.isoformat(),
        "branch_results": final_results,
    })
    (OUT_DIR / "hq_summary.json").write_text(json.dumps(hq, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 데모 실행 요약",
        "",
        f"- 생성일 기준 as_of: {final_as_of.isoformat()}",
        f"- 가맹점: {len(summary_rows)}개 × 24개월 = {len(summary_rows) * MONTHS} 운영보고서",
        f"- 본사 집계: 위험 {hq['grade_distribution']['위험']} / 주의 {hq['grade_distribution']['주의']} / 정상 {hq['grade_distribution']['정상']}"
        f" (danger_ratio {hq['danger_ratio_pct']}%, avg_score {hq['average_score']})",
        "",
        "| branch | 시나리오 | 구 | 업종 | score | grade | status | 리뷰 | 첫 사이렌 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in summary_rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    lines += ["", "## 시나리오별 기대", ""]
    for name, s in SCENARIOS.items():
        lines.append(f"- **{name}**: {s.label} → 기대 {s.expect}")
    (OUT_DIR / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\nwrote artifacts under {OUT_DIR}")


if __name__ == "__main__":
    main()
