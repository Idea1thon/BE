"""격자 합성 후보 seed + 공개 데이터 파생 접근성/경쟁 맥락 → project_generated 근거 레코드.

**개별 상가·매물 데이터가 아니다.** 주소·호실·면적·월세·주차·공실·매물 링크는 없다.
개별 매물 크롤링(불법·비확장) 대신, 선택 지역을 격자로 나눈 **합성 좌표**에
반경 대중교통·아파트·10개 외식 업종 인허가 수를 집계해 붙인 맥락 레이어다.
모든 레코드에 evidence_origin=project_generated, is_synthetic_anchor=true, listing_url=null을 명시한다.

지역별로 상업 필터(반경 내 영업 중 음식점 인허가 ≥ N) → 역·아파트 seed 80m dedup →
최원점 표본추출(farthest-point sampling)로 지리적으로 퍼진 약 K개를 남긴다.

산출 (지역 slug별):
  output/generated_evidence/<slug>/gridpoint_evidence.jsonl   (레코드 1줄씩, generated-evidence-schema.json)
  output/generated_evidence/<slug>/manifest.json
  output/generated_evidence/<slug>/seeds.json                 (recommendation_pipeline --include-generated-points 입력)
--all-seoul 이면 추가로:
  output/generated_evidence/_seoul_index.json                 (자치구별 slug·레코드 수)

재현:
  .venv/bin/python3 scripts/generate_gridpoint_evidence.py --all-seoul --per-region-limit 10
  .venv/bin/python3 scripts/generate_gridpoint_evidence.py --sido 서울특별시 --sigungu 송파구 --dong 잠실동
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import recommendation_pipeline as rp
from shapely.geometry import Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "artifacts/20-method/generated-evidence-schema.json"
GEN_VERSION = "gen-evidence-v1"

# 프로젝트 10개 외식 업종 — 반경 인허가 수를 업종별로 모두 기록한다(특정 업종 하드코딩 금지).
INDUSTRIES = {
    "CS100001": "한식", "CS100002": "중식", "CS100003": "일식", "CS100004": "양식",
    "CS100005": "제과점", "CS100006": "패스트푸드", "CS100007": "치킨", "CS100008": "분식",
    "CS100009": "호프-간이주점", "CS100010": "커피-음료",
}

LIMITATIONS = [
    "이 지점은 프로젝트가 격자로 생성한 좌표이며 실제 매물·점포가 아니다.",
    "POI·정류장·인허가 수는 공실 여부·임대료·매출·수요를 의미하지 않는다.",
    "이 근거만으로 창업 성공을 단정하지 않는다.",
]


def load_active_food_points() -> tuple[list[Point], list[str], STRtree]:
    """서울 전역 영업 중 음식점 인허가 좌표(EPSG:5181). (points, 업종코드, STRtree)."""
    path = rp.find_file(ROOT / "data/인허가", "음식점_인허가_서울")
    pts: list[Point] = []
    inds: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            handle = path.open(encoding=enc, newline="")
            reader = csv.DictReader(handle)
            for row in reader:
                if (row.get("폐업_분기") or "").strip() or "영업" not in (row.get("영업상태명") or ""):
                    continue
                try:
                    pts.append(Point(float(row["좌표X_5181"]), float(row["좌표Y_5181"])))
                except (TypeError, ValueError, KeyError):
                    continue
                inds.append(row.get("업종코드", ""))
            handle.close()
            break
        except UnicodeDecodeError:
            continue
    return pts, inds, STRtree(pts)


def near_indices(tree: STRtree, center: Point, radius: float) -> list[int]:
    return [int(i) for i in tree.query(center.buffer(radius))]


def farthest_point_sample(pts: list[Point], k: int) -> list[int]:
    """지리적으로 최대한 퍼진 k개 인덱스를 그리디로 고른다."""
    if len(pts) <= k:
        return list(range(len(pts)))
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    center = Point(cx, cy)
    chosen = [min(range(len(pts)), key=lambda i: pts[i].distance(center))]
    mind = [pts[i].distance(pts[chosen[0]]) for i in range(len(pts))]
    while len(chosen) < k:
        nxt = max(range(len(pts)), key=lambda i: mind[i] if i not in chosen else -1)
        chosen.append(nxt)
        for i in range(len(pts)):
            d = pts[i].distance(pts[nxt])
            if d < mind[i]:
                mind[i] = d
    return sorted(chosen)


def to_wgs84(x: float, y: float) -> dict[str, float] | None:
    try:
        from pyproj import Transformer
        lon, lat = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True).transform(x, y)
        return {"lon": round(lon, 6), "lat": round(lat, 6)}
    except Exception:  # noqa: BLE001
        return None


def generate_region(
    args: argparse.Namespace, sigungu: str, dong: str | None,
    layers: tuple, radius_points: tuple, food: tuple, poi_ctx: Any,
) -> dict[str, Any]:
    trdar_layer, _hinterland, dong_layer, sigungu_by_prefix = layers
    stations_all, buses_all, apts_all, radius_paths = radius_points
    food_pts, food_inds, food_tree = food

    # industry_code는 resolve_region에서 미사용 — 반경 인허가는 10개 업종 전부 집계한다.
    request = rp.RecommendationRequest(args.sido, sigungu, dong or None, "CS100001")
    selected, target_poly, target_buffer = rp.resolve_region(request, dong_layer, sigungu_by_prefix)

    minx, miny, maxx, maxy = target_buffer.bounds
    pad = max(args.radius_m, args.commercial_radius_m) + 50
    bx = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    in_bbox = lambda p: bx[0] <= p.x <= bx[2] and bx[1] <= p.y <= bx[3]  # noqa: E731
    stations = [(p, r) for p, r in stations_all if in_bbox(p)]
    buses = [(p, r) for p, r in buses_all if in_bbox(p)]
    apts = [(p, r) for p, r in apts_all if in_bbox(p)]

    # --- 격자 생성 ---
    step = args.grid_spacing_m
    raw: list[tuple[int, int, Point]] = []
    col = 0
    x = (int(minx) // step) * step
    while x <= maxx:
        row_i = 0
        y = (int(miny) // step) * step
        while y <= maxy:
            p = Point(x, y)
            if target_poly.covers(p):
                raw.append((col, row_i, p))
            y += step
            row_i += 1
        x += step
        col += 1
    stats = {"grid_raw": len(raw)}

    # --- 상업 활동 필터 (반경 내 영업 중 음식점 인허가 ≥ N) ---
    def food_near(p: Point, r: float) -> list[str]:
        return [food_inds[i] for i in near_indices(food_tree, p, r) if food_pts[i].distance(p) <= r]

    kept = [
        (c, ri, p) for c, ri, p in raw
        if args.commercial_min <= 0 or len(food_near(p, args.commercial_radius_m)) >= args.commercial_min
    ]
    stats["after_commercial_filter"] = len(kept)

    # --- dedup: 격자끼리 + 기존 seed(역·아파트) 근처 제거 ---
    seed_pts = [p for p, _ in stations] + [p for p, _ in apts]
    merged: list[tuple[int, int, Point]] = []
    for c, ri, p in kept:
        if any(p.distance(q) <= args.dedup_m for q in seed_pts):
            continue
        if any(p.distance(mp) <= args.dedup_m for _, _, mp in merged):
            continue
        merged.append((c, ri, p))
    stats["after_dedup"] = len(merged)

    # --- 지리적 표본추출로 약 K개 ---
    cap = args.per_region_limit if args.per_region_limit > 0 else (args.limit or 0)
    if cap and len(merged) > cap:
        pick = farthest_point_sample([p for _, _, p in merged], cap)
        merged = [merged[i] for i in pick]
    stats["final"] = len(merged)

    slug = rp.safe_slug(f"{sigungu}-{dong or '전체'}")
    inputs = sorted({
        rp.relative_path(radius_paths["transit"]), rp.relative_path(radius_paths["bus"]),
        rp.relative_path(radius_paths["apartment"]),
        rp.relative_path(rp.find_file(ROOT / "data/인허가", "음식점_인허가_서울")),
        rp.relative_path(rp.find_shape(ROOT / "data/영역/상권", "영역-상권")),
    } | ({rp.relative_path(poi_ctx.csv_path)} if poi_ctx else set()))
    gen_block = {
        "script": "scripts/generate_gridpoint_evidence.py", "version": GEN_VERSION,
        "generated_at": str(date.today()),
        "region_request": {"sido": args.sido, "sigungu": sigungu, "dong": dong},
        "inputs": inputs,
        "params": {
            "grid_spacing_m": step, "radius_m": args.radius_m, "bus_radius_m": args.bus_radius_m,
            "commercial_radius_m": args.commercial_radius_m, "commercial_min": args.commercial_min,
            "dedup_m": args.dedup_m, "per_region_limit": cap or None,
            "selection": "farthest_point_sampling" if cap else "all",
            "industries": list(INDUSTRIES),
            "industry_metric_scope": "반경 500m 영업 중 인허가 수를 10개 업종 전부 기록 (업종 필터 아님, 특정 업종 하드코딩 없음)",
            "commercial_filter_basis": "전체 음식점 인허가 활동 (상가·근생·소매·서비스 전체 커버리지 아님)",
        },
    }

    records: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    for seq, (col, row_i, p) in enumerate(merged, 1):
        host = host_rel = host_dist = None
        hits = trdar_layer.covering(p)
        if hits:
            host, host_rel, host_dist = hits[0][1], "포함", 0.0
        else:
            _, nearest, dist = trdar_layer.nearest(p)
            if dist <= rp.DEFAULT_HOST_MAX_M and str(nearest.raw.get("SIGNGU_CD_", "")).strip() == sigungu:
                host, host_rel, host_dist = nearest, "최근접", round(dist, 1)
        dong_hits = dong_layer.covering(p)
        dong_rec = dong_hits[0][1] if dong_hits else None

        near_st = rp.spatial_near(stations, p, args.radius_m)
        near_bs = rp.spatial_near(buses, p, args.bus_radius_m)
        near_ap = rp.spatial_near(apts, p, args.radius_m)
        hh = sum(rp.as_int(rp.num(r, "세대수")) or 0 for _, r in near_ap)
        food_r = food_near(p, args.radius_m)
        by_ind = Counter(food_r)

        metrics: dict[str, float | None] = {
            "subway_station_count_500m": len(near_st),
            "nearest_subway_station_m": near_st[0][0] if near_st else None,
            "bus_stop_count_250m": len(near_bs),
            "nearest_bus_stop_m": near_bs[0][0] if near_bs else None,
            "active_food_license_count_500m": len(food_r),
            "apartment_households_500m": hh,
        }
        for code in INDUSTRIES:
            metrics[f"active_{code}_license_count_500m"] = by_ind.get(code, 0)
        defs = {
            "subway_station_count_500m": "지점 반경 500m 내 도시철도역 수 (역사정보_서울.csv, EPSG:5181)",
            "nearest_subway_station_m": "최근접 도시철도역까지 직선거리(m)",
            "bus_stop_count_250m": "지점 반경 250m 내 버스정류소 수 (버스정류소_서울.csv)",
            "nearest_bus_stop_m": "최근접 버스정류소까지 직선거리(m)",
            "active_food_license_count_500m": "반경 500m 내 영업 중 음식점 인허가 수 전체 (음식점_인허가_서울.csv, 폐업_분기 없음 & 영업상태 '영업/정상'). 경쟁·상업 활성 대리이며 매출·성공 아님",
            "apartment_households_500m": "반경 500m 내 아파트 단지 세대수 합 (아파트단지_서울.csv, geocode 신뢰도 high/medium). K-apt 의무관리 위주 — 소형 주거 누락",
        }
        for code, name in INDUSTRIES.items():
            defs[f"active_{code}_license_count_500m"] = f"반경 500m 내 영업 중 {name}({code}) 인허가 수 — 해당 업종 경쟁 규모이며 매물 수·성공 아님"
        if poi_ctx:
            np500 = rp.spatial_near(poi_ctx.points, p, 500)
            metrics["food_poi_count_500m"] = sum(1 for _, r in np500 if r.get("category_group_code") == "FD6")
            metrics["cafe_poi_count_500m"] = sum(1 for _, r in np500 if r.get("category_group_code") == "CE7")
            defs["food_poi_count_500m"] = f"반경 500m 내 카카오 FD6 POI 수 ({rp.relative_path(poi_ctx.csv_path)} {poi_ctx.retrieved_at[:10]})"
            defs["cafe_poi_count_500m"] = "반경 500m 내 카카오 CE7 POI 수 (동일 스냅샷)"

        anchor_id = f"GEN-PT-{slug}-{seq:04d}"
        rec = {
            "evidence_id": f"EVD-GEN-{slug}-{seq:04d}-ACCESS",
            "evidence_origin": "project_generated",
            "evidence_type": "accessibility_context",
            "schema_version": GEN_VERSION,
            "target_anchor_id": anchor_id,
            "is_synthetic_anchor": True,
            "listing_url": None,
            "address_point": None,
            "anchor": {
                "type": "generated_point",
                "point": {"x": round(p.x, 2), "y": round(p.y, 2), "crs": "EPSG:5181"},
                "wgs84": to_wgs84(p.x, p.y),
                "generation_method": f"grid_{step}m",
                "grid_id": f"{col}_{row_i}",
                "sigungu": sigungu,
                "admin_dong": ({"code": dong_rec.code, "name": dong_rec.name} if dong_rec else None),
                "host_commercial_area": (
                    {"code": host.code, "name": host.name, "relation": host_rel, "distance_m": host_dist}
                    if host else None
                ),
            },
            "generator": gen_block,
            "claim": "이 합성 좌표는 공개 데이터 기준 대중교통·아파트·10개 외식 업종 인허가 밀도 맥락을 가진다. 개별 상가·매물·점포·수요·성공을 뜻하지 않는다.",
            "spatial_grain": "point_multi_radius",
            "generated_at": str(date.today()),
            "metrics": metrics,
            "metric_definitions": defs,
            "source_references": [
                "서울시 도시철도역사 정보 (국가철도공단)",
                "서울시 버스정류소 (서울 열린데이터광장)",
                "식품 인허가 (지방행정 인허가 데이터, LOCALDATA)",
                "서울시 상권분석서비스 영역 (서울시)",
            ] + (["카카오 로컬 POI"] if poi_ctx else []),
            "source_files": inputs,
            "limitations": list(LIMITATIONS),
        }
        records.append(rec)
        seeds.append({
            "anchor_id": anchor_id, "kind": "생성지점",
            "x": round(p.x, 2), "y": round(p.y, 2), "crs": "EPSG:5181",
            "grid_id": f"{col}_{row_i}",
            "host_code": host.code if host else None,
            "evidence_id": rec["evidence_id"],
        })

    out_dir = ROOT / "output/generated_evidence" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "gridpoint_evidence.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps({
        "generator": gen_block, "region_dongs": [r.name for r in selected],
        "bbox_5181": [round(v, 1) for v in target_buffer.bounds],
        "grid_stats": stats, "record_count": len(records),
        "schema": "artifacts/20-method/generated-evidence-schema.json",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "seeds.json").write_text(json.dumps({
        "generated_by": "scripts/generate_gridpoint_evidence.py", "version": GEN_VERSION,
        "region": {"sido": args.sido, "sigungu": sigungu, "dong": dong},
        "seed_count": len(seeds), "seeds": seeds,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    errors = validate(records)
    print(f"  {sigungu}{'/' + dong if dong else ''}: 격자 {stats['grid_raw']} → 상업 {stats['after_commercial_filter']} "
          f"→ dedup {stats['after_dedup']} → 최종 {len(records)}  (schema 오류 {len(errors)})")
    return {"slug": slug, "records": len(records), "errors": len(errors), "out_dir": str(out_dir.relative_to(ROOT)), "stats": stats}


def validate(records: list[dict[str, Any]]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema 미설치"]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v = Draft202012Validator(schema)
    errs: list[str] = []
    for rec in records:
        for err in v.iter_errors(rec):
            errs.append(f"{rec['target_anchor_id']}: {list(err.path)} {err.message}")
        if not rec["limitations"] or "생성" not in rec["limitations"][0] or "실제 매물" not in rec["limitations"][0]:
            errs.append(f"{rec['target_anchor_id']}: limitations[0]가 생성 좌표 고지가 아님")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sido", default="서울특별시")
    ap.add_argument("--sigungu", default="")
    ap.add_argument("--dong", default="")
    ap.add_argument("--all-seoul", action="store_true", help="서울 25개 자치구 전체를 각각 생성")
    ap.add_argument("--grid-spacing-m", type=int, default=100)
    ap.add_argument("--radius-m", type=float, default=500)
    ap.add_argument("--bus-radius-m", type=float, default=250)
    ap.add_argument("--commercial-radius-m", type=float, default=150)
    ap.add_argument("--commercial-min", type=int, default=3)
    ap.add_argument("--dedup-m", type=float, default=80)
    ap.add_argument("--per-region-limit", type=int, default=10, help="지역별 최원점 표본추출 상한(0=전부)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-poi-context", action="store_true")
    args = ap.parse_args()

    if not args.all_seoul and not args.sigungu:
        ap.error("--sigungu 또는 --all-seoul 중 하나가 필요합니다")

    print("데이터 로드 중...")
    layers = rp.load_layers()
    radius_points = rp.load_radius_points()
    food = load_active_food_points()
    print(f"  영업 중 음식점 인허가 {len(food[0])}건")

    if args.all_seoul:
        sigungus = sorted({v for v in layers[3].values() if v})
        print(f"서울 {len(sigungus)}개 자치구 · 지역별 ~{args.per_region_limit}개")
        index = []
        total_err = 0
        for sg in sigungus:
            poi_ctx = None
            r = generate_region(args, sg, None, layers, radius_points, food, poi_ctx)
            index.append({"sigungu": sg, **{k: r[k] for k in ("slug", "records", "errors", "out_dir")}})
            total_err += r["errors"]
        (ROOT / "output/generated_evidence/_seoul_index.json").write_text(json.dumps({
            "generated_at": str(date.today()), "version": GEN_VERSION,
            "per_region_limit": args.per_region_limit, "sigungu_count": len(sigungus),
            "total_records": sum(x["records"] for x in index), "total_schema_errors": total_err,
            "regions": index,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n총 {sum(x['records'] for x in index)}개 레코드 · schema 오류 {total_err}")
        print(f"인덱스: output/generated_evidence/_seoul_index.json")
        return 1 if total_err else 0

    poi_ctx = rp.load_completed_poi_context(
        rp.RecommendationRequest(args.sido, args.sigungu, args.dong or None, "CS100001")
    ) if args.include_poi_context else None
    r = generate_region(args, args.sigungu, args.dong or None, layers, radius_points, food, poi_ctx)
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
