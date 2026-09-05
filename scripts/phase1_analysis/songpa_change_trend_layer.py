"""상권변화지표(LL/LH/HL/HH)의 전체 가용 분기(2021Q1~2026Q1, 21개 분기)를 사용해
송파구 상권/행정동이 시간이 지나며 어떻게 분류가 이동했는지 추적한다.

이 스크립트는 다른 분석(아파트-매출 상관관계 등)의 "위에 얹는" 해석 레이어다: 특정
시점의 상관관계만 보면 놓치는 "이 지역 상권이 최근 성장/위축/고착화 중인가"라는
맥락을 보여준다.
"""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
SONGPA_DIR = os.path.join(ROOT, "output", "songpa")
FIG_DIR = os.path.join(ROOT, "output", "figures", "songpa")
os.makedirs(FIG_DIR, exist_ok=True)

QUADRANT_ORDER = ["LL", "LH", "HL", "HH"]
QUADRANT_LABEL = {"LL": "다이나믹(LL)", "LH": "상권확장(LH)", "HL": "상권축소(HL)", "HH": "정체(HH)"}
QUADRANT_COLOR = {"LL": "#2a78d6", "LH": "#eb6834", "HL": "#1baf7a", "HH": "#eda100"}

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#fcfcfb"
GRIDLINE = "#e1e0d9"


def load_trdar_codes():
    return set(pd.read_csv(os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv"),
                            encoding="utf-8-sig")["TRDAR_CD"].astype(int))


def load_dong_codes():
    dong_df = pd.read_csv(os.path.join(DATA, "영역", "행정동", "서울시 상권분석서비스(영역-행정동).csv"), encoding="cp949")
    return set(dong_df.loc[dong_df["행정동_코드"].astype(str).str.startswith("11710"), "행정동_코드"].astype(int))


def load_change_indicator(unit_key, fname, codes):
    path = os.path.join(DATA, "상권변화지표", fname)
    df = pd.read_csv(path, encoding="cp949")
    df = df[df[unit_key].isin(codes)]
    return df[["기준_년분기_코드", unit_key, "상권_변화_지표"]].sort_values(["기준_년분기_코드", unit_key])


def quadrant_share_by_quarter(df):
    counts = df.groupby(["기준_년분기_코드", "상권_변화_지표"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=QUADRANT_ORDER, fill_value=0)
    share = counts.div(counts.sum(axis=1), axis=0)
    return counts, share


def churn_rate(df, unit_key):
    """분기마다 직전 분기 대비 사분면이 바뀐 상권/행정동의 비율."""
    wide = df.pivot(index=unit_key, columns="기준_년분기_코드", values="상권_변화_지표")
    quarters = sorted(wide.columns)
    rates = []
    for prev_q, cur_q in zip(quarters[:-1], quarters[1:]):
        valid = wide[[prev_q, cur_q]].dropna()
        changed = (valid[prev_q] != valid[cur_q]).mean()
        rates.append({"분기": cur_q, "변화율": changed})
    return pd.DataFrame(rates)


def plot_stacked_share(share, title, out_path):
    quarters = share.index.tolist()
    x = range(len(quarters))
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.set_facecolor(SURFACE)
    bottom = np.zeros(len(quarters))
    for q in QUADRANT_ORDER:
        vals = share[q].values * 100
        ax.bar(x, vals, bottom=bottom, color=QUADRANT_COLOR[q], label=QUADRANT_LABEL[q], width=0.7)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(quarters, rotation=45, ha="right", fontsize=8, color=INK_MUTED)
    ax.set_ylabel("비중(%)", color=INK_SECONDARY, fontsize=10)
    ax.set_title(title, color=INK_PRIMARY, fontsize=12)
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    ax.tick_params(colors=INK_MUTED)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"{out_path} 저장")


def main():
    trdar_codes = load_trdar_codes()
    dong_codes = load_dong_codes()

    trdar_ci = load_change_indicator("상권_코드", "서울시 상권분석서비스(상권변화지표-상권).csv", trdar_codes)
    dong_ci = load_change_indicator("행정동_코드", "서울시 상권분석서비스(상권변화지표-행정동).csv", dong_codes)

    quarters = sorted(trdar_ci["기준_년분기_코드"].unique())
    print(f"분석 분기: {quarters[0]} ~ {quarters[-1]} (총 {len(quarters)}개 분기)\n")

    trdar_counts, trdar_share = quadrant_share_by_quarter(trdar_ci)
    dong_counts, dong_share = quadrant_share_by_quarter(dong_ci)

    trdar_counts.to_csv(os.path.join(SONGPA_DIR, "songpa_change_indicator_counts_trdar.csv"), encoding="utf-8-sig")
    dong_counts.to_csv(os.path.join(SONGPA_DIR, "songpa_change_indicator_counts_dong.csv"), encoding="utf-8-sig")
    trdar_share.to_csv(os.path.join(SONGPA_DIR, "songpa_change_indicator_trend_trdar.csv"), encoding="utf-8-sig")
    dong_share.to_csv(os.path.join(SONGPA_DIR, "songpa_change_indicator_trend_dong.csv"), encoding="utf-8-sig")

    print("=== 상권 단위: 분기별 사분면 비중(%) ===")
    print((trdar_share * 100).round(1).to_string())
    print()
    print("=== 행정동 단위: 분기별 사분면 비중(%) ===")
    print((dong_share * 100).round(1).to_string())
    print()

    plot_stacked_share(trdar_share, "송파구 상권: 분기별 상권변화지표 사분면 비중 추이 (2021Q1~2026Q1)",
                        os.path.join(FIG_DIR, "songpa_change_trend_trdar.png"))
    plot_stacked_share(dong_share, "송파구 행정동: 분기별 상권변화지표 사분면 비중 추이 (2021Q1~2026Q1)",
                        os.path.join(FIG_DIR, "songpa_change_trend_dong.png"))

    trdar_churn = churn_rate(trdar_ci, "상권_코드")
    dong_churn = churn_rate(dong_ci, "행정동_코드")
    print("=== 상권 단위: 분기 간 사분면 변화율(%) 평균 ===", f"{trdar_churn['변화율'].mean()*100:.1f}%")
    print("=== 행정동 단위: 분기 간 사분면 변화율(%) 평균 ===", f"{dong_churn['변화율'].mean()*100:.1f}%")

    # 처음(2021Q1)과 최신(2026Q1) 분기 비교 — 개별 상권이 어디서 어디로 이동했는지
    first_q, last_q = quarters[0], quarters[-1]
    wide = trdar_ci.pivot(index="상권_코드", columns="기준_년분기_코드", values="상권_변화_지표")
    transition = wide[[first_q, last_q]].dropna()
    transition_matrix = pd.crosstab(transition[first_q], transition[last_q])
    transition_matrix = transition_matrix.reindex(index=QUADRANT_ORDER, columns=QUADRANT_ORDER, fill_value=0)
    transition_csv = os.path.join(SONGPA_DIR, "songpa_change_indicator_transition_2021_to_2026.csv")
    transition_matrix.to_csv(transition_csv, encoding="utf-8-sig")
    print(f"\n=== 상권 단위: {first_q} -> {last_q} 사분면 이동 행렬 ===")
    print(transition_matrix.to_string())
    print(f"{transition_csv} 저장")


if __name__ == "__main__":
    main()
