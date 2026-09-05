"""네이버 검색트렌드 계절성·이상치 분해 — FC-41·42의 '여름 급등' 아티팩트(F12) 대응.

배경 (실측 2026-09-02):
  - 10개 외식업 검색 관심도는 **완만한 계절성**(진폭 1.2~1.6x, 봄 3~4월 peak)만 있다.
  - 그런데 `_요약.csv`의 `최근6개월_기울기`는 raw `rel_index` 기반이라, 이상치가 창에 들어오면
    "폭발적 상승 추세"로 읽힌다. 실제로 **호프-간이주점 2026년 여름**이 그렇다:
    raw_ratio가 네이버 데이터랩 상한 100에 달해(202607) rel_index 4.9 → 109.8. 이건 계절성이 아니라
    단발 서치 급등 + 상한 캡 아티팩트다(전년 7월엔 없던 패턴).

방법 (scipy 없이):
  1. 중심 12개월 이동평균 대비 비율 → 월-of-year 계절지수 SI[moy] (median, 평균=1 정규화; 이상치에 강함)
  2. 탈계절 시계열 = rel_index / SI[moy]
  3. 이상치 탐지: rel_index > 3× 직전 12개월 중앙값  OR  raw_ratio >= 95 (데이터랩 상한 근처)
  4. robust 추세 = 탈계절 + 이상치 월 제외 후 6/12개월 기울기
  5. YoY = rel_index[m] / rel_index[m-12] (동월 비교라 계절 상쇄, 단 이상치 월은 신뢰 불가)

산출:
  output/feature_validation/naver_seasonality.csv          (key별 추세·이상치 지표)
  output/feature_validation/naver_seasonal_factors.csv     (key × moy 계절지수)
  output/feature_validation/naver_seasonality_summary.json
"""
from __future__ import annotations
import csv, json, os, statistics, unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output/feature_validation"

FILES = [
    ("업종", "업종_검색트렌드_월.csv", "업종명", None),
    ("자치구", "자치구_검색트렌드_월.csv", "자치구", None),
    ("행정동", "행정동_검색트렌드_월.csv", "행정동명", "검색키워드"),
]
ANOM_RATIO = 2.5     # rel_index > 2.5× 직전 12개월 중앙값 → 이상치
ANOM_RAW_CEIL = 90   # raw_ratio >= 90 → 데이터랩 상한 캡 아티팩트


def realpath(name: str) -> str:
    for dp, _, fs in os.walk(ROOT / "data/네이버트렌드"):
        for fn in fs:
            full = os.path.join(dp, fn)
            if unicodedata.normalize("NFC", full).endswith(name):
                return full
    raise FileNotFoundError(name)


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + (int(b[4:6]) - int(a[4:6]))


def ols_slope_pct(vals: list[float]) -> float | None:
    """마지막 n개월의 선형 기울기를 평균 대비 %/월로."""
    v = [x for x in vals if x is not None]
    if len(v) < 3:
        return None
    n = len(v)
    xs = list(range(n))
    mx = (n - 1) / 2
    my = sum(v) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0 or my == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, v)) / denom
    return round(slope / my * 100, 2)


def seasonal_index(series: list[tuple[str, float | None]]) -> dict[int, float]:
    """중심 12개월 이동평균 대비 비율의 월별 중앙값 → 평균=1 정규화."""
    vals = [v for _, v in series]
    ratios: dict[int, list[float]] = defaultdict(list)
    for i, (ym, v) in enumerate(series):
        lo, hi = i - 6, i + 6
        if lo < 0 or hi >= len(vals) or v is None:
            continue
        window = [x for x in vals[lo:hi + 1] if x is not None]
        if len(window) < 10:
            continue
        ma = sum(window) / len(window)
        if ma > 0:
            ratios[int(ym[4:6])].append(v / ma)
    si = {m: statistics.median(r) for m, r in ratios.items() if r}
    if len(si) < 6:
        return {}
    mean_si = sum(si.values()) / len(si)
    if mean_si == 0:
        return {}
    si = {m: round(v / mean_si, 4) for m, v in si.items()}
    for m in range(1, 13):
        si.setdefault(m, 1.0)
    return si


def analyze():
    rows_out = []
    factors_out = []
    for grain, fname, keycol, kw in FILES:
        f = realpath(fname)
        by_rel: dict[str, dict[str, float | None]] = defaultdict(dict)
        by_raw: dict[str, dict[str, float | None]] = defaultdict(dict)
        names: dict[str, str] = {}
        for r in csv.DictReader(open(f, encoding="utf-8-sig")):
            key = r[keycol] + ("|" + r[kw] if kw else "")
            names[key] = r[keycol]
            rel = (r.get("rel_index") or "").strip()
            raw = (r.get("raw_ratio") or "").strip()
            by_rel[key][r["기준_년월"]] = float(rel) if rel else None
            by_raw[key][r["기준_년월"]] = float(raw) if raw else None

        for key, m2v in by_rel.items():
            yms = sorted(m2v)
            series = [(ym, m2v[ym]) for ym in yms]
            si = seasonal_index(series)
            if not si:
                continue
            deseason = {ym: (v / si[int(ym[4:6])] if v is not None else None) for ym, v in series}

            # 이상치 탐지: rel > 3× 직전 12개월 중앙값  또는  raw_ratio >= 95
            anomalies = []
            for i, ym in enumerate(yms):
                v = m2v[ym]
                if v is None:
                    continue
                prior = [m2v[y] for y in yms[max(0, i - 12):i] if m2v[y] is not None]
                hi = v > ANOM_RATIO * statistics.median(prior) if len(prior) >= 6 else False
                ceil = (by_raw[key].get(ym) or 0) >= ANOM_RAW_CEIL
                if hi or ceil:
                    anomalies.append(ym)
            anom_set = set(anomalies)

            latest_ym = yms[-1]
            latest = m2v[latest_ym]

            def yoy_at(ym):
                prev = f"{int(ym[:4]) - 1}{ym[4:6]}"
                a, b = m2v.get(ym), m2v.get(prev)
                return (a / b) if (a and b) else None
            yoy_series = [yoy_at(ym) for ym in yms[-6:]]
            yoy_clean = [x for ym, x in zip(yms[-6:], yoy_series) if x is not None and ym not in anom_set]

            raw_last6 = [m2v[ym] for ym in yms[-6:]]
            des_last6 = [deseason[ym] for ym in yms[-6:]]
            des_last12 = [deseason[ym] for ym in yms[-12:]]
            robust6 = [deseason[ym] for ym in yms[-6:] if ym not in anom_set]
            robust12 = [deseason[ym] for ym in yms[-12:] if ym not in anom_set]

            amp = round(max(si.values()) / min(si.values()), 2) if min(si.values()) > 0 else None
            raw_s6 = ols_slope_pct(raw_last6)
            anom_recent = [ym for ym in anomalies if ym >= yms[-6]]
            # surge 진행 중: 최근 3개월 내 이상치 OR 최신값이 직전 12개월 중앙값의 2배 초과
            prior12 = [m2v[y] for y in yms[-13:-1] if m2v[y] is not None]
            surge_active = bool([ym for ym in anomalies if ym >= yms[-3]]) or (
                latest is not None and len(prior12) >= 6 and latest > 2 * statistics.median(prior12))
            rob_s6 = None if surge_active else ols_slope_pct(robust6)
            misleading = bool(anom_recent) or surge_active or (
                raw_s6 is not None and rob_s6 is not None
                and (raw_s6 * rob_s6 < 0 or abs(raw_s6 - rob_s6) >= 15)
            )

            rows_out.append({
                "grain": grain, "key": names[key],
                "keyword": key.split("|", 1)[1] if "|" in key else "",
                "months": len([v for v in m2v.values() if v is not None]),
                "latest_ym": latest_ym,
                "latest_rel_index": round(latest, 3) if latest else None,
                "seasonal_amp": amp, "peak_month": max(si, key=si.get), "trough_month": min(si, key=si.get),
                "anomaly_months": ";".join(anomalies),
                "anomaly_in_last6": ";".join(anom_recent),
                "surge_active": surge_active,
                "latest_yoy": round(yoy_series[-1], 3) if yoy_series[-1] else None,
                "yoy_clean_recent_mean": round(statistics.mean(yoy_clean), 3) if yoy_clean else None,
                "raw_slope6_pct_per_month": raw_s6,
                "deseason_slope6_pct_per_month": ols_slope_pct(des_last6),
                "robust_slope6_pct_per_month": rob_s6,
                "robust_slope12_pct_per_month": None if surge_active else ols_slope_pct(robust12),
                "misleading_raw_trend": misleading,
            })
            for m in range(1, 13):
                factors_out.append({"grain": grain, "key": names[key],
                                    "keyword": key.split("|", 1)[1] if "|" in key else "",
                                    "month": m, "seasonal_index": si[m]})

    return rows_out, factors_out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, factors = analyze()

    with open(OUT / "naver_seasonality.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(OUT / "naver_seasonal_factors.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(factors[0].keys()))
        w.writeheader(); w.writerows(factors)

    ind = [r for r in rows if r["grain"] == "업종"]
    misleading = [r for r in rows if r["misleading_raw_trend"]]
    anom_keys = [r for r in rows if r["anomaly_months"]]
    summary = {
        "latest_ym": max(r["latest_ym"] for r in rows),
        "keys": {"업종": sum(1 for r in rows if r["grain"] == "업종"),
                 "자치구": sum(1 for r in rows if r["grain"] == "자치구"),
                 "행정동": sum(1 for r in rows if r["grain"] == "행정동")},
        "misleading_raw_trend_count": len(misleading),
        "keys_with_anomaly": len(anom_keys),
        "keys_with_anomaly_in_last6": sum(1 for r in rows if r["anomaly_in_last6"]),
        "keys_surge_active": sum(1 for r in rows if r["surge_active"]),
        "surge_active_by_grain": {g: sum(1 for r in rows if r["surge_active"] and r["grain"] == g) for g in ("업종", "자치구", "행정동")},
        "anomaly_examples": sorted(
            ({"grain": r["grain"], "key": r["key"], "keyword": r["keyword"],
              "anomaly_months": r["anomaly_months"], "latest_rel": r["latest_rel_index"],
              "raw_slope6": r["raw_slope6_pct_per_month"], "robust_slope6": r["robust_slope6_pct_per_month"]}
             for r in anom_keys if r["anomaly_in_last6"]),
            key=lambda x: -(x["raw_slope6"] or 0))[:12],
        "industry_seasonality": sorted(
            ({"업종": r["key"], "seasonal_amp": r["seasonal_amp"], "peak_month": r["peak_month"],
              "trough_month": r["trough_month"], "anomaly_months": r["anomaly_months"],
              "latest_yoy": r["latest_yoy"], "yoy_clean_recent_mean": r["yoy_clean_recent_mean"],
              "raw_slope6": r["raw_slope6_pct_per_month"], "robust_slope6": r["robust_slope6_pct_per_month"]}
             for r in ind), key=lambda x: -(x["seasonal_amp"] or 0)),
    }
    (OUT / "naver_seasonality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 네이버 검색트렌드 계절성·이상치 분해 ===")
    print(f"key: 업종 {summary['keys']['업종']} · 자치구 {summary['keys']['자치구']} · 행정동 {summary['keys']['행정동']} (최신 {summary['latest_ym']})")
    print(f"이상치 보유 key {summary['keys_with_anomaly']} (최근6개월 {summary['keys_with_anomaly_in_last6']}) · "
          f"surge 진행중 {summary['keys_surge_active']} {summary['surge_active_by_grain']} · raw 추세 오해 {len(misleading)}\n")
    print("-- 업종별 계절 진폭 · 추세 (robust6=None이면 surge로 추세 판단 불가) --")
    for r in summary["industry_seasonality"]:
        print(f"  {r['업종']:14s} 진폭 {str(r['seasonal_amp']):>5s}x peak {r['peak_month']:2d}월  "
              f"raw6 {str(r['raw_slope6']):>8s}%/월  robust6 {str(r['robust_slope6']):>7s}%/월  "
              f"YoY(clean) {r['yoy_clean_recent_mean']}  이상치[{r['anomaly_months']}]")
    print(f"\n산출: {OUT}/naver_seasonality*.csv|json")

    try:
        plot(rows, factors)
    except Exception as e:  # noqa: BLE001
        print(f"(plot 생략: {e})")


def plot(rows, factors):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False

    ind_factors: dict[str, list[float]] = defaultdict(lambda: [1.0] * 12)
    for r in factors:
        if r["grain"] == "업종":
            ind_factors[r["key"]][r["month"] - 1] = r["seasonal_index"]

    # 호프 원계열
    f = realpath("업종_검색트렌드_월.csv")
    hof = sorted((r["기준_년월"], float(r["rel_index"]))
                 for r in csv.DictReader(open(f, encoding="utf-8-sig")) if "호프" in r["업종명"] and r["rel_index"])

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.5))
    months = list(range(1, 13))
    for name, si in sorted(ind_factors.items(), key=lambda kv: -(max(kv[1]) - min(kv[1]))):
        a1.plot(months, si, marker="o", ms=3, lw=1.4, label=name)
    a1.axhline(1.0, color="#888", ls="--", lw=1)
    a1.set_xticks(months); a1.set_xlabel("월"); a1.set_ylabel("계절지수 (연평균=1)")
    a1.set_title("업종별 계절성 — 봄(3~4월) peak · 여름(7~8월) trough 공통\n(raw 6개월 기울기가 계절만 반영해 '하락'으로 오독)")
    a1.legend(fontsize=7, ncol=2)

    xs = list(range(len(hof)))
    a2.plot(xs, [v for _, v in hof], lw=1.5, color="#555")
    for i, (ym, v) in enumerate(hof):
        if ym in ("202605", "202607", "202608"):
            a2.plot(i, v, "o", color="crimson", ms=6)
    a2.set_title("호프-간이주점 원계열 — 2026 여름 급등은 계절성 아님\n(raw_ratio 데이터랩 상한 100 도달, 이전 7월엔 없던 패턴)")
    tick = [i for i in xs if hof[i][0][4:6] == "01"]
    a2.set_xticks(tick); a2.set_xticklabels([hof[i][0][:4] for i in tick])
    a2.set_ylabel("rel_index"); a2.set_xlabel("연")
    fig.tight_layout()
    d = ROOT / "output/figures/feature_validation"
    d.mkdir(parents=True, exist_ok=True)
    fig.savefig(d / "naver_seasonality.png", dpi=120)
    print(f"그림: {d}/naver_seasonality.png")


if __name__ == "__main__":
    main()
