"""상권/행정동을 '신도시형' vs '골목상권형'으로 분류한다.

목적: 상주인구·직장인구를 '입지(상권) 존속 가능성' 신호로 해석할 때, 배후 상권이
성숙(골목형)인지 미성숙(신도시형)인지에 따라 의미가 반대이므로(사용자 가정,
2026-09-03) 그 조건을 먼저 나눈다.

- 신도시형: 대단지 아파트 배후 + 상권 미발달(점포 적음, 프랜차이즈 플라자, 개업
  활발, 영업개월 짧음, 역외 소비). 잠재고객은 많지만 존속 가능성은 불확실.
- 골목상권형: 성숙한 동네 상권(점포 밀도 높음, 독립점, 영업개월 김, 동네 소비
  활발, 고착/정체). 상주인구·직장인구가 많으면 존속 하방 방어 근거.
- 배후주거 희박: 오피스/도심/관광 상권. 상주인구 신호 자체가 무의미 → 별도 분리.

방법: 각 축을 서울 3분위로 나눠 '신도시 방향 / 골목 방향'에 투표하고, 순표 차이
>=3이면 라벨을 붙인다. **가중 합산 점수가 아니라 방향 투표 + 각 축 근거 노출**
(evidence-first). 이 스크립트는 분류 규칙일 뿐 "어느 유형이 낫다"는 판정이 아니며,
그 검증은 2단계(상권 유형별 조건부 폐업률 실측)에서 한다.

환경: pandas / numpy / pyshp. scipy·sklearn 없음. 상권분석 CSV는 cp949, SHP는 utf-8.

산출:
  output/site_typology/typology_{상권,행정동}.csv
  output/site_typology/typology_manifest.json
  output/figures/site_typology/*.png
"""
from __future__ import annotations

import json
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output" / "site_typology"
FIG = ROOT / "output" / "figures" / "site_typology"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

QUARTER = 20261
FOOD_CODES = [f"CS100{i:03d}" for i in range(1, 11)]

# 투표 방향: (축, 신도시 방향이 '상위 분위'인가)
#   True  → 값이 크면 신도시 방향, 작으면 골목 방향
#   False → 값이 크면 골목 방향, 작으면 신도시 방향
# '신도시형'의 본질 = 배후 인구 대비 상권 미성숙(점포 적음·프랜차이즈·최근 생김·역외 소비).
# 성숙도 축(영업개월·개업률·점포수·소비지역성)을 앞에 두고, 아파트 배후 대단지는 보조.
# (아파트_가구_수 컬럼은 서울시 데이터가 전 상권 0이라 사용 불가 → 250m 반경 K-apt로 대체)
AXES = {
    "avg_operating_months": False,     # 운영 영업개월 ↑ = 골목(오래 버팀) — 성숙도 핵심
    "open_rate": True,                 # 개업률 ↑ = 신도시(신규 상권)
    "residents_per_store": True,       # 상주인구당 점포 부족 ↑ = 신도시(상권 미발달)
    "store_density": False,            # 외식 점포 밀도 ↑ = 골목
    "food_sales_per_resident": False,  # 상주인구당 외식매출 ↑ = 골목(동네 소비)
    "franchise_ratio": True,           # 프랜차이즈 비율 ↑ = 신도시(플라자 체인)
    "avg_complex_size": True,          # 반경 250m 평균 단지 규모 ↑ = 신도시 대단지(보조, 상권만)
    "apt_units_250m": True,            # 반경 250m K-apt 세대수 ↑ = 아파트 배후(보조, 상권만)
}
NET_VOTE_MARGIN = 2  # 순표 차이 이상이어야 라벨 확정 (LL/HH 라벨 1표 포함 최대 9표)

# 서울시 상권 구분(TRDAR_SE_1) 중 신도시/골목 판정 대상이 아닌 유형
NON_ALLEY_SEOUL_CLASSES = {"발달상권", "전통시장", "관광특구"}


def read_cp949(name: str, subdir: str) -> pd.DataFrame:
    path = DATA / subdir / name
    return pd.read_csv(path, encoding="cp949")


def load_shp_area(unit: str) -> pd.DataFrame:
    """상권/행정동 면적(㎡) + 이름·자치구. pyshp로 dbf만 읽는다."""
    import shapefile

    shp = glob.glob(str(DATA / "영역" / unit / "*.shp"))[0]
    sf = shapefile.Reader(shp, encoding="utf-8")
    fields = [f[0] for f in sf.fields[1:]]
    rows = []
    for rec in sf.iterRecords():
        d = dict(zip(fields, rec))
        if unit == "상권":
            rows.append({
                "code": str(d["TRDAR_CD"]).strip(),
                "name": str(d["TRDAR_CD_N"]).strip(),
                "sigungu": str(d["SIGNGU_CD_"]).strip(),
                "area_m2": float(d["RELM_AR"] or 0),
                "seoul_trdar_class": str(d["TRDAR_SE_1"]).strip(),
            })
        else:
            rows.append({
                "code": str(d["ADSTRD_CD"]).strip(),
                "name": str(d["ADSTRD_NM"]).strip(),
                "sigungu": "",
                "area_m2": float(d["RELM_AR"] or 0),
                "seoul_trdar_class": "",
            })
    return pd.DataFrame(rows)


def _norm_key(df: pd.DataFrame, key: str) -> pd.DataFrame:
    df = df.copy()
    df[key] = df[key].astype(str).str.strip()
    return df


def build_features(unit: str) -> pd.DataFrame:
    key = "상권_코드" if unit == "상권" else "행정동_코드"

    store = _norm_key(read_cp949(f"서울시 상권분석서비스(점포-{unit}).csv", "점포/2026년"), key)
    store = store[(store["기준_년분기_코드"] == QUARTER) & (store["서비스_업종_코드"].isin(FOOD_CODES))]
    store_agg = store.groupby(key).agg(
        food_store_n=("전체_점포_수", "sum"),
        franchise_n=("프랜차이즈_점포_수", "sum"),
        open_rate=("개업_율", "mean"),
    ).reset_index()
    store_agg["franchise_ratio"] = store_agg["franchise_n"] / store_agg["food_store_n"].replace(0, np.nan)

    sales = _norm_key(read_cp949(f"서울시 상권분석서비스(추정매출-{unit}).csv", "추정매출/2026"), key)
    sales = sales[(sales["기준_년분기_코드"] == QUARTER) & (sales["서비스_업종_코드"].isin(FOOD_CODES))]
    sales_agg = sales.groupby(key).agg(food_sales=("당월_매출_금액", "sum")).reset_index()

    resid = _norm_key(read_cp949(f"서울시 상권분석서비스(상주인구-{unit}).csv", "상주인구"), key)
    resid = resid[resid["기준_년분기_코드"] == QUARTER][
        [key, "총_상주인구_수", "총_가구_수"]
    ].copy()
    # 서울시 상주인구 파일의 아파트_가구_수는 전 상권 0 → 사용 불가. 250m K-apt로 대체(아래).

    change = _norm_key(read_cp949(f"서울시 상권분석서비스(상권변화지표-{unit}).csv", "상권변화지표"), key)
    change = change[change["기준_년분기_코드"] == QUARTER][
        [key, "상권_변화_지표", "운영_영업_개월_평균"]
    ].rename(columns={"상권_변화_지표": "change_label", "운영_영업_개월_평균": "avg_operating_months"})

    area = load_shp_area(unit).rename(columns={"code": key})

    df = area.merge(store_agg, on=key, how="left") \
             .merge(sales_agg, on=key, how="left") \
             .merge(resid, on=key, how="left") \
             .merge(change, on=key, how="left")

    df["store_density"] = df["food_store_n"] / (df["area_m2"] / 1_000_000).replace(0, np.nan)
    df["residents_per_store"] = df["총_상주인구_수"] / df["food_store_n"].replace(0, np.nan)
    df["food_sales_per_resident"] = df["food_sales"] / df["총_상주인구_수"].replace(0, np.nan)

    if unit == "상권":
        apt = pd.read_csv(DATA / "공동주택" / "상권_아파트접근성.csv", encoding="utf-8-sig")
        apt["상권_코드"] = apt["상권_코드"].astype(str).str.strip()
        apt = apt.rename(columns={"250m내_세대수합": "apt_units_250m", "250m내_단지_수": "apt_complex_250m"})
        apt["avg_complex_size"] = apt["apt_units_250m"] / apt["apt_complex_250m"].replace(0, np.nan)
        df = df.merge(apt[["상권_코드", "apt_units_250m", "apt_complex_250m", "avg_complex_size"]], on="상권_코드", how="left")
        df["apt_units_250m"] = df["apt_units_250m"].fillna(0)
        df["avg_complex_size"] = df["avg_complex_size"].fillna(0)
    else:
        df["apt_units_250m"] = np.nan  # 행정동 단지 데이터 없음 → 이 축들 기권
        df["avg_complex_size"] = np.nan

    df = df.rename(columns={key: "code"})
    return df


def classify(df: pd.DataFrame, unit: str) -> tuple[pd.DataFrame, dict]:
    df = df.copy()

    # 0) 신도시/골목 판정 대상 모집단
    #    상권: 서울시 구분이 '골목상권'인 것만(발달상권·전통시장·관광특구는 별도 라벨).
    #    행정동: 서울시 구분이 없으므로 전수(발달+골목 혼재 — 러프 백업).
    if unit == "상권":
        df["classifiable"] = df["seoul_trdar_class"] == "골목상권"
    else:
        df["classifiable"] = True

    pool = df[df["classifiable"]]

    # 1) 배후주거 희박: 모집단 내 상주인구 하위 15% + 반경 250m K-apt 하위 25% → 상주인구 신호 무의미
    #    (행정동은 K-apt 없음 → 상주인구만으로 판정)
    resid_lo = pool["총_상주인구_수"].quantile(0.15)
    apt_series = df["apt_units_250m"] if df["apt_units_250m"].notna().any() else None
    apt_lo = pool["apt_units_250m"].quantile(0.25) if apt_series is not None else None
    thin = df["classifiable"] & (df["총_상주인구_수"] <= resid_lo)
    if apt_series is not None:
        thin = thin & (df["apt_units_250m"] <= apt_lo)
    df["thin_residential"] = thin

    # 2) 축별 3분위 컷 — 판정 대상 모집단(배후주거 희박 제외) 기준으로 고정
    base = pool[~pool.index.isin(df.index[df["thin_residential"]])]
    cuts = {}
    for axis in AXES:
        s = base[axis].dropna()
        if len(s) < 30:
            cuts[axis] = None
            continue
        lo, hi = s.quantile(1 / 3), s.quantile(2 / 3)
        cuts[axis] = (float(lo), float(hi))

    def axis_vote(row) -> tuple[int, int, dict]:
        newtown, alley, detail = 0, 0, {}
        for axis, newtown_is_high in AXES.items():
            val = row[axis]
            c = cuts[axis]
            if c is None or pd.isna(val):
                detail[axis] = None
                continue
            lo, hi = c
            if val >= hi:
                pos = "high"
            elif val <= lo:
                pos = "low"
            else:
                detail[axis] = "mid"
                continue
            is_newtown = (pos == "high") == newtown_is_high
            if is_newtown:
                newtown += 1
                detail[axis] = "newtown"
            else:
                alley += 1
                detail[axis] = "alley"
        # 상권변화지표 라벨: LL=신도시, HH/HL=골목, LH=중립
        lbl = row.get("change_label")
        if lbl == "LL":
            newtown += 1
            detail["change_label"] = "newtown(LL)"
        elif lbl in ("HH", "HL"):
            alley += 1
            detail["change_label"] = f"alley({lbl})"
        else:
            detail["change_label"] = f"mid({lbl})" if isinstance(lbl, str) else None
        return newtown, alley, detail

    votes = df.apply(axis_vote, axis=1, result_type="expand")
    df["newtown_votes"] = votes[0]
    df["alley_votes"] = votes[1]
    df["vote_detail"] = votes[2].apply(lambda d: json.dumps(d, ensure_ascii=False))
    df["net_newtown"] = df["newtown_votes"] - df["alley_votes"]

    def label(row) -> str:
        if not row["classifiable"]:
            return row["seoul_trdar_class"] or "판정대상아님"
        if row["thin_residential"]:
            return "배후주거_희박"
        if pd.isna(row["food_store_n"]) or row["food_store_n"] == 0:
            return "판정보류(데이터부족)"
        if row["net_newtown"] >= NET_VOTE_MARGIN:
            return "신도시형"
        if row["net_newtown"] <= -NET_VOTE_MARGIN:
            return "골목상권형"
        return "혼합형"

    df["typology"] = df.apply(label, axis=1)
    return df, cuts


def make_figures(df: pd.DataFrame, unit: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False

    order = ["골목상권형", "혼합형", "신도시형", "배후주거_희박", "판정보류(데이터부족)",
             "발달상권", "전통시장", "관광특구"]
    colors = {"골목상권형": "#1baf7a", "혼합형": "#9aa0a6", "신도시형": "#2a78d6",
              "배후주거_희박": "#eb6834", "판정보류(데이터부족)": "#d0d0d0",
              "발달상권": "#eda100", "전통시장": "#8a6d3b", "관광특구": "#c85c8e"}

    counts = df["typology"].value_counts().reindex(order).dropna()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(range(len(counts)), counts.values, color=[colors.get(k, "#bbbbbb") for k in counts.index])
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(counts.index, rotation=15)
    ax.set_title(f"{unit} 유형 분포 (20261, n={len(df)})")
    for i, v in enumerate(counts.values):
        ax.text(i, v, f"{int(v)}", ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(FIG / f"typology_dist_{unit}.png", dpi=130)
    plt.close(fig)

    sub = df[df["typology"].isin(["골목상권형", "혼합형", "신도시형"])]
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    for k in ["골목상권형", "혼합형", "신도시형"]:
        s = sub[sub["typology"] == k]
        ax.scatter(s["avg_operating_months"], s["store_density"], s=14, alpha=0.6, label=k, color=colors[k])
    ax.set_xlabel("운영 영업개월 평균 (성숙도)")
    ax.set_ylabel("외식 점포 밀도 (개/㎢)")
    ax.set_yscale("log")
    ax.set_title(f"{unit}: 상권 성숙도 vs 점포 밀도")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG / f"typology_scatter_{unit}.png", dpi=130)
    plt.close(fig)

    if unit == "상권":
        ct = pd.crosstab(df["seoul_trdar_class"], df["typology"])
        ct.to_csv(OUT / "typology_vs_seoul_class_상권.csv", encoding="utf-8-sig")


def run(unit: str) -> dict:
    feats = build_features(unit)
    df, cuts = classify(feats, unit)

    keep = ["code", "name", "sigungu", "seoul_trdar_class", "classifiable", "area_m2",
            "총_상주인구_수", "총_가구_수", "apt_units_250m", "avg_complex_size",
            "food_store_n", "franchise_ratio", "open_rate", "store_density",
            "residents_per_store", "food_sales_per_resident", "avg_operating_months",
            "change_label",
            "thin_residential", "newtown_votes", "alley_votes", "net_newtown",
            "typology", "vote_detail"]
    out = df[[c for c in keep if c in df.columns]].copy()
    for c in out.select_dtypes(include=[float]).columns:
        out[c] = out[c].round(4)
    csv_path = OUT / f"typology_{unit}.csv"
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    make_figures(df, unit)

    dist = df["typology"].value_counts().to_dict()
    return {
        "unit": unit,
        "quarter": QUARTER,
        "rows": len(df),
        "csv": str(csv_path.relative_to(ROOT)),
        "label_distribution": {k: int(v) for k, v in dist.items()},
        "tercile_cuts": cuts,
        "missing": {
            "avg_operating_months_null": int(df["avg_operating_months"].isna().sum()),
            "food_store_n_zero_or_null": int((df["food_store_n"].fillna(0) == 0).sum()),
            "food_sales_per_resident_null": int(df["food_sales_per_resident"].isna().sum()),
            "apt_units_250m_null": int(df["apt_units_250m"].isna().sum()),
        },
        "net_vote_margin": NET_VOTE_MARGIN,
    }


def main() -> int:
    manifest = {
        "generated_for": "미래가치 존속가능성 재정의 1단계 — 상권 유형 분류기",
        "method": f"축별 서울 3분위 → 신도시/골목 방향 투표, 순표차 >={NET_VOTE_MARGIN}이면 라벨. 가중합산 아님.",
        "axes": {a: ("높을수록 신도시" if hi else "높을수록 골목") for a, hi in AXES.items()},
        "change_label_rule": "LL→신도시 1표, HH/HL→골목 1표, LH→중립",
        "limitation": [
            "분류 규칙일 뿐 '어느 유형이 낫다'는 판정이 아님. 검증은 2단계(유형별 조건부 폐업률 실측).",
            "상주인구·아파트 가구는 계단식 데이터 → 수준만 사용, 20261 스냅샷.",
            "상권 SHP는 서울 상권분석 1,650개(서울 면적 27%). 행정동은 425개 전수.",
            "외식 10개 업종(CS100001~010) 기준. 전체 업종 아님.",
            "행정동 단위는 평균 단지 규모(avg_complex_size) 축을 기권한다(단지 데이터 없음).",
        ],
        "units": [],
    }
    for unit in ("상권", "행정동"):
        result = run(unit)
        manifest["units"].append(result)
        print(json.dumps({k: result[k] for k in ("unit", "rows", "label_distribution")}, ensure_ascii=False))

    (OUT / "typology_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"manifest → {OUT / 'typology_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
