"""'유동인구 00~06시 비중이 24.6%로 높은 건 사실 상주인구(취침 중 거주자)가 잡히는
것 아닌가?'라는 가설을 검증.

방법:
1. 상권별 (상주인구 총량, 직장인구 총량) vs (유동인구 6개 시간대 값)의 상관관계를 각각 계산.
   -> 00~06시 유동인구가 상주인구와 가장 강하게 상관되고, 낮 시간대 유동인구가 직장인구와
      가장 강하게 상관된다면 가설이 지지됨.
2. 상권별 (00~06시 유동인구 / 상주인구) 비율의 분포를 확인.
   -> 비율이 1 근처에 몰려 있으면 "그 시간대 유동인구 ≈ 그 지역 상주인구"라는 뜻이 되어
      가설이 더 강하게 지지됨.
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

QUARTER = 20261
TIME_BINS = ["00~06", "06~11", "11~14", "14~17", "17~21", "21~24"]
FLOW_COLS = [f"시간대_{b.replace('~', '_')}_유동인구_수" for b in TIME_BINS]

INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
SURFACE = "#fcfcfb"
GRIDLINE = "#e1e0d9"
BLUE = "#2a78d6"
ORANGE = "#eb6834"


def load_songpa_trdar_codes():
    df = pd.read_csv(os.path.join(SONGPA_DIR, "songpa_trdar_membership.csv"), encoding="utf-8-sig")
    return set(df["TRDAR_CD"].astype(int))


def load(folder, fname, cols):
    path = os.path.join(DATA, folder, fname)
    df = pd.read_csv(path, encoding="cp949")
    df = df[df["기준_년분기_코드"] == QUARTER]
    return df.set_index("상권_코드")[cols]


def main():
    trdar_codes = load_songpa_trdar_codes()

    resident = load("상주인구", "서울시 상권분석서비스(상주인구-상권).csv", ["총_상주인구_수"])
    worker = load("직장인구", "서울시 상권분석서비스(직장인구-상권).csv", ["총_직장_인구_수"])
    flow = load("길단위인구", "서울시 상권분석서비스(길단위인구-상권).csv", FLOW_COLS + ["총_유동인구_수"])

    df = resident.join(worker, how="outer").join(flow, how="outer")
    df = df.loc[df.index.isin(trdar_codes)].fillna(0)
    print(f"분석 대상 송파구 상권 수: {len(df)}개\n")

    # 1) 상관관계: 상주인구/직장인구 vs 시간대별 유동인구
    log_df = np.log1p(df)
    corr_resident = [log_df["총_상주인구_수"].corr(log_df[c]) for c in FLOW_COLS]
    corr_worker = [log_df["총_직장_인구_수"].corr(log_df[c]) for c in FLOW_COLS]

    corr_table = pd.DataFrame({"시간대": TIME_BINS, "상주인구와_상관계수": corr_resident, "직장인구와_상관계수": corr_worker})
    out_csv = os.path.join(SONGPA_DIR, "songpa_flow_resident_worker_correlation.csv")
    corr_table.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print("=== 시간대별 유동인구와 상주인구/직장인구의 상관계수 (log1p, Pearson) ===")
    print(corr_table.round(3).to_string(index=False))

    # 2) 00~06시 유동인구 / 상주인구 비율 분포
    ratio_0006 = df["시간대_00_06_유동인구_수"] / df["총_상주인구_수"].replace(0, np.nan)
    ratio_1114 = df["시간대_11_14_유동인구_수"] / df["총_직장_인구_수"].replace(0, np.nan)
    print(f"\n00~06시 유동인구 / 상주인구 비율: 중앙값 {ratio_0006.median():.2f}, 평균 {ratio_0006.mean():.2f}")
    print(f"11~14시 유동인구 / 직장인구 비율: 중앙값 {ratio_1114.median():.2f}, 평균 {ratio_1114.mean():.2f}")
    print(f"\n송파구 합계 - 총 상주인구: {df['총_상주인구_수'].sum():,.0f} / 00~06시 유동인구 합계: {df['시간대_00_06_유동인구_수'].sum():,.0f}")
    print(f"송파구 합계 - 총 직장인구: {df['총_직장_인구_수'].sum():,.0f} / 11~14시 유동인구 합계: {df['시간대_11_14_유동인구_수'].sum():,.0f}")

    # === 시각화 1: 시간대별 상관계수 막대그래프 ===
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.set_facecolor(SURFACE)
    x = np.arange(len(TIME_BINS))
    width = 0.35
    ax.bar(x - width / 2, corr_resident, width, color=BLUE, label="상주인구와 상관계수")
    ax.bar(x + width / 2, corr_worker, width, color=ORANGE, label="직장인구와 상관계수")
    ax.set_xticks(x)
    ax.set_xticklabels(TIME_BINS, color=INK_SECONDARY)
    ax.set_ylabel("Pearson r (log1p)", color=INK_SECONDARY, fontsize=10)
    ax.set_title("시간대별 유동인구가 상주인구·직장인구 중 무엇과 더 닮았는가 (송파구)", color=INK_PRIMARY, fontsize=12)
    ax.axhline(0, color=GRIDLINE, linewidth=1)
    ax.tick_params(colors=INK_MUTED)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)
    fig.tight_layout()
    out1 = os.path.join(FIG_DIR, "songpa_flow_vs_resident_worker_corr.png")
    fig.savefig(out1, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"\n{out1} 저장")

    # === 시각화 2: 00~06 유동인구 vs 상주인구 산점도 (y=x 참조선 포함) ===
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))

    ax = axes[0]
    ax.set_facecolor(SURFACE)
    lx, ly = log_df["총_상주인구_수"], log_df["시간대_00_06_유동인구_수"]
    ax.scatter(lx, ly, color=BLUE, alpha=0.7, s=32, edgecolor=SURFACE, linewidth=0.6)
    lims = [min(lx.min(), ly.min()), max(lx.max(), ly.max())]
    ax.plot(lims, lims, color=INK_MUTED, linestyle="--", linewidth=1.5, label="y = x (1:1 기준선)")
    ax.set_xlabel("log(1+총 상주인구)", color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("log(1+00~06시 유동인구)", color=INK_SECONDARY, fontsize=10)
    ax.set_title(f"00~06시 유동인구 vs 상주인구 (r={log_df['총_상주인구_수'].corr(log_df['시간대_00_06_유동인구_수']):.2f})",
                 color=INK_PRIMARY, fontsize=11)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    ax.tick_params(colors=INK_MUTED, labelsize=8)
    ax.grid(color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)

    ax = axes[1]
    ax.set_facecolor(SURFACE)
    lx, ly = log_df["총_직장_인구_수"], log_df["시간대_11_14_유동인구_수"]
    ax.scatter(lx, ly, color=ORANGE, alpha=0.7, s=32, edgecolor=SURFACE, linewidth=0.6)
    lims = [min(lx.min(), ly.min()), max(lx.max(), ly.max())]
    ax.plot(lims, lims, color=INK_MUTED, linestyle="--", linewidth=1.5, label="y = x (1:1 기준선)")
    ax.set_xlabel("log(1+총 직장인구)", color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("log(1+11~14시 유동인구)", color=INK_SECONDARY, fontsize=10)
    ax.set_title(f"11~14시 유동인구 vs 직장인구 (r={log_df['총_직장_인구_수'].corr(log_df['시간대_11_14_유동인구_수']):.2f})",
                 color=INK_PRIMARY, fontsize=11)
    ax.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9)
    ax.tick_params(colors=INK_MUTED, labelsize=8)
    ax.grid(color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)

    fig.suptitle("송파구: 시간대별 유동인구가 상주/직장인구 규모와 얼마나 일치하는가", color=INK_PRIMARY, fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out2 = os.path.join(FIG_DIR, "songpa_flow_resident_worker_scatter.png")
    fig.savefig(out2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out2} 저장")


if __name__ == "__main__":
    main()
