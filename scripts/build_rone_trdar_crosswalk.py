"""R-ONE 상권명과 서울시 상권분석서비스 상권의 검토 가능한 crosswalk 생성.

R-ONE 조사권역은 서울시 상권분석서비스 1,650개 상권과 경계·grain이
동일하지 않다. 따라서 문자열 유사도나 면적 순위만으로 강제 조인하지
않고, 사람이 검토 가능한 명시적 별칭/복합 지명 사전을 사용한다.

산출물:
  output/crosswalks/crosswalk_rone_trdar.csv
  output/crosswalks/crosswalk_rone_trdar_summary.json

매핑 상태:
  matched    : 직접명 또는 명시적 별칭으로 연결. 그래도 R-ONE 값은
               서울시 상권분석 상권의 실측값으로 바꾸어 표현하지 않는다.
  review     : 도로명·복합권역·역 주변 대표 상권을 고른 proxy. 운영 조인 전 검토.
  unresolved : 방어 가능한 서울시 상권명이 없어 연결하지 않음.

실행:
  .venv/bin/python3 scripts/build_rone_trdar_crosswalk.py
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from typing import Any

import shapefile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENT_PATH = os.path.join(ROOT, "data", "임대료", "R-ONE_임대동향_분기.csv")
TRDAR_PATH = os.path.join(
    ROOT, "data", "영역", "상권", "서울시 상권분석서비스(영역-상권).shp"
)
OUT_DIR = os.path.join(ROOT, "output", "crosswalks")
OUT_PATH = os.path.join(OUT_DIR, "crosswalk_rone_trdar.csv")
SUMMARY_PATH = os.path.join(OUT_DIR, "crosswalk_rone_trdar_summary.json")


def _target(name: str, role: str = "primary") -> tuple[str, str]:
    return name, role


# R-ONE 명칭 -> 서울시 상권분석서비스 명칭.
# 이 사전의 값은 자동 유사도 결과가 아니라 명시적으로 검토할 alias다.
# 여러 값은 R-ONE 복합권역을 구성하는 상권분석 상권 후보이며, 값을 쪼개
# 임대료가 각각의 상권 실측값인 것처럼 해석하지 않는다.
MAPPING: dict[str, dict[str, Any]] = {
    "가락시장": {"targets": [_target("가락시장")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "강남대로": {"targets": [_target("강남역")], "method": "road_proxy", "confidence": "medium", "note": "강남대로 도로권역의 대표 발달상권 proxy"},
    "건대입구": {"targets": [_target("건대입구역(건대)")], "method": "explicit_alias", "confidence": "high", "note": "R-ONE 권역명과 상권분석 발달상권 명칭의 역명 alias"},
    "경희대": {"targets": [_target("경희대학교(경희대)")], "method": "explicit_alias", "confidence": "high", "note": "대학명 alias"},
    "공덕역": {"targets": [_target("공덕역(공덕오거리)")], "method": "explicit_alias", "confidence": "high", "note": "역명·오거리 표기 차이"},
    "광화문": {"targets": [_target("광화문역")], "method": "explicit_alias", "confidence": "high", "note": "광화문 대표 역세권"},
    "교대역": {"targets": [_target("교대역(법원.검찰청)")], "method": "explicit_alias", "confidence": "high", "note": "역명·시설 표기 차이"},
    "구로디지털단지역": {"targets": [_target("구로디지털단지역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "구의역": {"targets": [_target("구의역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "군자": {"targets": [_target("군자역")], "method": "explicit_alias", "confidence": "high", "note": "R-ONE의 역세권 축약명"},
    "까치산역": {"targets": [_target("까치산역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "낙성대": {"targets": [_target("낙성대역 1번(관악구민 종합체육센터)"), _target("낙성대역 5번(관악중학교)", "component")], "method": "station_components", "confidence": "medium", "note": "낙성대 발달상권 명칭 부재로 역 출구 상권 2개를 후보로 보존"},
    "남대문": {"targets": [_target("남대문시장(자유상가)")], "method": "explicit_alias", "confidence": "high", "note": "남대문 대표 전통시장"},
    "남부터미널": {"targets": [_target("남부터미널역")], "method": "explicit_alias", "confidence": "high", "note": "역명 alias"},
    "노량진": {"targets": [_target("노량진역(노량진)")], "method": "explicit_alias", "confidence": "high", "note": "노량진 대표 발달상권"},
    "노원역": {"targets": [_target("노원역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "논현역": {"targets": [_target("논현역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "당산역": {"targets": [_target("당산역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "도산대로": {"targets": [_target("도산공원교차로")], "method": "road_proxy", "confidence": "medium", "note": "도산대로 도로권역의 대표 발달상권 proxy"},
    "독산/시흥": {"targets": [_target("독산동"), _target("시흥동 은행나무사거리", "component")], "method": "composite_components", "confidence": "medium", "note": "복합 지명은 독산·시흥 구성요소를 분리해 보존"},
    "동교/연남": {"targets": [_target("연남동(홍대)"), _target("동교초등학교", "component")], "method": "composite_components", "confidence": "medium", "note": "복합 지명은 연남·동교 구성요소를 분리해 보존"},
    "동대문": {"targets": [_target("동대문패션타운 관광특구")], "method": "explicit_alias", "confidence": "medium", "note": "동대문 광역권의 대표 관광특구 proxy"},
    "뚝섬": {"targets": [_target("뚝섬역")], "method": "explicit_alias", "confidence": "high", "note": "역명 alias"},
    "망원역": {"targets": [_target("망원역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "명동": {"targets": [_target("명동(명동거리)")], "method": "explicit_alias", "confidence": "high", "note": "명동 대표 발달상권"},
    "명일역": {"targets": [_target("명일역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "목동": {"targets": [_target("목동신시가지")], "method": "explicit_alias", "confidence": "medium", "note": "목동 대표 발달상권 proxy"},
    "미아사거리": {"targets": [_target("미아사거리")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "방배역/내방역": {"targets": [_target("방배역"), _target("내방역", "component")], "method": "composite_components", "confidence": "high", "note": "두 역명을 각각 보존"},
    "방산시장": {"targets": [_target("방산종합시장(방산시장)")], "method": "explicit_alias", "confidence": "high", "note": "시장명 alias"},
    "북촌": {"targets": [_target("북촌(안국역)")], "method": "explicit_alias", "confidence": "high", "note": "북촌 대표 발달상권"},
    "불광역": {"targets": [_target("불광역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "사당": {"targets": [_target("사당역(사당)")], "method": "explicit_alias", "confidence": "high", "note": "사당 대표 발달상권"},
    "상계역": {"targets": [_target("상계역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭(골목상권)"},
    "상봉역": {"targets": [_target("상봉역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "서래마을": {"targets": [_target("서래마을카페거리(서래마을)")], "method": "explicit_alias", "confidence": "high", "note": "상권분석 명칭의 카페거리 suffix"},
    "서울대입구역": {"targets": [_target("서울대입구역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "서촌": {"targets": [_target("서촌(경복궁역)")], "method": "explicit_alias", "confidence": "high", "note": "서촌 대표 발달상권"},
    "성신여대": {"targets": [_target("성신여대")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "수유": {"targets": [_target("수유역")], "method": "explicit_alias", "confidence": "high", "note": "R-ONE의 역세권 축약명"},
    "숙명여대": {"targets": [_target("숙대입구역(남영역, 남영동)")], "method": "explicit_alias", "confidence": "medium", "note": "숙명여대 주변의 상권분석 명칭은 숙대입구·남영역으로 표기"},
    "시청": {"targets": [_target("서울시청")], "method": "explicit_alias", "confidence": "high", "note": "시청 대표 발달상권"},
    "신림역": {"targets": [_target("신림역(신림)")], "method": "explicit_alias", "confidence": "high", "note": "신림 대표 발달상권"},
    "신사역": {"targets": [_target("신사역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "신촌/이대": {"targets": [_target("신촌역(신촌역, 신촌로터리)"), _target("이화여대(이대역, 이대)", "component")], "method": "composite_components", "confidence": "high", "note": "신촌·이대 발달상권을 각각 보존"},
    "쌍문역": {"targets": [_target("쌍문역동측상점가")], "method": "explicit_alias", "confidence": "medium", "note": "상권분석 명칭 중 역명을 직접 포함하는 대표 전통시장 proxy"},
    "압구정": {"targets": [_target("압구정역"), _target("압구정로데오역(압구정로데오)", "component")], "method": "composite_components", "confidence": "medium", "note": "압구정역·압구정로데오를 분리해 보존"},
    "약수역": {"targets": [_target("약수역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "양재말죽거리": {"targets": [_target("양재역")], "method": "road_proxy", "confidence": "medium", "note": "말죽거리의 대표 양재역 proxy; 양재역 R-ONE 권역과 target 재사용 검토 필요"},
    "양재역": {"targets": [_target("양재역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "여의도": {"targets": [_target("여의도역(여의도)")], "method": "explicit_alias", "confidence": "high", "note": "여의도 대표 발달상권"},
    "연신내": {"targets": [_target("연신내역")], "method": "explicit_alias", "confidence": "high", "note": "연신내 대표 발달상권"},
    "영등포역": {"targets": [_target("영등포역(영등포)")], "method": "explicit_alias", "confidence": "high", "note": "영등포 대표 발달상권"},
    "오류동역": {"targets": [_target("오류동역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "왕십리": {"targets": [_target("왕십리역(왕십리)")], "method": "explicit_alias", "confidence": "high", "note": "왕십리 대표 발달상권"},
    "용산역": {"targets": [_target("신용산역(용산역)")], "method": "explicit_alias", "confidence": "medium", "note": "상권분석 명칭은 신용산역(용산역)으로 표기"},
    "을지로": {"targets": [_target("을지로3가역"), _target("을지로입구역", "component")], "method": "composite_components", "confidence": "medium", "note": "을지로 광역권의 두 대표 역세권을 보존"},
    "이태원": {"targets": [_target("이태원(이태원역)"), _target("이태원 관광특구", "component")], "method": "composite_components", "confidence": "medium", "note": "역세권·관광특구를 분리해 보존"},
    "잠실/송파": {"targets": [_target("잠실 관광특구"), _target("송파나루역", "component")], "method": "composite_components", "confidence": "medium", "note": "잠실·송파 복합권역의 대표 상권 후보를 보존"},
    "잠실새내역": {"targets": [_target("잠실새내역(신천)")], "method": "explicit_alias", "confidence": "high", "note": "신천 표기 alias"},
    "장안동": {"targets": [_target("장안동사거리")], "method": "explicit_alias", "confidence": "medium", "note": "장안동 대표 발달상권 proxy"},
    "종로": {"targets": [_target("종로3가역"), _target("종로·청계 관광특구", "component")], "method": "composite_components", "confidence": "medium", "note": "종로 광역권의 역세권·관광특구를 보존"},
    "천호": {"targets": [_target("천호역")], "method": "explicit_alias", "confidence": "high", "note": "천호 대표 발달상권"},
    "청담": {"targets": [_target("청담사거리(청담동명품거리)")], "method": "explicit_alias", "confidence": "high", "note": "청담 대표 발달상권"},
    "청량리": {"targets": [_target("청량리역(청량리)")], "method": "explicit_alias", "confidence": "high", "note": "청량리 대표 발달상권"},
    "충무로": {"targets": [_target("충무로역")], "method": "explicit_alias", "confidence": "high", "note": "역명 alias"},
    "테헤란로": {"targets": [], "method": "unresolved", "confidence": "none", "note": "도로 corridor 경계와 1,650 상권의 직접 대응표가 없어 강남역 등으로 강제하지 않음"},
    "학동/강남구청역": {"targets": [_target("학동사거리"), _target("강남구청역", "component")], "method": "composite_components", "confidence": "medium", "note": "학동·강남구청역 복합권역 후보를 보존"},
    "한티역": {"targets": [_target("한티역")], "method": "exact_name", "confidence": "high", "note": "동일 명칭"},
    "혜화동": {"targets": [_target("대학로(혜화역)")], "method": "explicit_alias", "confidence": "high", "note": "혜화동 대표 발달상권"},
    "홍대/합정": {"targets": [_target("홍대입구역(홍대)"), _target("합정역", "component")], "method": "composite_components", "confidence": "high", "note": "홍대·합정 대표 발달상권을 각각 보존"},
    "화곡": {"targets": [_target("화곡역")], "method": "explicit_alias", "confidence": "high", "note": "화곡 대표 발달상권"},
}


def load_rone_areas() -> dict[str, str]:
    regions: dict[str, set[str]] = defaultdict(set)
    with open(RENT_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            area = row.get("R_ONE_상권", "").strip()
            if area:
                regions[area].add(row.get("권역", "").strip())
    bad = {name: values for name, values in regions.items() if len(values) != 1}
    if bad:
        raise ValueError(f"R-ONE 상권의 권역이 단일하지 않음: {bad}")
    return {name: next(iter(values)) for name, values in sorted(regions.items())}


def load_trdar() -> dict[str, dict[str, Any]]:
    sf = shapefile.Reader(TRDAR_PATH, encoding="utf-8")
    fields = [field[0] for field in sf.fields[1:]]
    by_name: dict[str, dict[str, Any]] = {}
    for sr in sf.iterShapeRecords():
        row = dict(zip(fields, sr.record))
        name = str(row["TRDAR_CD_N"]).strip()
        if name in by_name:
            raise ValueError(f"서울시 상권명이 중복됨: {name}")
        by_name[name] = {
            "TRDAR_CD": str(row["TRDAR_CD"]).strip(),
            "TRDAR_CD_N": name,
            "TRDAR_SE_NM": str(row["TRDAR_SE_1"]).strip(),
            "SIGNGU_CD": str(row["SIGNGU_CD"]).strip(),
            "SIGNGU_NM": str(row["SIGNGU_CD_"]).strip(),
            "ADSTRD_CD": str(row["ADSTRD_CD"]).strip(),
            "ADSTRD_NM": str(row["ADSTRD_CD_"]).strip(),
            "RELM_AR": float(row["RELM_AR"]),
        }
    if len(by_name) != 1650:
        raise ValueError(f"서울시 상권 수가 1650이 아님: {len(by_name)}")
    return by_name


def build_crosswalk(rone: dict[str, str], trdar: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rone_set = set(rone)
    mapping_set = set(MAPPING)
    missing_specs = sorted(rone_set - mapping_set)
    stale_specs = sorted(mapping_set - rone_set)
    if missing_specs or stale_specs:
        raise ValueError(f"매핑 사전과 R-ONE 영역 불일치: missing={missing_specs}, stale={stale_specs}")

    rows: list[dict[str, Any]] = []
    for rone_area, region in rone.items():
        spec = MAPPING[rone_area]
        targets = spec["targets"]
        status = "unresolved" if not targets else ("matched" if spec["confidence"] == "high" else "review")
        if not targets:
            rows.append({
                "R_ONE_상권": rone_area,
                "R_ONE_권역": region,
                "mapping_status": status,
                "mapping_method": spec["method"],
                "mapping_confidence": spec["confidence"],
                "mapping_role": "none",
                "TRDAR_CD": "",
                "TRDAR_CD_N": "",
                "TRDAR_SE_NM": "",
                "SIGNGU_CD": "",
                "SIGNGU_NM": "",
                "ADSTRD_CD": "",
                "ADSTRD_NM": "",
                "RELM_AR": "",
                "target_reuse_count": 0,
                "join_eligible": "no",
                "mapping_note": spec["note"],
            })
            continue
        for target_name, role in targets:
            if target_name not in trdar:
                raise ValueError(f"매핑 대상 상권명이 없음: {rone_area} -> {target_name}")
            target = trdar[target_name]
            rows.append({
                "R_ONE_상권": rone_area,
                "R_ONE_권역": region,
                "mapping_status": status,
                "mapping_method": spec["method"],
                "mapping_confidence": spec["confidence"],
                "mapping_role": role,
                **target,
                "target_reuse_count": 0,
                "join_eligible": "yes" if status == "matched" and role == "primary" else "review",
                "mapping_note": spec["note"],
            })

    target_users: Counter[str] = Counter(
        row["TRDAR_CD"] for row in rows if row["TRDAR_CD"]
    )
    for row in rows:
        if row["TRDAR_CD"]:
            row["target_reuse_count"] = target_users[row["TRDAR_CD"]]
            # 같은 서울시 상권이 여러 R-ONE 영역에 귀속되면 역방향 join 시
            # 어느 R-ONE 값을 써야 하는지 결정할 수 없으므로 사람 검토로 내린다.
            if row["target_reuse_count"] > 1 and row["join_eligible"] == "yes":
                row["join_eligible"] = "review"

    mapped_areas = {row["R_ONE_상권"] for row in rows if row["mapping_status"] != "unresolved"}
    unresolved = sorted(rone_set - mapped_areas)
    review_areas = sorted({row["R_ONE_상권"] for row in rows if row["mapping_status"] == "review"})
    high_areas = sorted({row["R_ONE_상권"] for row in rows if row["mapping_status"] == "matched"})
    join_areas = sorted({row["R_ONE_상권"] for row in rows if row["join_eligible"] == "yes"})
    reused_targets = sorted(code for code, count in target_users.items() if count > 1)
    summary = {
        "rone_area_count": len(rone),
        "mapping_row_count": len(rows),
        "mapped_area_count": len(mapped_areas),
        "unresolved_area_count": len(unresolved),
        "matched_high_confidence_area_count": len(high_areas),
        "join_eligible_area_count": len(join_areas),
        "review_area_count": len(review_areas),
        "unique_trdar_target_count": len(target_users),
        "target_reuse_count": len(reused_targets),
        "unresolved_areas": unresolved,
        "review_areas": review_areas,
        "reused_trdar_codes": reused_targets,
        "join_rule": "matched + primary + target_reuse_count=1만 자동 조인 후보. review·component·재사용·unresolved는 사람 검토/보류.",
        "grain_warning": "R-ONE 조사권역의 값은 서울시 상권분석 상권 실측값이 아니며, crosswalk는 proxy 귀속을 위한 연결 근거다.",
    }
    return rows, summary


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    fields = [
        "R_ONE_상권", "R_ONE_권역", "mapping_status", "mapping_method",
        "mapping_confidence", "mapping_role", "TRDAR_CD", "TRDAR_CD_N",
        "TRDAR_SE_NM", "SIGNGU_CD", "SIGNGU_NM", "ADSTRD_CD", "ADSTRD_NM",
        "RELM_AR", "target_reuse_count", "join_eligible", "mapping_note",
    ]
    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def main() -> int:
    rone = load_rone_areas()
    trdar = load_trdar()
    rows, summary = build_crosswalk(rone, trdar)
    write_outputs(rows, summary)
    print(f"saved: {OUT_PATH} ({len(rows)}행)")
    print(f"saved: {SUMMARY_PATH}")
    print(json.dumps({k: v for k, v in summary.items() if k.endswith("count")}, ensure_ascii=False))
    print(f"unresolved: {summary['unresolved_areas']}")
    print(f"review: {summary['review_areas']}")
    print(f"reused targets: {summary['reused_trdar_codes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
