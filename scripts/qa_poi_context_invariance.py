"""완결 Kakao POI context가 추천 판정을 바꾸지 않는지 반복 검증한다.

POI는 지점 주변의 중립 관측일 뿐 FC, 수요·공실·성공 outcome, fit_tier 또는
정렬 근거가 아니다. 같은 요청을 context 없음/있음으로 각각 실행해 F24·F25의
불변 조건을 검증하고, 재현 가능한 JSON·Markdown 보고서를 남긴다.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import recommendation_pipeline as pipeline


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDUSTRIES = tuple(sorted(pipeline.SUPPORTED_INDUSTRIES))
EXPECTED_POI_METRICS = {
    "반경250m_관측_CE7_카페수",
    "반경250m_관측_FD6_음식점수",
    "반경500m_관측_CE7_카페수",
    "반경500m_관측_FD6_음식점수",
}
CHANGE_CODES = {"LL", "LH", "HL", "HH"}
REQUIRED_NORMALIZATIONS = {
    "kakao_rect_grid",
    "point_in_polygon_area_assign",
    "kakao_place_id_dedup",
    "poi_radius_count",
}


def change_code(candidate: dict[str, Any]) -> Any:
    return next(
        item.get("value")
        for item in candidate["evidence"]
        if item.get("metric_name") == "상권_변화_지표"
    )


def entry_label(candidate: dict[str, Any]) -> Any:
    return candidate["dimension_evidence"]["진입건전성"]["entry_health_v1"]["inputs"]["라벨"]


def compare_case(
    baseline: dict[str, Any], contextual: dict[str, Any],
    baseline_summary: dict[str, Any], contextual_summary: dict[str, Any],
) -> list[str]:
    """F24·F25 및 Evidence 출처·grain을 한 업종 입력에서 검사한다."""
    errors: list[str] = []
    expect = lambda condition, message: errors.append(message) if not condition else None

    expect(baseline_summary["schema_error_count"] == 0, "context 없음 실행의 RAG schema 오류")
    expect(contextual_summary["schema_error_count"] == 0, "context 있음 실행의 RAG schema 오류")
    context_info = contextual_summary.get("poi_context", {})
    expect(context_info.get("used") is True, "완결 POI context가 사용되지 않음")
    expect(set(context_info.get("categories", [])) == {"CE7", "FD6"}, "POI 카테고리 계약 불일치")
    expect(len(baseline) == len(contextual), "candidate 수가 context에 의해 변함")

    baseline_ids = [candidate["candidate_id"] for candidate in baseline]
    contextual_ids = [candidate["candidate_id"] for candidate in contextual]
    expect(baseline_ids == contextual_ids, "candidate ID 또는 정렬이 context에 의해 변함")
    baseline_by_id = {candidate["candidate_id"]: candidate for candidate in baseline}

    for candidate in contextual:
        candidate_id = candidate["candidate_id"]
        other = baseline_by_id.get(candidate_id)
        if other is None:
            errors.append(f"{candidate_id}: baseline 후보 없음")
            continue
        expect(candidate["fit_tier"] == other["fit_tier"], f"{candidate_id}: fit_tier 변경")
        expect(candidate["reasons"] == other["reasons"], f"{candidate_id}: 긍정 근거 변경")
        expect(candidate["counter_evidence"] == other["counter_evidence"], f"{candidate_id}: 반대 근거 변경")
        expect(change_code(candidate) == change_code(other), f"{candidate_id}: FC-11 코드 변경")
        expect(entry_label(candidate) == entry_label(other), f"{candidate_id}: entry_health FC-11 라벨 변경")
        expect(change_code(candidate) in CHANGE_CODES, f"{candidate_id}: FC-11 코드가 공식 LL/LH/HL/HH가 아님")

        coverage = candidate["feature_build"]["coverage"].get("poi_context", {})
        expect(coverage.get("matched") == 1 and coverage.get("expected") == 1, f"{candidate_id}: POI context coverage 불완전")
        poi_evidence = {
            item["metric_name"]: item
            for item in candidate["evidence"]
            if "관측_" in item.get("metric_name", "")
        }
        expect(set(poi_evidence) == EXPECTED_POI_METRICS, f"{candidate_id}: POI metric 집합 불일치")
        for metric, item in poi_evidence.items():
            expect(item.get("source_type") == "derived", f"{candidate_id}:{metric}: source_type 불일치")
            expect(item.get("spatial_grain") == "지점", f"{candidate_id}:{metric}: 지점 grain 아님")
            expect(item.get("grain_is_proxy") is False, f"{candidate_id}:{metric}: proxy로 잘못 표기")
            expect(REQUIRED_NORMALIZATIONS <= set(item.get("normalization", [])), f"{candidate_id}:{metric}: POI 정규화 누락")
            expect("fit_tier" in item.get("limitation", ""), f"{candidate_id}:{metric}: 등급 미사용 한계 누락")
    return errors


def write_report(report_dir: Path, payload: dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Kakao POI context 불변성 QA",
        "",
        f"- 생성: GPT(Codex), {payload['generated_at']}",
        f"- 요청: {payload['request']['sigungu']} {payload['request']['dong']} · {len(payload['cases'])}개 업종",
        f"- 결과: {'PASS' if payload['passed'] else 'FAIL'}",
        "- 불변 조건: 후보 ID·정렬, fit_tier, 긍정/반대 근거, FC-11 변화지표 코드, entry_health 라벨",
        "- POI 경계: complete context의 지점 grain 관측만 허용하며 FC·성공 outcome·등급·정렬 입력이 아니다.",
        "",
        "| 업종 | 후보 수 | 결과 | 오류 |",
        "| --- | ---: | --- | ---: |",
    ]
    for case in payload["cases"]:
        lines.append(f"| {case['industry_code']} | {case.get('candidate_count', 0)} | {'PASS' if not case['errors'] else 'FAIL'} | {len(case['errors'])} |")
    if payload["errors"]:
        lines.extend(["", "## 오류", ""])
        lines.extend(f"- {error}" for error in payload["errors"])
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Kakao POI context context 없음/있음 불변성 QA")
    parser.add_argument("--sido", default="서울특별시")
    parser.add_argument("--sigungu", default="송파구")
    parser.add_argument("--dong", default="잠실동")
    parser.add_argument("--industry-code", action="append", dest="industries", choices=sorted(pipeline.SUPPORTED_INDUSTRIES))
    parser.add_argument("--special-condition-text", default="월세 300만원 이하, 20평 이상, 주차 가능")
    parser.add_argument("--quarter", default=pipeline.DEFAULT_QUARTER)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "artifacts/evals/poi-context-invariance-2026-09-02")
    parser.add_argument("--run-root", type=Path, default=ROOT / "output/recommendation_runs/poi-context-invariance-2026-09-02")
    args = parser.parse_args()

    industries = args.industries or DEFAULT_INDUSTRIES
    cases: list[dict[str, Any]] = []
    all_errors: list[str] = []
    for industry_code in industries:
        request = pipeline.RecommendationRequest(
            args.sido, args.sigungu, args.dong, industry_code,
            args.special_condition_text, args.quarter,
        )
        case: dict[str, Any] = {"industry_code": industry_code, "errors": []}
        try:
            baseline = pipeline.run_pipeline(
                request, args.run_root / industry_code / "without-context", include_poi=True,
            )
            contextual = pipeline.run_pipeline(
                request, args.run_root / industry_code / "with-context", include_poi=True, include_poi_context=True,
            )
            case["candidate_count"] = len(contextual["candidates"])
            case["errors"] = compare_case(
                baseline["candidates"], contextual["candidates"],
                baseline["summary"], contextual["summary"],
            )
        except Exception as exc:  # QA는 한 업종 실패도 보고서에 남긴다.
            case["errors"].append(f"실행 예외: {type(exc).__name__}: {exc}")
        cases.append(case)
        all_errors.extend(f"{industry_code}: {error}" for error in case["errors"])

    payload = {
        "generated_by": "GPT(Codex)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not all_errors,
        "request": {
            "sido": args.sido, "sigungu": args.sigungu, "dong": args.dong,
            "quarter": args.quarter, "special_condition_text": args.special_condition_text,
        },
        "checks": ["F24 context 경계·등급/정렬/근거 불변", "F25 FC-11/entry_health 라벨 불변"],
        "cases": cases,
        "errors": all_errors,
    }
    write_report(args.report_dir, payload)
    print(f"{'PASS' if payload['passed'] else 'FAIL'}: {args.report_dir}")
    print(json.dumps({"industry_count": len(industries), "error_count": len(all_errors)}, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
