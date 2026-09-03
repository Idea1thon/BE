"""entry_health_v1 등급 컷·가중치 설계 근거 분석.

FC-10 = **지역(상권/행정동) 배경 진입 환경 등급**. 업종 구분 없음 —
상권변화지표 라벨이 전 업종 통합으로 계산되므로(CLAUDE.md), 점포 성분도 전 업종 통합.
업종별 개·폐업 신호는 FC-11(변화라벨)·FC-12(폐업추세)·FC-30(경쟁)이 별도로 담당.

입력
  data/점포/2026년 20261(현재), data/점포/2025년 20254(전년동분기) — 전 업종
  data/상권변화지표/ 상권·행정동 20261
grain: 상권(1,650)·행정동(≈425). 각 단위 1행.

산출
  output/entry_health/units_{상권,행정동}.csv          단위별 성분·분위·합성위험·등급
  output/entry_health/grade_by_label_{상권,행정동}.csv  등급 × 라벨 교차표
  output/entry_health/weight_sensitivity_{상권,행정동}.csv
  output/entry_health/cut_candidates.json
"""
import csv
import os
import json
import bisect
import statistics
from collections import defaultdict, Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "entry_health")
os.makedirs(OUT, exist_ok=True)

Q_NOW, Q_YOY = "20261", "20254"

# CLAUDE.md 4분류 해석 → 신규 진입 리스크 (프로파일 §7)
LABEL_RISK = {"LH": 0.0, "HH": 0.5, "LL": 0.6, "HL": 1.0}
LABEL_NAME = {"LH": "상권확장", "HH": "정체", "LL": "다이나믹", "HL": "상권축소"}

# 권고 가중치: 폐업률 / 개업률(역) / 점포증감률(역) / 라벨리스크
W0 = {"close": 0.25, "open": 0.25, "delta": 0.25, "label": 0.25}

_EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드", "trdar_cd": "상권_코드", "adstrd_cd": "행정동_코드",
    "svc_induty_cd": "서비스_업종_코드", "stor_co": "전체_점포_수",
    "opbiz_stor_co": "개업_점포_수", "clsbiz_stor_co": "폐업_점포_수", "점포_수": "전체_점포_수",
}


def rd(path):
    with open(os.path.join(ROOT, path), encoding="cp949", newline="") as f:
        rows = list(csv.DictReader(f))
    if rows and ("stdr_yyqu_cd" in rows[0] or "점포_수" in rows[0]):
        rows = [{_EN2KO.get(k, k): v for k, v in r.items()} for r in rows]
    return rows


def inum(row, key):
    try:
        return int(float(row.get(key, "") or 0))
    except ValueError:
        return 0


def pctile_fn(values):
    sv = sorted(values)
    n = len(sv)

    def p(x):
        if n == 0 or x is None:
            return None
        lo = bisect.bisect_left(sv, x)
        hi = bisect.bisect_right(sv, x)
        return 100.0 * (lo + hi) / 2.0 / n

    return p


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    frac = idx - lo
    if lo + 1 < len(sorted_vals):
        return sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac
    return sorted_vals[lo]


GRADES = ["양호", "보통", "주의", "경계"]


def grade_by_cuts(risk, cuts):
    if risk is None:
        return "정보없음"
    for i, cut in enumerate(cuts):
        if risk < cut:
            return GRADES[i]
    return GRADES[-1]


def build_scope(scope):
    """전 업종 통합 상권/행정동 단위 리스트."""
    code_col = "상권_코드" if scope == "상권" else "행정동_코드"
    name_col = "상권_코드_명" if scope == "상권" else "행정동_코드_명"
    now = rd(f"data/점포/2026년/서울시 상권분석서비스(점포-{scope}).csv")
    yoy = rd(f"data/점포/2025년/서울시 상권분석서비스(점포-{scope})_2025년.csv")
    chg = rd(f"data/상권변화지표/서울시 상권분석서비스(상권변화지표-{scope}).csv")
    label = {r[code_col]: r["상권_변화_지표"] for r in chg if r["기준_년분기_코드"] == Q_NOW}

    agg = defaultdict(lambda: {"n": 0, "op": 0, "cl": 0, "name": ""})
    for r in now:
        if r["기준_년분기_코드"] != Q_NOW:
            continue
        a = agg[r[code_col]]
        a["n"] += inum(r, "전체_점포_수")
        a["op"] += inum(r, "개업_점포_수")
        a["cl"] += inum(r, "폐업_점포_수")
        a["name"] = r[name_col]
    yoy_n = defaultdict(int)
    for r in yoy:
        if r["기준_년분기_코드"] == Q_YOY:
            yoy_n[r[code_col]] += inum(r, "전체_점포_수")

    units = []
    for code, a in agg.items():
        if a["n"] == 0:
            continue
        n0 = yoy_n.get(code, 0)
        units.append({
            "scope": scope, "code": code, "name": a["name"],
            "n_now": a["n"], "n_yoy": n0 or None,
            "open_r": 100.0 * a["op"] / a["n"],
            "close_r": 100.0 * a["cl"] / a["n"],
            "delta": ((a["n"] - n0) / n0) if n0 else None,
            "label": label.get(code), "label_risk": LABEL_RISK.get(label.get(code)),
        })
    return units


def add_percentiles(units):
    fc = pctile_fn([u["close_r"] for u in units])
    fo = pctile_fn([u["open_r"] for u in units])
    fd = pctile_fn([u["delta"] for u in units if u["delta"] is not None])
    for u in units:
        u["p_close"] = fc(u["close_r"])
        u["p_open"] = fo(u["open_r"])
        u["p_delta"] = fd(u["delta"]) if u["delta"] is not None else None


def composite(u, w, label_risk_default=0.5):
    lr = u["label_risk"] if u["label_risk"] is not None else label_risk_default
    parts = [(w["close"], u["p_close"]), (w["open"], 100 - u["p_open"])]
    if u["p_delta"] is not None:
        parts.append((w["delta"], 100 - u["p_delta"]))
    parts.append((w["label"], lr * 100))
    tw = sum(p[0] for p in parts)
    return sum(p[0] * p[1] for p in parts) / tw if tw else None


def main():
    report = {}
    for scope in ("상권", "행정동"):
        units = build_scope(scope)
        add_percentiles(units)
        for u in units:
            u["risk0"] = composite(u, W0)

        risks = sorted(u["risk0"] for u in units if u["risk0"] is not None)
        q = {p: quantile(risks, p) for p in (0.10, 0.25, 0.50, 0.75, 0.90)}
        cutA = [round(q[p], 1) for p in (0.25, 0.50, 0.75)]
        cutB = [40.0, 52.0, 64.0]
        cutC = [round(x) for x in cutA]           # v1 동결(정수)

        n_nolabel = sum(1 for u in units if u["label_risk"] is None)
        n_nodelta = sum(1 for u in units if u["p_delta"] is None)
        n_zero_open = sum(1 for u in units if u["open_r"] == 0)
        n_zero_close = sum(1 for u in units if u["close_r"] == 0)

        pc = [(u["p_close"], u["p_open"]) for u in units]
        r_close_open = pearson([a for a, _ in pc], [b for _, b in pc])
        pd = [(u["p_open"], u["p_delta"]) for u in units if u["p_delta"] is not None]
        r_open_delta = pearson([a for a, _ in pd], [b for _, b in pd])

        gl = defaultdict(Counter)
        for u in units:
            gl[grade_by_cuts(u["risk0"], cutC)][u["label"] or "무라벨"] += 1

        # 가중치 민감도
        variants = {
            "권고 0.25×4": W0,
            "폐업↑ 0.40/0.15/0.15/0.30": {"close": 0.40, "open": 0.15, "delta": 0.15, "label": 0.30},
            "라벨↓ 0.35/0.25/0.20/0.20": {"close": 0.35, "open": 0.25, "delta": 0.20, "label": 0.20},
            "라벨↑ 0.20/0.15/0.15/0.50": {"close": 0.20, "open": 0.15, "delta": 0.15, "label": 0.50},
            "점포중심 0.35/0.35/0.30/0.00": {"close": 0.35, "open": 0.35, "delta": 0.30, "label": 0.0},
        }
        base = {u["code"]: grade_by_cuts(u["risk0"], cutC) for u in units}
        sens = []
        for vn, w in variants.items():
            rv = sorted(composite(u, w) for u in units if composite(u, w) is not None)
            cv = [quantile(rv, p) for p in (0.25, 0.50, 0.75)]
            ch = big = 0
            for u in units:
                g = grade_by_cuts(composite(u, w), cv)
                if g != base[u["code"]]:
                    ch += 1
                    if abs(GRADES.index(g) - GRADES.index(base[u["code"]])) >= 2:
                        big += 1
            sens.append({"variant": vn, "n": len(units),
                         "grade_changed_pct": round(100 * ch / len(units), 1),
                         "shift_2plus_pct": round(100 * big / len(units), 1)})

        # 파일
        with open(os.path.join(OUT, f"units_{scope}.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["scope", "code", "name", "n_now", "n_yoy", "open_r", "close_r", "delta_ratio",
                        "label", "label_name", "label_risk", "p_close", "p_open", "p_delta",
                        "risk_v1", "grade_v1"])
            for u in sorted(units, key=lambda u: u["risk0"] or 0):
                w.writerow([u["scope"], u["code"], u["name"], u["n_now"], u["n_yoy"] or "",
                            round(u["open_r"], 2), round(u["close_r"], 2),
                            round(u["delta"], 4) if u["delta"] is not None else "",
                            u["label"] or "", LABEL_NAME.get(u["label"], ""), u["label_risk"],
                            round(u["p_close"], 1), round(u["p_open"], 1),
                            round(u["p_delta"], 1) if u["p_delta"] is not None else "",
                            round(u["risk0"], 1), grade_by_cuts(u["risk0"], cutC)])
        with open(os.path.join(OUT, f"grade_by_label_{scope}.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            labs = ["LH", "HH", "LL", "HL", "무라벨"]
            w.writerow(["grade"] + [f"{l}({LABEL_NAME.get(l,'-')})" for l in labs] + ["합계"])
            for g in GRADES:
                row = [gl[g][l] for l in labs]
                w.writerow([g] + row + [sum(row)])
        with open(os.path.join(OUT, f"weight_sensitivity_{scope}.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["variant", "n", "grade_changed_pct", "shift_2plus_pct"])
            for s in sens:
                w.writerow([s["variant"], s["n"], s["grade_changed_pct"], s["shift_2plus_pct"]])

        report[scope] = {
            "n_units": len(units), "n_no_label": n_nolabel, "n_no_delta": n_nodelta,
            "n_zero_open_rate": n_zero_open, "n_zero_close_rate": n_zero_close,
            "risk_quantiles": {str(k): round(v, 1) for k, v in q.items()},
            "cut_A_quartile": cutA, "cut_B_fixed": cutB, "cut_C_v1_frozen": cutC,
            "grade_dist": dict(Counter(grade_by_cuts(u["risk0"], cutC) for u in units)),
            "r_close_vs_open": round(r_close_open, 3) if r_close_open else None,
            "r_open_vs_delta": round(r_open_delta, 3) if r_open_delta else None,
            "grade_by_label": {g: dict(gl[g]) for g in GRADES},
            "weight_sensitivity": sens,
        }

        print(f"\n{'='*68}\n[{scope}]  단위 {len(units)}  무라벨 {n_nolabel}  Δ결측 {n_nodelta}  "
              f"개업률0 {n_zero_open}  폐업률0 {n_zero_close}")
        print(f"  risk 분위 p10={q[0.10]:.1f} p25={q[0.25]:.1f} p50={q[0.50]:.1f} p75={q[0.75]:.1f} p90={q[0.90]:.1f}")
        print(f"  컷A 사분위 {cutA}   컷B 고정 {cutB}   컷C v1동결 {cutC}")
        print(f"  등급분포(컷C) {report[scope]['grade_dist']}")
        print(f"  성분상관  close~open r={r_close_open:.3f}   open~delta r={r_open_delta:.3f}")
        print(f"  등급 × 라벨 (컷C):")
        for g in GRADES:
            print(f"    {g:4s} {dict(gl[g])}")
        print(f"  가중치 민감도 (등급변동%/2등급↑%):")
        for s in sens:
            print(f"    {s['variant']:34s} {s['grade_changed_pct']:5.1f} / {s['shift_2plus_pct']:.1f}")

    with open(os.path.join(OUT, "cut_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n→ {OUT}/")


if __name__ == "__main__":
    main()
