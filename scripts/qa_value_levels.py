"""값 수준 QA — 추정매출·점포·유동 데이터가 판정에 쓰이기 전에 이상치·0 vs 결측·greenfield를 점검.

파이프라인이 실제로 소비하는 값(20261, 상권/행정동, 10개 외식업)만 검사한다:
  - store_count = 점포 `전체_점포_수`(상권) / `점포_수`(행정동)
  - sales       = 추정매출 `당월_매출_금액`
  - rev_per_store = sales / store_count  (store_count > 0)
  - franchise_ratio = 프랜차이즈_점포_수 / store_count
  - flow_density = 총_유동인구_수 / 영역_면적  (상권)

산출:
  output/feature_validation/value_level_qa.csv          (scope×업종 요약)
  output/feature_validation/value_level_qa_flow.csv     (유동/면적 요약)
  output/feature_validation/value_level_qa_examples.csv (이상치 표본)
  output/feature_validation/value_level_qa_summary.json
"""
from __future__ import annotations
import csv, json, os, statistics, unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Q = "20261"
INDS = {f"CS100{i:03d}": nm for i, nm in enumerate(
    ["한식", "중식", "일식", "양식", "제과점", "패스트푸드", "치킨", "분식", "호프-간이주점", "커피-음료"], 1)}

# 점포당매출(월) 현실 범위 — 이 밖이면 이상치로 플래그
REV_PP_MIN = 100_000            # 월 10만 원 미만: 매출 부분집계·데이터 오류 의심
REV_PP_MAX = 2_000_000_000      # 월 20억 원 초과: 점포수 과소·대형점 1개 의심

_EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드", "trdar_cd": "상권_코드", "svc_induty_cd": "서비스_업종_코드",
    "stor_co": "전체_점포_수", "점포_수": "전체_점포_수", "similr_induty_stor_co": "유사_업종_점포_수",
    "frc_stor_co": "프랜차이즈_점포_수", "opbiz_rt": "개업_율", "opbiz_stor_co": "개업_점포_수",
    "clsbiz_rt": "폐업_률", "clsbiz_stor_co": "폐업_점포_수", "thsmon_selng_amt": "당월_매출_금액",
    "thsmon_selng_co": "당월_매출_건수", "adstrd_cd": "행정동_코드",
}


def realpath(nfc: str) -> str:
    top = nfc.split("/")[0]
    for dp, _, fs in os.walk(ROOT / top):
        for fn in fs:
            full = os.path.join(dp, fn)
            if unicodedata.normalize("NFC", str(Path(full).relative_to(ROOT))) == nfc:
                return full
    raise FileNotFoundError(nfc)


def rd(nfc: str) -> list[dict]:
    path = realpath(nfc)
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            rows = list(csv.DictReader(open(path, encoding=enc)))
            break
        except UnicodeDecodeError:
            continue
    out = []
    for r in rows:
        out.append({_EN2KO.get(k, k): v for k, v in r.items()})
    return out


def fnum(row: dict, key: str):
    """반환: (값 or None, 상태)  상태 ∈ missing/blank/nonnum/neg/zero/ok"""
    if key not in row:
        return None, "missing"
    raw = (row.get(key) or "").strip()
    if raw == "":
        return None, "blank"
    try:
        v = float(raw)
    except ValueError:
        return None, "nonnum"
    if v < 0:
        return v, "neg"
    if v == 0:
        return 0.0, "zero"
    return v, "ok"


def pctl(sorted_vals: list[float], p: float):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(p / 100 * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def scope_check(scope: str, store_file: str, sales_file: str, code_col: str):
    store = defaultdict(dict)   # (code, ind) -> row
    for r in rd(store_file):
        if r.get("기준_년분기_코드") != Q:
            continue
        store[(r.get(code_col), r.get("서비스_업종_코드"))] = r
    sales = defaultdict(dict)
    for r in rd(sales_file):
        if r.get("기준_년분기_코드") != Q:
            continue
        sales[(r.get(code_col), r.get("서비스_업종_코드"))] = r

    rows_out = []
    examples = []
    for ind, ind_nm in INDS.items():
        s_keys = {k for k in store if k[1] == ind}
        v_keys = {k for k in sales if k[1] == ind}
        rec = {"scope": scope, "industry": ind, "industry_name": ind_nm,
               "store_cells": len(s_keys), "sales_cells": len(v_keys)}
        st = {"missing": 0, "blank": 0, "nonnum": 0, "neg": 0, "zero": 0, "ok": 0}
        sv = {"missing": 0, "blank": 0, "nonnum": 0, "neg": 0, "zero": 0, "ok": 0}
        fr_gt_total = 0
        parts_mismatch = 0
        rate_bad = 0
        store_pos_sales_absent = 0     # 점포>0 인데 매출 행 없음  (파이프라인 "결측" 경로)
        store_pos_sales_zero = 0       # 점포>0 인데 매출 0
        sales_pos_store_absent = 0     # 매출>0 인데 점포 행 없음/0
        greenfield = 0                 # 점포 0 또는 결측
        rev_pp = []
        for k in s_keys | v_keys:
            srow = store.get(k)
            vrow = sales.get(k)
            sc, sc_state = fnum(srow, "전체_점포_수") if srow else (None, "missing")
            am, am_state = fnum(vrow, "당월_매출_금액") if vrow else (None, "missing")
            st[sc_state] += 1
            sv[am_state] += 1
            if srow:
                fr, _ = fnum(srow, "프랜차이즈_점포_수")
                gen, _ = fnum(srow, "일반_점포_수")
                if fr is not None and sc is not None and fr > sc:
                    fr_gt_total += 1
                if fr is not None and gen is not None and sc is not None and abs((fr + gen) - sc) > 0.5:
                    parts_mismatch += 1
                for rk in ("개업_율", "폐업_률"):
                    rv, _ = fnum(srow, rk)
                    if rv is not None and (rv < 0 or rv > 100):
                        rate_bad += 1
            if sc is None or sc == 0:
                greenfield += 1
            if sc is not None and sc > 0:
                if vrow is None:
                    store_pos_sales_absent += 1
                elif am_state == "zero":
                    store_pos_sales_zero += 1
                elif am is not None and am > 0:
                    rev_pp.append(am / sc)
            if am is not None and am > 0 and (sc is None or sc == 0):
                sales_pos_store_absent += 1

        rev_pp.sort()
        rec.update({
            "store_missing_row": st["missing"], "store_blank": st["blank"], "store_nonnum": st["nonnum"],
            "store_neg": st["neg"], "store_zero": st["zero"],
            "sales_missing_row": sv["missing"], "sales_blank": sv["blank"], "sales_neg": sv["neg"], "sales_zero": sv["zero"],
            "franchise_gt_total": fr_gt_total, "parts_sum_mismatch": parts_mismatch, "rate_out_of_0_100": rate_bad,
            "greenfield_cells": greenfield,
            "store_pos_but_sales_row_absent": store_pos_sales_absent,
            "store_pos_but_sales_zero": store_pos_sales_zero,
            "sales_pos_but_store_absent_or_0": sales_pos_store_absent,
            "rev_pp_n": len(rev_pp),
            "rev_pp_min": round(rev_pp[0]) if rev_pp else None,
            "rev_pp_p01": round(pctl(rev_pp, 1)) if rev_pp else None,
            "rev_pp_p25": round(pctl(rev_pp, 25)) if rev_pp else None,
            "rev_pp_median": round(statistics.median(rev_pp)) if rev_pp else None,
            "rev_pp_p75": round(pctl(rev_pp, 75)) if rev_pp else None,
            "rev_pp_p99": round(pctl(rev_pp, 99)) if rev_pp else None,
            "rev_pp_max": round(rev_pp[-1]) if rev_pp else None,
            "rev_pp_below_min": sum(1 for v in rev_pp if v < REV_PP_MIN),
            "rev_pp_above_max": sum(1 for v in rev_pp if v > REV_PP_MAX),
        })
        rows_out.append(rec)

        # 이상치 표본: rev_pp 최소/최대 3건씩
        pairs = []
        for k in s_keys & v_keys:
            sc, _ = fnum(store[k], "전체_점포_수")
            am, _ = fnum(sales[k], "당월_매출_금액")
            if sc and sc > 0 and am and am > 0:
                pairs.append((am / sc, k, sc, am))
        pairs.sort()
        for tag, sub in (("min", pairs[:3]), ("max", pairs[-3:])):
            for rpp, k, sc, am in sub:
                examples.append({"scope": scope, "industry": ind, "industry_name": ind_nm, "flag": tag,
                                 "code": k[0], "name": (store[k].get("상권_코드_명") or store[k].get("행정동_코드_명") or ""),
                                 "store_count": int(sc), "sales_month": int(am), "rev_per_store": round(rpp)})
    return rows_out, examples


def flow_check():
    area = {"상권": {}, "행정동": {}}
    for r in rd("data/영역/상권/서울시 상권분석서비스(영역-상권).csv"):
        a, _ = fnum(r, "영역_면적")
        area["상권"][r.get("상권_코드")] = a
    for r in rd("data/영역/행정동/서울시 상권분석서비스(영역-행정동).csv"):
        a, _ = fnum(r, "영역_면적")
        area["행정동"][r.get("행정동_코드")] = a
    rows = []
    examples = []
    for scope, ffile, code_col in (
        ("상권", "data/길단위인구/서울시 상권분석서비스(길단위인구-상권).csv", "상권_코드"),
        ("행정동", "data/길단위인구/서울시 상권분석서비스(길단위인구-행정동).csv", "행정동_코드"),
    ):
        st = {"missing": 0, "blank": 0, "neg": 0, "zero": 0, "ok": 0, "nonnum": 0}
        area_zero = area_missing = 0
        dens = []
        for r in rd(ffile):
            if r.get("기준_년분기_코드") != Q:
                continue
            fv, state = fnum(r, "총_유동인구_수")
            st[state] += 1
            a = area[scope].get(r.get(code_col))
            if a is None:
                area_missing += 1
            elif a == 0:
                area_zero += 1
            elif fv and fv > 0 and a:
                d = fv / a
                dens.append(d)
                examples.append((d, scope, r.get(code_col), r.get(f"{code_col}_명", "") or r.get("행정동_코드_명", "") or r.get("상권_코드_명", ""), int(fv), round(a, 1)))
        dens.sort()
        rows.append({"scope": scope, "flow_missing_row": st["missing"], "flow_blank": st["blank"],
                     "flow_neg": st["neg"], "flow_zero": st["zero"], "flow_ok": st["ok"],
                     "area_missing": area_missing, "area_zero": area_zero,
                     "density_n": len(dens),
                     "density_min": round(dens[0], 2) if dens else None,
                     "density_p01": round(pctl(dens, 1), 2) if dens else None,
                     "density_median": round(statistics.median(dens), 2) if dens else None,
                     "density_p99": round(pctl(dens, 99), 2) if dens else None,
                     "density_max": round(dens[-1], 2) if dens else None})
    examples.sort()
    tagged = [("min", *e) for e in examples[:5]] + [("max", *e) for e in examples[-5:]]
    ex_rows = [{"metric": "flow_density", "flag": t, "scope": s, "code": c, "name": n, "flow": f, "area_m2": a, "density": round(d, 2)}
               for t, d, s, c, n, f, a in tagged]
    return rows, ex_rows


def main():
    out = ROOT / "output/feature_validation"
    out.mkdir(parents=True, exist_ok=True)

    all_rows, all_ex = [], []
    for scope, sf, vf, cc in (
        ("상권", "data/점포/2026년/서울시 상권분석서비스(점포-상권).csv",
         "data/추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv", "상권_코드"),
        ("행정동", "data/점포/2026년/서울시 상권분석서비스(점포-행정동).csv",
         "data/추정매출/2026/서울시 상권분석서비스(추정매출-행정동).csv", "행정동_코드"),
    ):
        r, e = scope_check(scope, sf, vf, cc)
        all_rows += r
        all_ex += e

    flow_rows, flow_ex = flow_check()

    with open(out / "value_level_qa.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    with open(out / "value_level_qa_flow.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(flow_rows[0].keys()))
        w.writeheader()
        w.writerows(flow_rows)
    with open(out / "value_level_qa_examples.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_ex[0].keys()))
        w.writeheader()
        w.writerows(all_ex + [dict.fromkeys(all_ex[0].keys())] )
        w2 = csv.DictWriter(f, fieldnames=list(flow_ex[0].keys()))
        w2.writeheader()
        w2.writerows(flow_ex)

    agg = lambda k: sum(r[k] for r in all_rows)
    summary = {
        "quarter": Q, "scopes": ["상권", "행정동"], "industries": list(INDS),
        "rev_pp_realistic_range_krw_month": [REV_PP_MIN, REV_PP_MAX],
        "totals": {
            "store_cells": agg("store_cells"), "sales_cells": agg("sales_cells"),
            "greenfield_cells": agg("greenfield_cells"),
            "store_blank": agg("store_blank"), "store_neg": agg("store_neg"), "store_zero": agg("store_zero"),
            "sales_blank": agg("sales_blank"), "sales_neg": agg("sales_neg"), "sales_zero": agg("sales_zero"),
            "franchise_gt_total": agg("franchise_gt_total"), "parts_sum_mismatch": agg("parts_sum_mismatch"),
            "rate_out_of_0_100": agg("rate_out_of_0_100"),
            "store_pos_but_sales_row_absent": agg("store_pos_but_sales_row_absent"),
            "store_pos_but_sales_zero": agg("store_pos_but_sales_zero"),
            "sales_pos_but_store_absent_or_0": agg("sales_pos_but_store_absent_or_0"),
            "rev_pp_n": agg("rev_pp_n"), "rev_pp_below_min": agg("rev_pp_below_min"), "rev_pp_above_max": agg("rev_pp_above_max"),
        },
        "flow": flow_rows,
    }
    (out / "value_level_qa_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== 값 수준 QA ({Q}) — 상권+행정동 × 10 외식업 ===")
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=2))
    print("\n-- 점포당매출 분포 (원/월) --")
    for r in all_rows:
        print(f"  {r['scope']:4s} {r['industry_name']:9s} n={r['rev_pp_n']:4d}  "
              f"min {r['rev_pp_min']:>12,}  p25 {r['rev_pp_p25']:>13,}  med {r['rev_pp_median']:>13,}  "
              f"p99 {r['rev_pp_p99']:>15,}  max {r['rev_pp_max']:>15,}  <10만:{r['rev_pp_below_min']} >20억:{r['rev_pp_above_max']}")
    print("\n-- 유동/면적 --")
    for r in flow_rows:
        print(f"  {r['scope']:4s} 결측행 {r['flow_missing_row']} 공백 {r['flow_blank']} 0 {r['flow_zero']} | "
              f"면적결측 {r['area_missing']} 면적0 {r['area_zero']} | 밀도 med {r['density_median']} max {r['density_max']}")
    print(f"\n산출: {out}/value_level_qa*.csv|json")


if __name__ == "__main__":
    main()
