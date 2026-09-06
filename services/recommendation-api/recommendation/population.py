"""인구 3종(상주·직장·외국인 생활인구) 로더 — 이슈 #28.

`candidate-selection-spec.md` §0-8·§0-9·§4-2 계약 구현.

- FC-03(상주 수준·구성)·FC-04(직장 수준·구성)·FC-05(활동유형지수)·FC-06a(외국인 거주)·
  FC-06b(외국인 방문)은 전부 `context_notes` 버킷이다. `reasons`·`counter_evidence`에
  넣지 않고 `fit_tier`·정렬 키·(FC-06a/b 제외) `data_confidence`에 반영하지 않는다.
- 상주·직장인구는 계단식(저빈도 갱신). 파일 분기코드가 20261이어도 실제 값이 마지막으로
  변한 분기(`RESIDENT_AS_OF`/`WORKER_AS_OF`)를 로드하고 `as_of_quarter`에 기록한다.
  분기 증감·모멘텀·추세 계산 금지(memory `stepwise_low_frequency_datasets`).
- 외국인생활인구는 행정동 grain만 존재 → 상권값은 `crosswalk_trdar_dong.csv` 면적가중
  대리 이식(`grain_is_proxy=true`). 20263은 202607 1개월치 부분분기라 제외한다.
"""

from __future__ import annotations

import csv
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- 계단식 스냅샷 분기 (data-usage-classification.md 부록 C.2, 2026-09-06 실측 확정) ---
# 상주: 행정동 425/425가 20234에서 마지막 변경, 이후 20241~20261 8분기 동결 복사.
# 직장: 20244에서 갱신, 이후 20251~20261 4분기 동결.
# 외국인: 계단식 아님(매분기 갱신). 최신 완전분기 = 20262. 20263은 부분분기.
RESIDENT_AS_OF = "20234"
WORKER_AS_OF = "20244"
FOREIGN_LATEST_COMPLETE = "20262"
FOREIGN_PARTIAL_EXCLUDED = "20263"

_RESIDENT_DIR = "data/상주인구"
_WORKER_DIR = "data/직장인구"
_FOREIGN_DIR = "data/외국인생활인구"
_CROSSWALK = "output/crosswalks/crosswalk_trdar_dong.csv"

_RESIDENT_FILES = {
    "상권": "서울시 상권분석서비스(상주인구-상권).csv",
    "행정동": "서울시 상권분석서비스(상주인구-행정동).csv",
}
_WORKER_FILES = {
    "상권": "서울시 상권분석서비스(직장인구-상권).csv",
    "행정동": "서울시 상권분석서비스(직장인구-행정동).csv",
}

_AGE_LABELS = {"10": "10대", "20": "20대", "30": "30대", "40": "40대", "50": "50대", "60_이상": "60대 이상"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"CSV 인코딩을 읽지 못함: {path}")


def _f(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    value = row.get(key)
    if value is None or str(value).strip() in ("", "-", "N/A"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pct(sorted_values: list[float], value: float | None) -> float | None:
    if value is None or not sorted_values:
        return None
    idx = bisect_left(sorted_values, value)
    return round(100 * idx / len(sorted_values), 1)


def _dominant_age(row: dict[str, str], stem: str) -> tuple[str, float] | None:
    """가장 큰 연령대 버킷과 그 비중(%)."""
    total = _f(row, f"총_{stem}_수")
    if not total:
        return None
    best_key, best_val = None, -1.0
    for suffix, label in _AGE_LABELS.items():
        val = _f(row, f"연령대_{suffix}_{stem}_수") or 0.0
        if val > best_val:
            best_key, best_val = label, val
    if best_key is None or best_val <= 0:
        return None
    return best_key, round(100 * best_val / total, 1)


def _dominant_gender(row: dict[str, str], stem: str) -> tuple[str, float] | None:
    total = _f(row, f"총_{stem}_수")
    male = _f(row, f"남성_{stem}_수")
    female = _f(row, f"여성_{stem}_수")
    if not total or male is None or female is None:
        return None
    if male >= female:
        return "남성", round(100 * male / total, 1)
    return "여성", round(100 * female / total, 1)


@dataclass
class PopulationData:
    """정규화된 인구 스냅샷. 원천 CSV를 후보에 직접 조인하지 않고 이 객체를 경유한다."""

    resident_trdar: dict[str, dict[str, str]]
    resident_dong: dict[str, dict[str, str]]
    worker_trdar: dict[str, dict[str, str]]
    worker_dong: dict[str, dict[str, str]]
    # 외국인: 행정동 원천 + 상권 crosswalk 대리
    foreign_dong: dict[str, dict[str, float | None]]
    foreign_trdar: dict[str, dict[str, float | None]]
    # 서울 분포(분위 계산용, 항상 sorted)
    seoul: dict[str, list[float]] = field(default_factory=dict)
    as_of: dict[str, str] = field(default_factory=dict)
    source_paths: dict[str, str] = field(default_factory=dict)
    # 커버리지·결측(감사 부록 C.1)
    coverage: dict[str, Any] = field(default_factory=dict)


def _load_stepwise(root: Path, folder: str, files: dict[str, str], quarter: str, stem: str
                   ) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = {"상권": {}, "행정동": {}}
    key_col = {"상권": "상권_코드", "행정동": "행정동_코드"}
    for grain, fname in files.items():
        path = root / folder / fname
        for row in _read_csv(path):
            if row.get("기준_년분기_코드") != quarter:
                continue
            code = str(row.get(key_col[grain], "")).strip()
            if not code:
                continue
            if code in out[grain]:
                raise RuntimeError(f"결합 키 중복: {folder} {grain} {code}")
            out[grain][code] = row
    return out["상권"], out["행정동"]


def _load_foreign(root: Path, quarter: str) -> dict[str, dict[str, float | None]]:
    """행정동 grain 외국인 생활인구 → FC-06a/06b 근사 비율 재료."""
    path = root / _FOREIGN_DIR / "외국인생활인구_행정동_분기.csv"
    out: dict[str, dict[str, float | None]] = {}
    for row in _read_csv(path):
        if row.get("기준_년분기_코드") != quarter:
            continue
        code = str(row.get("행정동_코드", "")).strip()
        if not code:
            continue
        out[code] = {
            "장기_외국인_평균": _f(row, "장기_외국인_평균"),
            "단기_외국인_평균": _f(row, "단기_외국인_평균"),
            "장기_관측일수": _f(row, "장기_관측일수"),
            "단기_관측일수": _f(row, "단기_관측일수"),
        }
    return out


def _load_crosswalk(root: Path) -> dict[str, list[tuple[str, float]]]:
    """상권_코드 → [(행정동_코드, ratio_of_trdar), ...] (면적가중치)."""
    path = root / _CROSSWALK
    out: dict[str, list[tuple[str, float]]] = {}
    for row in _read_csv(path):
        trdar = str(row.get("TRDAR_CD", "")).strip()
        dong = str(row.get("ADSTRD_CD", "")).strip()
        ratio = _f(row, "ratio_of_trdar")
        if not trdar or not dong or ratio is None:
            continue
        out.setdefault(trdar, []).append((dong, ratio))
    return out


_SOURCE_PATHS = {
    "resident": f"{_RESIDENT_DIR}/{_RESIDENT_FILES['상권']}",
    "resident_dong": f"{_RESIDENT_DIR}/{_RESIDENT_FILES['행정동']}",
    "worker": f"{_WORKER_DIR}/{_WORKER_FILES['상권']}",
    "worker_dong": f"{_WORKER_DIR}/{_WORKER_FILES['행정동']}",
    "foreign": f"{_FOREIGN_DIR}/외국인생활인구_행정동_분기.csv",
    "population_crosswalk": _CROSSWALK,
}


def assemble(
    *,
    resident_trdar: dict[str, dict[str, str]],
    resident_dong: dict[str, dict[str, str]],
    worker_trdar: dict[str, dict[str, str]],
    worker_dong: dict[str, dict[str, str]],
    foreign_dong_raw: dict[str, dict[str, float | None]],
    crosswalk: dict[str, list[tuple[str, float]]],
    flow_dong: dict[str, dict[str, str]] | None,
    as_of: dict[str, str],
    source_paths: dict[str, str] | None = None,
) -> PopulationData:
    """이미 파싱된 행 dict에서 PopulationData를 조립한다. 파일 소스와 DB 소스 공통 코어."""
    flow_dong = flow_dong or {}

    # FC-06a/06b: 행정동에서 근사 비율을 **먼저** 계산한다(분자=생활인구 통신신호, 분모=상주/유동 타 방법론).
    # 상권 대리는 이 동별 비율을 면적가중평균하는 것이므로 FC-06a·06b 모두 동일 규율(부록 C.3).
    foreign_dong: dict[str, dict[str, float | None]] = {}
    for code, fr in foreign_dong_raw.items():
        res_total = _f(resident_dong.get(code), "총_상주인구_수")
        long_foreign = fr["장기_외국인_평균"]
        short_foreign = fr["단기_외국인_평균"]
        flow_total = _f(flow_dong.get(code), "총_유동인구_수")
        flow_daily = (flow_total / 90.0) if flow_total else None
        ratio_a = (long_foreign / res_total) if long_foreign is not None and res_total else None
        ratio_b = (short_foreign / flow_daily) if short_foreign is not None and flow_daily else None
        s_l = (short_foreign / long_foreign) if short_foreign is not None and long_foreign else None
        foreign_dong[code] = {
            "장기_외국인_평균": long_foreign,
            "단기_외국인_평균": short_foreign,
            "ratio_a": ratio_a,
            "ratio_b": ratio_b,
            "short_long_ratio": s_l,
            "총_상주인구_수": res_total,
        }

    # 상권 대리: 동별 비율(ratio_a·ratio_b·단기/장기)을 crosswalk ratio_of_trdar로 면적가중평균(부록 C.3·C.4).
    foreign_trdar: dict[str, dict[str, float | None]] = {}
    for trdar, parts in crosswalk.items():
        acc: dict[str, list[float]] = {"ratio_a": [], "ratio_b": [], "short_long_ratio": []}
        wsum: dict[str, float] = {"ratio_a": 0.0, "ratio_b": 0.0, "short_long_ratio": 0.0}
        for dong, w in parts:
            fr = foreign_dong.get(dong)
            if not fr:
                continue
            for k in acc:
                if fr[k] is not None:
                    acc[k].append(fr[k] * w)
                    wsum[k] += w
        if any(acc.values()):
            foreign_trdar[trdar] = {
                k: (sum(acc[k]) / wsum[k]) if wsum[k] else None for k in acc
            } | {"grain_is_proxy": True}

    seoul = {
        "resident_total_trdar": sorted(v for r in resident_trdar.values() if (v := _f(r, "총_상주인구_수")) is not None),
        "resident_total_dong": sorted(v for r in resident_dong.values() if (v := _f(r, "총_상주인구_수")) is not None),
        "worker_total_trdar": sorted(v for r in worker_trdar.values() if (v := _f(r, "총_직장_인구_수")) is not None),
        "worker_total_dong": sorted(v for r in worker_dong.values() if (v := _f(r, "총_직장_인구_수")) is not None),
        "foreign_a_dong": sorted(v for r in foreign_dong.values() if (v := r["ratio_a"]) is not None),
        "foreign_b_dong": sorted(v for r in foreign_dong.values() if (v := r["ratio_b"]) is not None),
        "foreign_long_dong": sorted(v for r in foreign_dong.values() if (v := r["장기_외국인_평균"]) is not None),
    }

    return PopulationData(
        resident_trdar=resident_trdar, resident_dong=resident_dong,
        worker_trdar=worker_trdar, worker_dong=worker_dong,
        foreign_dong=foreign_dong, foreign_trdar=foreign_trdar,
        seoul=seoul,
        as_of=dict(as_of),
        source_paths=source_paths or dict(_SOURCE_PATHS),
        coverage={
            "resident_trdar": len(resident_trdar), "resident_dong": len(resident_dong),
            "worker_trdar": len(worker_trdar), "worker_dong": len(worker_dong),
            "foreign_dong": len(foreign_dong), "foreign_trdar": len(foreign_trdar),
        },
    )


def load_population(root: Path,
                    flow_dong: dict[str, dict[str, str]] | None = None,
                    resident_quarter: str = RESIDENT_AS_OF,
                    worker_quarter: str = WORKER_AS_OF,
                    foreign_quarter: str = FOREIGN_LATEST_COMPLETE) -> PopulationData:
    """파일 소스: 원천 CSV에서 인구 스냅샷을 읽는다.

    flow_dong: 길단위인구 행정동 인덱스(dong_code → row). FC-06b 분모(유동인구 일평균)용.
    """
    resident_trdar, resident_dong = _load_stepwise(root, _RESIDENT_DIR, _RESIDENT_FILES, resident_quarter, "상주인구")
    worker_trdar, worker_dong = _load_stepwise(root, _WORKER_DIR, _WORKER_FILES, worker_quarter, "직장_인구")
    foreign_dong_raw = _load_foreign(root, foreign_quarter)
    crosswalk = _load_crosswalk(root)
    return assemble(
        resident_trdar=resident_trdar, resident_dong=resident_dong,
        worker_trdar=worker_trdar, worker_dong=worker_dong,
        foreign_dong_raw=foreign_dong_raw, crosswalk=crosswalk, flow_dong=flow_dong,
        as_of={"resident": resident_quarter, "worker": worker_quarter, "foreign_resident": foreign_quarter},
    )


def load_crosswalk(root: Path) -> dict[str, list[tuple[str, float]]]:
    """DB 소스도 상권↔행정동 면적가중 crosswalk는 파일에서 읽는다(작은 파생 산출물)."""
    return _load_crosswalk(root)


@dataclass
class PopulationContext:
    context_notes: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    as_of: dict[str, str] = field(default_factory=dict)
    dimension_features: list[str] = field(default_factory=list)
    grain_notes: dict[str, str] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    normalizations: list[str] = field(default_factory=list)
    confidence_downgrade: bool = False
    confidence_reasons: list[str] = field(default_factory=list)


def _ev(feature_id: str, metric_name: str, value: Any, unit: str, *, spatial_grain: str,
        grain_is_proxy: bool, period: str, observed_end_period: str, update_cycle: str,
        source_path: str, interpretation: str, limitation: str,
        seoul_percentile: float | None = None, proxy_note: str | None = None,
        approximation_note: str | None = None, partial_period_excluded: str | None = None,
        normalization: list[str] | None = None) -> dict[str, Any]:
    """rag-evidence-schema.json evidence[] items 계약에 맞춘 인구 evidence 항목."""
    item: dict[str, Any] = {
        "metric_name": metric_name, "value": value, "unit": unit,
        "source_type": "derived", "comparison_scope": "seoul_quantile" if seoul_percentile is not None else "none",
        "period": period, "spatial_grain": spatial_grain, "grain_is_proxy": grain_is_proxy,
        "source_path": source_path, "quarter_file_source": source_path,
        "normalization": normalization or ["population_stepwise_snapshot_load"],
        "interpretation": interpretation, "limitation": limitation,
        "feature_id": feature_id, "observed_end_period": observed_end_period, "update_cycle": update_cycle,
    }
    if seoul_percentile is not None:
        item["seoul_percentile"] = seoul_percentile
    if grain_is_proxy:
        item["proxy_note"] = proxy_note or "행정동 값을 crosswalk_trdar_dong.csv ratio_of_trdar로 면적가중 대리"
    if feature_id in ("FC-06a", "FC-06b"):
        item["approximation_note"] = approximation_note or "생활인구(통신 신호)와 상주/유동인구는 방법론 불일치 — 정밀 비율 아님"
        item["partial_period_excluded"] = partial_period_excluded or FOREIGN_PARTIAL_EXCLUDED
    return item


def _snapshot_lag(as_of: str, latest: str) -> int:
    """as_of 스냅샷이 latest(요청) 분기보다 몇 분기 이전인지 (산술적 분기 간격).

    예: 20234 → 20261 = 9분기. 계단식이라 20261 파일에 있는 값도 실제로는 20234 관측치다.
    """
    def q(code: str) -> int:
        return int(code[:4]) * 4 + int(code[4]) - 1
    return max(0, q(latest) - q(as_of))


_POP_FC_MISSING = [
    ("FC-03", "상주인구"), ("FC-04", "직장인구"), ("FC-05", "활동유형지수"),
    ("FC-06a", "외국인 거주"), ("FC-06b", "외국인 방문"),
]


def context_for_candidate(
    pop: PopulationData | None,
    *,
    host_code: str | None,
    dong_code: str | None,
    dong_sigungu: str | None,
    target_sigungu: str,
    flow_dong_total: float | None,
    flow_dong_seoul: list[float],
    quarter: str = "20261",
    condition_flags: dict[str, bool] | None = None,
) -> PopulationContext:
    """후보 1건에 붙일 인구 근거(전부 context_notes 버킷).

    상주·직장은 상권 있으면 상권 원천, 없으면 행정동 원천. 외국인은 상권이면 crosswalk 대리.
    """
    ctx = PopulationContext()
    if pop is None:
        for fc, label in _POP_FC_MISSING:
            ctx.missing.append({"feature": fc, "reason": f"{label}(#28) 미연결 — DbSource 인구 테이블 미적재. --source files 사용 시 활성화"})
        return ctx
    ctx.as_of = dict(pop.as_of)
    ctx.normalizations = ["population_stepwise_snapshot_load", "foreign_over_resident_ratio", "short_foreign_over_flow_ratio", "foreign_trdar_area_weight"]
    condition_flags = condition_flags or {}
    res_lag = _snapshot_lag(pop.as_of["resident"], quarter)
    wrk_lag = _snapshot_lag(pop.as_of["worker"], quarter)

    # ---- FC-03 상주 ----
    if host_code and host_code in pop.resident_trdar:
        r_scope, r_row, r_seoul = "상권", pop.resident_trdar[host_code], pop.seoul["resident_total_trdar"]
    elif dong_code and dong_code in pop.resident_dong:
        r_scope, r_row, r_seoul = "행정동", pop.resident_dong[dong_code], pop.seoul["resident_total_dong"]
    else:
        r_scope = r_row = None
        r_seoul = []
    res_total = _f(r_row, "총_상주인구_수")
    if res_total is not None:
        p_seoul = _pct(r_seoul, res_total)
        age = _dominant_age(r_row, "상주인구")
        gender = _dominant_gender(r_row, "상주인구")
        comp = []
        if age:
            comp.append(f"{age[0]} {age[1]}%")
        if gender:
            comp.append(f"{gender[0]} {gender[1]}%")
        if condition_flags.get("housing"):
            hh = _f(r_row, "총_가구_수")
            apt = _f(r_row, "아파트_가구_수")
            if hh and apt is not None:
                comp.append(f"아파트가구 {round(100 * apt / hh, 1)}%")
        ctx.context_notes.append(
            f"상주인구 {r_scope} 배경 {int(res_total):,}명 (서울 {p_seoul}%ile) — "
            f"최신 유효 스냅샷 {pop.as_of['resident']} 기준, 분기코드는 {quarter}이나 실제 값은 {res_lag}분기 전 동결. "
            f"주요 구성: {', '.join(comp) or '구성비 결측'}. "
            f"계단식이라 증감·추세 계산 안 함. 검증상 폐업·생존과 무연관(|ρ|<0.2, 2026-09-03) — 판정·정렬 미반영 (FC-03)"
        )
        ctx.dimension_features.append("FC-03")
        ctx.grain_notes["FC-03"] = f"{r_scope} 상주인구 원천 직접 · context_notes 버킷(신호 없음, 판정·정렬·data_confidence 미반영)"
        ctx.evidence.append(_ev(
            "FC-03", "총_상주인구_수", int(res_total), "명",
            spatial_grain=r_scope, grain_is_proxy=False, period=quarter,
            observed_end_period=pop.as_of["resident"], update_cycle="stepwise",
            source_path=pop.source_paths["resident" if r_scope == "상권" else "resident_dong"],
            seoul_percentile=p_seoul,
            interpretation=f"{r_scope} 배경 상주인구 수준·구성(스냅샷 {pop.as_of['resident']}, {res_lag}분기 전)",
            limitation="계단식 스냅샷이라 분기 증감·추세 없음. 폐업/생존과 |ρ|<0.2로 무연관 — fit_tier 판정·정렬 미반영. 연령×성별 교차표는 카드 미표시",
        ))
        ctx.freshness["resident"] = {
            "observed_end_period": pop.as_of["resident"], "periods_behind_latest": res_lag,
            "update_cadence": "stepwise", "is_partial_latest": False,
        }
    elif r_scope is None:
        ctx.missing.append({"feature": "FC-03", "reason": "상주인구_원천_결측 — 해당 상권/행정동 파티션 없음(0 아님)"})

    # ---- FC-04 직장 ----
    if host_code and host_code in pop.worker_trdar:
        w_scope, w_row, w_seoul = "상권", pop.worker_trdar[host_code], pop.seoul["worker_total_trdar"]
    elif dong_code and dong_code in pop.worker_dong:
        w_scope, w_row, w_seoul = "행정동", pop.worker_dong[dong_code], pop.seoul["worker_total_dong"]
    else:
        w_scope = w_row = None
        w_seoul = []
    wrk_total = _f(w_row, "총_직장_인구_수")
    if wrk_total is not None:
        p_seoul = _pct(w_seoul, wrk_total)
        age = _dominant_age(w_row, "직장_인구")
        gender = _dominant_gender(w_row, "직장_인구")
        comp = []
        if age:
            comp.append(f"{age[0]} {age[1]}%")
        if gender:
            comp.append(f"{gender[0]} {gender[1]}%")
        ctx.context_notes.append(
            f"직장인구 {w_scope} 배경 {int(wrk_total):,}명 (서울 {p_seoul}%ile) — "
            f"최신 유효 스냅샷 {pop.as_of['worker']} 기준, 분기코드는 {quarter}이나 실제 값은 {wrk_lag}분기 전 동결. "
            f"주요 구성: {', '.join(comp) or '구성비 결측'}. 계단식이라 증감·추세 계산 안 함. "
            f"검증상 폐업·생존과 무연관 — 판정·정렬 미반영 (FC-04)"
        )
        ctx.dimension_features.append("FC-04")
        ctx.grain_notes["FC-04"] = f"{w_scope} 직장인구 원천 직접 · context_notes 버킷(신호 없음)"
        ctx.evidence.append(_ev(
            "FC-04", "총_직장_인구_수", int(wrk_total), "명",
            spatial_grain=w_scope, grain_is_proxy=False, period=quarter,
            observed_end_period=pop.as_of["worker"], update_cycle="stepwise",
            source_path=pop.source_paths["worker" if w_scope == "상권" else "worker_dong"],
            seoul_percentile=p_seoul,
            interpretation=f"{w_scope} 배경 직장인구 수준·구성(스냅샷 {pop.as_of['worker']}, {wrk_lag}분기 전)",
            limitation="계단식 스냅샷이라 분기 증감·추세 없음. 폐업/생존 무연관 — fit_tier 판정·정렬 미반영. 직장인구는 주거형태 컬럼 없음",
        ))
        ctx.freshness["worker"] = {
            "observed_end_period": pop.as_of["worker"], "periods_behind_latest": wrk_lag,
            "update_cadence": "stepwise", "is_partial_latest": False,
        }
    elif w_scope is None:
        ctx.missing.append({"feature": "FC-04", "reason": "직장인구_행정동_미제공 — 실제 0 아님(11개 동/2개 종료 동), 0 대체 금지"})

    # ---- FC-05 활동유형 (행정동 grain, 3모집단 원값 합산 금지) ----
    if dong_code:
        res_dong_total = _f(pop.resident_dong.get(dong_code), "총_상주인구_수")
        wrk_dong_total = _f(pop.worker_dong.get(dong_code), "총_직장_인구_수")
        f_z = _pct(flow_dong_seoul, flow_dong_total)
        r_z = _pct(pop.seoul["resident_total_dong"], res_dong_total)
        w_z = _pct(pop.seoul["worker_total_dong"], wrk_dong_total)
        levels = {"유동": f_z, "상주": r_z, "직장": w_z}
        present = {k: v for k, v in levels.items() if v is not None}
        if len(present) >= 2:
            dominant = max(present, key=present.get)
            ctx.context_notes.append(
                f"지역 활동유형 우세 성분 '{dominant}' — 유동/상주/직장 각각 서울 표준화 수준 "
                f"{f_z}/{r_z}/{w_z}%ile의 상대 비중(원값 합산 아님). 상주={pop.as_of['resident']}·직장={pop.as_of['worker']} 스냅샷. "
                f"유형 경계값 미확정(사람 승인 전) — 라벨 없이 우세 성분만. 고객층·영업시간 조건 대조용 배경, 판정·정렬 미반영 (FC-05)"
            )
            ctx.dimension_features.append("FC-05")
            ctx.grain_notes["FC-05"] = "행정동 grain · FC-01·03·04 파생(각 데이터 내부 서울 표준화 후 상대비중, 원값 합산 금지) · 차원 status 계산만 참여, positive/negative 금지"
            # 세 성분 중 가장 오래된 스냅샷(상주 20234)을 observed_end_period로 — 신선하게 표기하지 않음(F37).
            fc05_obs = min(pop.as_of["resident"], pop.as_of["worker"])
            ctx.evidence.append(_ev(
                "FC-05", "활동유형_우세성분", dominant, "표준화수준 서울 %ile",
                spatial_grain="행정동", grain_is_proxy=False, period=quarter,
                observed_end_period=fc05_obs, update_cycle="stepwise",
                source_path=pop.source_paths["resident_dong"],
                interpretation=f"유동/상주/직장 표준화 수준 {f_z}/{r_z}/{w_z}%ile의 상대 비중 — 우세 '{dominant}' (라벨 미확정)",
                limitation="3모집단 원값 합산 금지. 유형 경계값 사람 승인 필요 — 라벨 확정 안 함. 상주 20234·직장 20244 스냅샷 혼합. fit_tier 판정·정렬 미반영",
            ))

    # ---- FC-06a 외국인 거주 / FC-06b 외국인 방문 ----
    fr_scope = None
    if host_code and host_code in pop.foreign_trdar:
        fr_scope, fr_row = "상권(crosswalk 대리)", pop.foreign_trdar[host_code]
    elif dong_code and dong_code in pop.foreign_dong:
        fr_scope, fr_row = "행정동", pop.foreign_dong[dong_code]
    if fr_scope:
        proxy = fr_scope.startswith("상권")
        ratio_a = fr_row.get("ratio_a")
        if ratio_a is not None:
            top_a = round(100 - (_pct(pop.seoul["foreign_a_dong"], ratio_a) or 0), 1)
            ctx.context_notes.append(
                f"외국인 거주 근사비율 약 {round(ratio_a * 100, 1)}% (장기 외국인 생활인구 ÷ 주민등록 상주인구, 서울 상위 {top_a}%) — "
                f"분자=통신 신호 기반 생활인구(시간대 평균), 분모=주민등록 상주인구로 방법론 다름 → 정밀 '외국인/전체 비율'이 아닌 신호. "
                f"grain={fr_scope}. 판정·정렬 미반영 (FC-06a)"
            )
            ctx.dimension_features.append("FC-06a")
            ctx.grain_notes["FC-06a"] = f"{fr_scope} · 행정동에서 비율 계산" + (" → 상권 crosswalk 면적가중 대리(grain_is_proxy)" if proxy else "") + " · context_notes 버킷"
            ctx.evidence.append(_ev(
                "FC-06a", "외국인_거주_근사비율", round(ratio_a, 4), "비율",
                spatial_grain=fr_scope, grain_is_proxy=proxy, period=pop.as_of["foreign_resident"],
                observed_end_period=pop.as_of["foreign_resident"], update_cycle="quarterly",
                partial_period_excluded=FOREIGN_PARTIAL_EXCLUDED,
                approximation_note="분자=장기 외국인 생활인구(통신 신호 시간대 평균), 분모=주민등록 상주인구 — footprint·방법론 불일치, 정밀 비율 아님. 상권값은 동별 비율의 면적가중평균",
                source_path=pop.source_paths["foreign"],
                normalization=["foreign_over_resident_ratio", "foreign_trdar_area_weight"],
                interpretation="장기체류 외국인 거주 수준의 근사 비율(거주지 성격 신호)",
                limitation="근사 신호이며 정밀 외국인 비율 아님. fit_tier 판정·정렬 미반영. 임계값 도입 시 사람 승인 게이트",
            ))
        if proxy:
            ctx.confidence_downgrade = True
            ctx.confidence_reasons.append("FC-06a 외국인 거주 비율은 상권 crosswalk 면적가중 대리(grain_is_proxy) — data_confidence 1단계 하향")

        # FC-06b 방문: 동별 비율(단기 외국인 일평균 ÷ 유동인구 일평균)을 로더에서 미리 계산·면적가중.
        ratio_b = fr_row.get("ratio_b")
        s_l = fr_row.get("short_long_ratio")
        if ratio_b is not None:
            character = "관광지 성격" if (s_l is not None and s_l >= 1) else "거주지 성격"
            top_b = round(100 - (_pct(pop.seoul["foreign_b_dong"], ratio_b) or 0), 1)
            ctx.context_notes.append(
                f"외국인 방문 근사비율 약 {round(ratio_b * 100, 2)}% (단기 외국인 일평균 ÷ 유동인구 일평균, 서울 상위 {top_b}%)"
                + (f", 단기/장기 {round(s_l, 2)} → {character} 신호" if s_l is not None else "")
                + " — 분자=단기 생활인구(통신), 분모=길단위 유동인구(도로 통행)로 방법론 상이. "
                f"grain={fr_scope}. 판정·정렬 미반영 (FC-06b)"
            )
            ctx.dimension_features.append("FC-06b")
            ctx.grain_notes["FC-06b"] = f"{fr_scope} · 행정동에서 (단기외국인일평균÷유동일평균) 계산" + (" → 상권 crosswalk 면적가중 대리" if proxy else "") + " · 20263 부분분기 제외 · context_notes 버킷"
            ctx.evidence.append(_ev(
                "FC-06b", "외국인_방문_근사비율", round(ratio_b, 4), "비율",
                spatial_grain=fr_scope, grain_is_proxy=proxy, period=pop.as_of["foreign_resident"],
                observed_end_period=pop.as_of["foreign_resident"], update_cycle="quarterly",
                partial_period_excluded=FOREIGN_PARTIAL_EXCLUDED,
                approximation_note="분자=단기 외국인 생활인구 일평균(통신), 분모=길단위 유동인구 일평균(분기총÷90) — 체류·통행 방법론 상이. 상권값은 동별 비율의 면적가중평균",
                source_path=pop.source_paths["foreign"],
                normalization=["short_foreign_over_flow_ratio", "foreign_trdar_area_weight"],
                interpretation=f"단기체류 외국인 방문 수준의 근사 비율({character})",
                limitation="근사 신호. 20263 부분분기 제외(20262까지). fit_tier 판정·정렬 미반영. 임계값 도입 시 사람 승인 게이트",
            ))
            if proxy:
                ctx.confidence_reasons.append("FC-06b 외국인 방문 비율도 상권 crosswalk 면적가중 대리")

    if ctx.dimension_features:
        ctx.freshness.setdefault("foreign_resident", {
            "observed_end_period": pop.as_of["foreign_resident"], "periods_behind_latest": 0,
            "update_cadence": "quarterly", "is_partial_latest": False,
            "partial_period_excluded": FOREIGN_PARTIAL_EXCLUDED,
        })
    return ctx
