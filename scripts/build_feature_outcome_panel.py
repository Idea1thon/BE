"""FC 피처 ↔ outcome 패널 빌더 (상권×업종, 상권).

`artifacts/10-analysis/feature-evidential-value.md` 검증용. spec 0절(원천 CSV 직접
조인 금지 — 위치 로드 → rename → grain → 분기배정 → 병합) 준수.

산출:
  output/feature_validation/panel_trdar_industry.csv  상권×업종(10 외식) × FC피처 + outcome
  output/feature_validation/panel_trdar.csv            상권 × 지역배경 FC (차원 분리도용)
  output/feature_validation/panel_manifest.json        행수·커버리지·분기·결측률

한계: 신규 점포 매출·손익 outcome 없음 → "좋은 입지 = 성공" 검증 불가.
outcome 은 인허가 생존/점포 교체 프록시. 인허가 폐업일자는 행정처리일이라 지연 완충 필요.

실행: .venv/bin/python3 scripts/build_feature_outcome_panel.py
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import shapefile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "feature_validation")
os.makedirs(OUT, exist_ok=True)

INDS = [f"CS1000{i:02d}" for i in range(1, 11)]
IND_NM = {
    "CS100001": "한식", "CS100002": "중식", "CS100003": "일식", "CS100004": "양식",
    "CS100005": "제과점", "CS100006": "패스트푸드", "CS100007": "치킨", "CS100008": "분식",
    "CS100009": "호프-간이주점", "CS100010": "커피-음료",
}
Q_NOW = "20261"
Q_TREND = ["20252", "20253", "20254", "20261"]   # FC-02/12 4분기 기울기
Q_YOY = "20254"
COHORT_QS = [f"20{y}{q}" for y in range(21, 25) for q in (1, 2, 3, 4)]   # surv_1y 코호트 2021~2024
CHURN_YEARS = ("2022", "2023", "2024", "2025")                          # churn_ratio 집계 창

_EN2KO = {
    "stdr_yyqu_cd": "기준_년분기_코드", "trdar_cd": "상권_코드", "adstrd_cd": "행정동_코드",
    "svc_induty_cd": "서비스_업종_코드", "stor_co": "전체_점포_수", "frc_stor_co": "프랜차이즈_점포_수",
    "opbiz_rt": "개업_율", "clsbiz_rt": "폐업_률", "opbiz_stor_co": "개업_점포_수",
    "clsbiz_stor_co": "폐업_점포_수", "thsmon_selng_amt": "당월_매출_금액", "점포_수": "전체_점포_수",
}


def rd(path, enc="cp949"):
    df = pd.read_csv(os.path.join(ROOT, path), encoding=enc, dtype=str)
    if "stdr_yyqu_cd" in df.columns or "점포_수" in df.columns:
        df = df.rename(columns=_EN2KO)
    return df


def num(s):
    return pd.to_numeric(s, errors="coerce")


def qadd(qcode: str, n: int) -> str:
    y, q = int(qcode[:4]), int(qcode[4])
    idx = y * 4 + (q - 1) + n
    return f"{idx // 4}{idx % 4 + 1}"


def within_industry_pctl(df: pd.DataFrame, cols, ind_col="업종코드"):
    """각 업종 내 서울 상권 분위(0~100). NaN 유지."""
    for c in cols:
        df[c + "_pctl"] = df.groupby(ind_col)[c].rank(pct=True) * 100
    return df


def linslope_pct(vals):
    """4점 선형회귀 기울기를 평균 대비 %/분기 로. NaN 있으면 NaN."""
    v = np.asarray(vals, float)
    if np.isnan(v).any() or v.mean() == 0:
        return np.nan
    x = np.arange(len(v))
    slope = np.polyfit(x, v, 1)[0]
    return 100 * slope / v.mean()


# ---------------------------------------------------------------- 공간 키
def spatial_keys():
    sf = shapefile.Reader(os.path.join(ROOT, "data/영역/상권/서울시 상권분석서비스(영역-상권)"),
                          encoding="utf-8")
    flds = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    rows = []
    for rec in sf.iterRecords():
        d = dict(zip(flds, rec))
        rows.append({"상권_코드": str(d["TRDAR_CD"]), "상권명": d["TRDAR_CD_N"],
                     "자치구": d["SIGNGU_CD_"], "행정동_코드": str(d["ADSTRD_CD"]),
                     "행정동명": d["ADSTRD_CD_"], "면적_m2": float(d["RELM_AR"]),
                     "상권_구분": d["TRDAR_SE_1"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 상권 배경 FC
def trdar_features(sk: pd.DataFrame) -> pd.DataFrame:
    f = sk[["상권_코드", "자치구", "행정동_코드", "면적_m2"]].copy()

    flow = rd("data/길단위인구/서울시 상권분석서비스(길단위인구-상권).csv")
    flow["총_유동인구_수"] = num(flow["총_유동인구_수"])
    fnow = flow[flow["기준_년분기_코드"] == Q_NOW].set_index("상권_코드")["총_유동인구_수"]
    f = f.join(fnow.rename("flow_total"), on="상권_코드")
    f["FC01_flow_density"] = f["flow_total"] / f["면적_m2"]
    # FC-02 유동 4Q 기울기 / FC-40 최근 급증률
    fp = flow[flow["기준_년분기_코드"].isin(Q_TREND)].pivot_table(
        index="상권_코드", columns="기준_년분기_코드", values="총_유동인구_수")
    fp = fp.reindex(columns=Q_TREND)
    f = f.join(fp.apply(lambda r: linslope_pct(r.values), axis=1).rename("FC02_flow_trend"), on="상권_코드")
    f = f.join(((fp[Q_NOW] - fp[Q_YOY]) / fp[Q_YOY] * 100).rename("FC40_flow_surge"), on="상권_코드")

    resid = rd("data/상주인구/서울시 상권분석서비스(상주인구-상권).csv")
    rnow = resid[resid["기준_년분기_코드"] == Q_NOW].set_index("상권_코드")
    f = f.join(num(rnow["총_상주인구_수"]).rename("FC03_resident"), on="상권_코드")
    f = f.join((num(rnow["아파트_가구_수"]) / num(rnow["총_가구_수"])).rename("resid_apt_ratio"), on="상권_코드")

    work = rd("data/직장인구/서울시 상권분석서비스(직장인구-상권).csv")
    wnow = work[work["기준_년분기_코드"] == Q_NOW].set_index("상권_코드")
    f = f.join(num(wnow["총_직장_인구_수"]).rename("FC04_worker"), on="상권_코드")

    # FC-05 활동유형: 유동·상주·직장 각각 서울 z-score
    for src, col in [("FC01_flow_density", "fc05_flow_z"), ("FC03_resident", "fc05_resid_z"),
                     ("FC04_worker", "fc05_work_z")]:
        v = f[src]
        f[col] = (v - v.mean()) / v.std()
    f["FC05_activity_spread"] = f[["fc05_flow_z", "fc05_resid_z", "fc05_work_z"]].max(axis=1) - \
        f[["fc05_flow_z", "fc05_resid_z", "fc05_work_z"]].min(axis=1)

    # FC-07 대중교통
    st = rd("data/도시철도역사/상권_역세권.csv", "utf-8-sig").set_index("상권_코드")
    bus = rd("data/버스정류장/상권_버스접근성.csv", "utf-8-sig").set_index("상권_코드")
    f = f.join(num(st["상권내_역_수"]).rename("FC07_station_n"), on="상권_코드")
    f = f.join(num(st["최근접_역_거리_m"]).rename("FC07_station_dist"), on="상권_코드")
    f = f.join((st["역세권_250m"] == "Y").astype(int).rename("FC07_station_250m"), on="상권_코드")
    f = f.join(num(bus["250m내_정류소_수"]).rename("FC07_bus_n"), on="상권_코드")
    f = f.join(num(bus["간선_중앙차로_정류소_수"]).rename("FC07_bus_trunk_n"), on="상권_코드")

    # FC-08 배후주거
    ap = rd("data/공동주택/상권_아파트접근성.csv", "utf-8-sig").set_index("상권_코드")
    f = f.join(num(ap["250m내_세대수합"]).rename("FC08_apt_hh_250m"), on="상권_코드")
    f = f.join(num(ap["상권내_총세대수"]).rename("FC08_apt_hh_in"), on="상권_코드")

    # FC-10 entry_health / FC-11 변화라벨
    eh = pd.read_csv(os.path.join(ROOT, "output/entry_health/units_상권.csv"),
                     encoding="utf-8-sig", dtype={"code": str})
    eh = eh.set_index("code")
    f = f.join(num(eh["risk_v1"]).rename("FC10_entry_health_risk"), on="상권_코드")
    f = f.join(num(eh["label_risk"]).rename("FC11_label_risk"), on="상권_코드")
    f = f.join(eh["label"].rename("chg_label"), on="상권_코드")

    # FC-20 임대료: R-ONE 상권 임대가격지수(join_eligible) → 없으면 서울전체
    cw = pd.read_csv(os.path.join(ROOT, "output/crosswalks/crosswalk_rone_trdar.csv"),
                     encoding="utf-8-sig", dtype=str)
    cw = cw[(cw["join_eligible"] == "yes") & (cw["mapping_role"] == "primary")]
    t2rone = cw.set_index("TRDAR_CD")["R_ONE_상권"].to_dict()
    rone = rd("data/임대료/R-ONE_임대동향_분기.csv", "utf-8-sig")
    rone["값"] = num(rone["값"])
    ridx = rone[(rone["상가유형"] == "소규모상가") & (rone["지표"] == "임대가격지수")]
    rq = ridx[ridx["기준_년분기_코드"] == "20262"]
    rone_area = rq[rq["grain"] == "상권"].set_index("R_ONE_상권")["값"].to_dict()
    seoul_rent = rq[rq["grain"] == "서울전체"]["값"].mean()
    f["FC20_rent_index"] = f["상권_코드"].map(
        lambda c: rone_area.get(t2rone.get(c), np.nan))
    f["FC20_rent_is_seoul_proxy"] = f["FC20_rent_index"].isna().astype(int)
    f["FC20_rent_index"] = f["FC20_rent_index"].fillna(seoul_rent)

    # FC-51 정비/개발사업 겹침
    dev = rd("data/도시계획사업/도시계획사업_상권겹침.csv", "utf-8-sig")
    dev["겹침_상권비율"] = num(dev["겹침_상권비율"])
    dsum = dev.groupby("상권_코드")["겹침_상권비율"].sum()
    f = f.join(dsum.rename("FC51_redev_overlap"), on="상권_코드")
    f["FC51_redev_overlap"] = f["FC51_redev_overlap"].fillna(0.0)

    # FC-52 계획철도 (자치구) / FC-53 고용률 (자치구) / FC-41 검색 (자치구)
    plan = rd("data/도시철도역사/도시철도망계획_자치구.csv", "utf-8-sig").set_index("자치구")
    f = f.join(num(plan["신설연장_노선_수"]).rename("FC52_plan_rail_n"), on="자치구")
    f["FC52_plan_rail_n"] = f["FC52_plan_rail_n"].fillna(0.0)

    emp = rd("data/고용률/자치구_고용률_반기.csv", "utf-8-sig")
    emp["고용률"] = num(emp["고용률"])
    emp_latest = emp[emp["성별"] == "계"].sort_values("기준_반기").groupby("자치구").tail(1)
    f = f.join(emp_latest.set_index("자치구")["고용률"].rename("FC53_employ_rate"), on="자치구")

    nv = rd("data/네이버트렌드/자치구_검색트렌드_월.csv", "utf-8-sig")
    nv["rel_index"] = num(nv["rel_index"])
    nv_recent = nv.sort_values("기준_년월").groupby("자치구").tail(3).groupby("자치구")["rel_index"].mean()
    f = f.join(nv_recent.rename("FC41_gu_search"), on="자치구")

    # FC-06a/b 외국인 (행정동 → 상권 프록시)
    fgn = rd("data/외국인생활인구/외국인생활인구_행정동_분기.csv", "utf-8-sig")
    fgn = fgn[fgn["기준_년분기_코드"] == Q_NOW]
    rd_ = rd("data/상주인구/서울시 상권분석서비스(상주인구-행정동).csv")
    rd_ = rd_[rd_["기준_년분기_코드"] == Q_NOW].set_index("행정동_코드")
    fl_ = rd("data/길단위인구/서울시 상권분석서비스(길단위인구-행정동).csv")
    fl_ = fl_[fl_["기준_년분기_코드"] == Q_NOW].set_index("행정동_코드")
    fgn["dong"] = fgn["행정동_코드"].astype(str)
    fgn["lg"] = num(fgn["장기_외국인_평균"])
    fgn["dg"] = num(fgn["단기_외국인_평균"])
    fgn["res"] = fgn["dong"].map(num(rd_["총_상주인구_수"]))
    fgn["fday"] = fgn["dong"].map(num(fl_["총_유동인구_수"])) / 90
    fgn["FC06a_foreign_resident"] = 100 * fgn["lg"] / fgn["res"]
    fgn["FC06b_foreign_visit"] = 100 * fgn["dg"] / fgn["fday"]
    fdong = fgn.set_index("dong")[["FC06a_foreign_resident", "FC06b_foreign_visit"]]
    f = f.join(fdong, on="행정동_코드")

    return f


# ---------------------------------------------------------------- 상권×업종 FC
def trdar_industry_features(sk: pd.DataFrame) -> pd.DataFrame:
    K = ["상권_코드", "업종코드"]

    def load_store(path):
        s = rd(path)
        s = s[s["서비스_업종_코드"].isin(INDS)].rename(columns={"서비스_업종_코드": "업종코드"})
        for c in ["전체_점포_수", "프랜차이즈_점포_수", "개업_율", "폐업_률"]:
            s[c] = num(s[c])
        return s

    s26 = load_store("data/점포/2026년/서울시 상권분석서비스(점포-상권).csv")
    s25 = load_store("data/점포/2025년/서울시 상권분석서비스(점포-상권)_2025년.csv")
    snow = s26[s26["기준_년분기_코드"] == Q_NOW][K + ["전체_점포_수", "프랜차이즈_점포_수", "개업_율", "폐업_률"]].copy()

    d = snow.merge(sk[["상권_코드", "면적_m2"]], on="상권_코드", how="left")
    d["FC30_store_density"] = d["전체_점포_수"] / (d["면적_m2"] / 10000)
    d["FC32_franchise_ratio"] = d["프랜차이즈_점포_수"] / d["전체_점포_수"]
    d["net_open_점포"] = d["개업_율"] - d["폐업_률"]
    d = d.rename(columns={"폐업_률": "close_r_점포"})

    # YoY 점포 증감
    n_yoy = s25[s25["기준_년분기_코드"] == Q_YOY][K + ["전체_점포_수"]].rename(columns={"전체_점포_수": "n_yoy"})
    d = d.merge(n_yoy, on=K, how="left")
    d["delta_store"] = (d["전체_점포_수"] - d["n_yoy"]) / d["n_yoy"].replace(0, np.nan) * 100

    # FC-12 폐업률 4Q 기울기 + outcome: 점포 4Q 평균 폐업률·순개업률
    four = pd.concat([
        s25[s25["기준_년분기_코드"].isin(["20252", "20253", "20254"])][K + ["기준_년분기_코드", "폐업_률", "개업_율"]],
        s26[s26["기준_년분기_코드"] == Q_NOW][K + ["기준_년분기_코드", "폐업_률", "개업_율"]],
    ])
    clsp = four.pivot_table(index=K, columns="기준_년분기_코드", values="폐업_률").reindex(columns=Q_TREND)
    fc12 = clsp.apply(lambda r: linslope_pct(r.values), axis=1).rename("FC12_close_trend").reset_index()
    d = d.merge(fc12, on=K, how="left")
    m4 = four.groupby(K).agg(close_r_4q=("폐업_률", "mean"), open_r_4q=("개업_율", "mean")).reset_index()
    m4["net_open_4q"] = m4["open_r_4q"] - m4["close_r_4q"]
    d = d.merge(m4[K + ["close_r_4q", "net_open_4q"]], on=K, how="left")

    # FC-31 점포당 매출
    sales = rd("data/추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv")
    sales = sales[(sales["서비스_업종_코드"].isin(INDS)) & (sales["기준_년분기_코드"] == Q_NOW)]
    sales = sales.rename(columns={"서비스_업종_코드": "업종코드"})
    sales["당월_매출_금액"] = num(sales["당월_매출_금액"])
    d = d.merge(sales[K + ["당월_매출_금액"]], on=K, how="left")
    d["FC31_rev_per_store"] = d["당월_매출_금액"] / d["전체_점포_수"]

    # FC-42 업종 검색 (업종별 상수 — 상권 무관)
    nvi = rd("data/네이버트렌드/업종_검색트렌드_월.csv", "utf-8-sig")
    nvi["rel_index"] = num(nvi["rel_index"])
    nvi_recent = nvi.sort_values("기준_년월").groupby("업종코드").tail(3).groupby("업종코드")["rel_index"].mean()
    d["FC42_industry_search"] = d["업종코드"].map(nvi_recent)
    return d


# ---------------------------------------------------------------- outcome: 인허가
def _qcode_to_idx(s: pd.Series) -> pd.Series:
    y = pd.to_numeric(s.str.slice(0, 4), errors="coerce")
    q = pd.to_numeric(s.str.slice(4, 5), errors="coerce")
    return y * 4 + (q - 1)


def licence_outcomes() -> pd.DataFrame:
    K = ["상권_코드", "업종코드"]

    # (1) 코호트 1년 생존율 — 업소 단위
    lic = pd.read_csv(os.path.join(ROOT, "data/인허가/음식점_인허가_서울.csv"),
                      encoding="utf-8", dtype=str).fillna("")
    lic = lic[lic["업종코드"].isin(INDS)].copy()
    lic["oq"], lic["cq"] = lic["인허가_분기"].str.strip(), lic["폐업_분기"].str.strip()
    coh = lic[lic["oq"].isin(COHORT_QS) & (lic["상권_코드"] != "")].copy()
    oi = _qcode_to_idx(coh["oq"])
    ci = _qcode_to_idx(coh["cq"].where(coh["cq"] != "", None))
    coh["alive_4q"] = ci.isna() | (ci >= oi + 4)
    coh["alive_5q"] = ci.isna() | (ci >= oi + 5)
    g = coh.groupby(K)
    surv = pd.DataFrame({"n_cohort": g.size(),
                         "surv_1y": g["alive_4q"].mean() * 100,
                         "surv_1y_buf5": g["alive_5q"].mean() * 100}).reset_index()

    # (2) 개업 대비 폐업 비율 + 순증 — 상권분기 패널 (완전 분기만)
    pan = pd.read_csv(os.path.join(ROOT, "data/인허가/음식점_상권분기_패널.csv"),
                      encoding="utf-8-sig", dtype=str)
    pan.columns = [c.lstrip("﻿") for c in pan.columns]
    for c in ["영업중_수", "신규개업_수", "폐업_수"]:
        pan[c] = num(pan[c])
    comp = pan[pan["분기_상태"] == "완전"]
    win = comp[comp["기준_년분기_코드"].str[:4].isin(CHURN_YEARS)]
    ag = win.groupby(K).agg(opens=("신규개업_수", "sum"), closes=("폐업_수", "sum")).reset_index()
    ag["churn_ratio"] = ag["closes"] / ag["opens"].replace(0, np.nan)
    base = comp[comp["기준_년분기_코드"] == "20221"].set_index(K)["영업중_수"]
    now = comp[comp["기준_년분기_코드"] == Q_NOW].set_index(K)["영업중_수"]
    ng = ((now - base) / base.replace(0, np.nan) * 100).rename("net_growth_pct").reset_index()

    out = surv.merge(ag[K + ["opens", "closes", "churn_ratio"]], on=K, how="outer")
    out = out.merge(ng, on=K, how="outer")
    out["상권_코드"] = out["상권_코드"].astype(str)

    # 상권 단위(전 외식 pooled) — 차원 분리도·자치구상수 FC용
    tg = win.groupby("상권_코드").agg(opens=("신규개업_수", "sum"), closes=("폐업_수", "sum"))
    tg["churn_ratio"] = tg["closes"] / tg["opens"].replace(0, np.nan)
    tb = comp[comp["기준_년분기_코드"] == "20221"].groupby("상권_코드")["영업중_수"].sum()
    tn = comp[comp["기준_년분기_코드"] == Q_NOW].groupby("상권_코드")["영업중_수"].sum()
    tg["net_growth_pct"] = (tn - tb) / tb.replace(0, np.nan) * 100
    tg = tg.reset_index()
    tg["상권_코드"] = tg["상권_코드"].astype(str)
    return out, tg


# ---------------------------------------------------------------- main
def main():
    sk = spatial_keys()
    tf = trdar_features(sk)
    tif = trdar_industry_features(sk)
    surv, surv_trdar = licence_outcomes()

    tf = tf.merge(surv_trdar.rename(columns={"opens": "opens_all", "closes": "closes_all",
                                             "churn_ratio": "churn_ratio_all",
                                             "net_growth_pct": "net_growth_pct_all"}),
                  on="상권_코드", how="left")

    ti = tif.merge(surv, on=["상권_코드", "업종코드"], how="left")
    bg_cols = [c for c in tf.columns if c.startswith("FC") or c in
               ("chg_label", "자치구", "행정동_코드", "면적_m2", "resid_apt_ratio",
                "fc05_flow_z", "fc05_resid_z", "fc05_work_z", "FC20_rent_is_seoul_proxy")]
    ti = ti.merge(tf[["상권_코드"] + bg_cols], on="상권_코드", how="left")
    ti["업종명"] = ti["업종코드"].map(IND_NM)
    ti["n_cohort"] = ti["n_cohort"].fillna(0)
    ti["opens"] = ti["opens"].fillna(0)
    # 표본 등급: churn_ratio 는 opens>=5, 생존율은 n_cohort>=5, 점포지표는 점포>=5
    ti["sample_full"] = ((ti["전체_점포_수"].fillna(0) >= 5) & (ti["n_cohort"] >= 5)).astype(int)
    ti["sample_churn_ok"] = (ti["opens"] >= 5).astype(int)
    ti["sample_surv_ok"] = (ti["n_cohort"] >= 5).astype(int)

    fc_num_cols = [c for c in ti.columns if c.startswith("FC") and ti[c].dtype != object]
    ti = within_industry_pctl(ti, fc_num_cols)
    ti["rev_pp_pctl"] = ti["FC31_rev_per_store_pctl"]

    ti.to_csv(os.path.join(OUT, "panel_trdar_industry.csv"), index=False, encoding="utf-8-sig")
    tf.to_csv(os.path.join(OUT, "panel_trdar.csv"), index=False, encoding="utf-8-sig")

    outcome_cols = ["churn_ratio", "surv_1y", "surv_1y_buf5", "net_growth_pct",
                    "close_r_4q", "net_open_4q", "delta_store", "rev_pp_pctl"]
    ch = ti[ti["sample_churn_ok"] == 1]
    sv = ti[ti["sample_surv_ok"] == 1]
    manifest = {
        "grain": "상권×업종 (10 외식) + 상권",
        "quarter_now": Q_NOW,
        "surv_cohort_quarters": [COHORT_QS[0], COHORT_QS[-1]],
        "churn_window_years": list(CHURN_YEARS),
        "rows_trdar_industry": len(ti), "rows_trdar": len(tf),
        "cells": {
            "total": len(ti),
            "sample_churn_ok (opens>=5)": int(ti["sample_churn_ok"].sum()),
            "sample_surv_ok (n_cohort>=5)": int(ti["sample_surv_ok"].sum()),
            "sample_full (점포>=5 & cohort>=5)": int(ti["sample_full"].sum()),
        },
        "primary_outcome": "churn_ratio (인허가 폐업/개업, 2022~2025 완전분기; 낮을수록 좋음)",
        "secondary_outcomes": ["surv_1y (코호트 +4Q 생존율)", "net_growth_pct (영업중 20221→20261 %증감)"],
        "fc_coverage_pct_churn_sample": {c: round(100 * ch[c].notna().mean(), 1)
                                         for c in ti.columns if c.startswith("FC")
                                         and not c.endswith("_pctl") and ti[c].dtype != object},
        "outcome_stats": {c: {"n": int(ti[c].notna().sum()),
                              "median": round(float(ti[c].median()), 3) if ti[c].notna().any() else None,
                              "iqr": [round(float(ti[c].quantile(.25)), 3), round(float(ti[c].quantile(.75)), 3)]
                              if ti[c].notna().any() else None} for c in outcome_cols},
        "n_cohort_total": int(ti["n_cohort"].sum()),
        "notes": [
            "primary=churn_ratio(스프레드 큼). surv_1y는 median~90%로 상한 포화 → 보조",
            "인허가 폐업일자=행정처리일 → 실제보다 지연. surv_1y_buf5(+5Q) 병기",
            "매출(rev_pp)은 맥락용 — '성공' 아님 (metric-contract)",
            "FC-03·04·05 계단식 → 수준만. FC-06a/b·20·41·42·52·53 프록시 grain",
            "FC-42 업종검색·FC-41/52/53 자치구 상수 → 업종·상권 내 분위 제한적, pooled 위주 해석",
            "FC-20 임대료: R-ONE join_eligible 상권만 실값(FC20_rent_is_seoul_proxy=0), 나머지 서울지수",
        ],
    }
    json.dump(manifest, open(os.path.join(OUT, "panel_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"panel_trdar_industry.csv  {len(ti)}행")
    print(f"  churn_ok(opens>=5): {int(ti['sample_churn_ok'].sum())}  "
          f"surv_ok(cohort>=5): {int(ti['sample_surv_ok'].sum())}  "
          f"full: {int(ti['sample_full'].sum())}")
    print(f"panel_trdar.csv           {len(tf)}행")
    print("outcome (n / median / IQR):")
    for c in outcome_cols:
        s = manifest["outcome_stats"][c]
        print(f"  {c:16} {s['n']:6}  {s['median']}  {s['iqr']}")
    print("FC 커버리지(churn 표본, %):")
    for c, v in sorted(manifest["fc_coverage_pct_churn_sample"].items()):
        print(f"  {c:26} {v}")


if __name__ == "__main__":
    main()
