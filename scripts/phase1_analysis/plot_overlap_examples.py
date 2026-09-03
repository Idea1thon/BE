import csv
import os

import matplotlib.pyplot as plt
import shapefile
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import shape

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "영역")
CROSSWALKS = os.path.join(ROOT, "output", "crosswalks")
FIG_DIR = os.path.join(ROOT, "output", "figures")
os.makedirs(FIG_DIR, exist_ok=True)

# dataviz 스킬 참조 팔레트 (references/palette.md)
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
CAT = {
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "aqua": "#1baf7a",
    "yellow": "#eda100",
}
SEQ_BLUE = "#2a78d6"


def load_shp(path):
    sf = shapefile.Reader(path, encoding="utf-8")
    fields = [f[0] for f in sf.fields if f[0] != "DeletionFlag"]
    items = []
    for sr in sf.iterShapeRecords():
        geom = shape(sr.shape.__geo_interface__)
        rec = dict(zip(fields, sr.record))
        items.append((geom, rec))
    return items


def add_polygon(ax, geom, facecolor, edgecolor, lw=2.0, alpha=0.55, zorder=1):
    coords = list(geom.exterior.coords)
    patch = MplPolygon(
        coords, closed=True, facecolor=facecolor, edgecolor=edgecolor,
        linewidth=lw, alpha=alpha, zorder=zorder,
    )
    ax.add_patch(patch)


def style_axes(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK_PRIMARY, fontsize=13, pad=12)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_aspect("equal")


def plot_trdar_dong_example():
    """상권 하나가 행정동 경계를 걸치는 사례: 이태원 관광특구 -> 이태원1동/한남동."""
    dong = load_shp(os.path.join(DATA, "행정동", "서울시 상권분석서비스(영역-행정동).shp"))
    trda = load_shp(os.path.join(DATA, "상권", "서울시 상권분석서비스(영역-상권).shp"))

    target_code = "3001491"  # 이태원 관광특구
    tr_geom, tr_rec = next((g, r) for g, r in trda if r["TRDAR_CD"] == target_code)
    dong_codes = {"11170650": ("이태원1동", CAT["blue"]), "11170685": ("한남동", CAT["orange"])}
    dong_geoms = [(g, r) for g, r in dong if r["ADSTRD_CD"] in dong_codes]

    fig, ax = plt.subplots(figsize=(7, 7))
    for g, r in dong_geoms:
        name, color = dong_codes[r["ADSTRD_CD"]]
        add_polygon(ax, g, facecolor=color, edgecolor=color, lw=1.5, alpha=0.35, zorder=1)
        cx, cy = g.centroid.x, g.centroid.y
        ax.text(cx, cy, name, ha="center", va="center", color=INK_SECONDARY, fontsize=10)

    add_polygon(ax, tr_geom, facecolor="none", edgecolor=INK_PRIMARY, lw=2.5, alpha=1.0, zorder=3)
    cx, cy = tr_geom.centroid.x, tr_geom.centroid.y
    ax.text(cx, cy, tr_rec["TRDAR_CD_N"], ha="center", va="center",
             color=INK_PRIMARY, fontsize=11, fontweight="bold", zorder=4)

    minx, miny, maxx, maxy = tr_geom.buffer(150).bounds
    for g, _ in dong_geoms:
        gminx, gminy, gmaxx, gmaxy = g.bounds
        minx, miny = min(minx, gminx), min(miny, gminy)
        maxx, maxy = max(maxx, gmaxx), max(maxy, gmaxy)
    ax.set_xlim(minx - 100, maxx + 100)
    ax.set_ylim(miny - 100, maxy + 100)

    style_axes(ax, "상권↔행정동 겹침 예시: 이태원 관광특구 (71.9% 이태원1동 / 28.1% 한남동)")
    fig.tight_layout()
    out = os.path.join(FIG_DIR, "example_trdar_dong_overlap.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


def plot_alley_self_overlap_example():
    """배후지 자기겹침 사례: 경리단길남측/북측/이태원역 북측."""
    back = load_shp(os.path.join(DATA, "상권배후지", "서울시 상권분석서비스(영역-상권배후지).shp"))
    codes = {
        "3110085": ("경리단길남측", CAT["blue"]),
        "3110084": ("경리단길북측", CAT["orange"]),
        "3110086": ("이태원역 북측", CAT["aqua"]),
    }
    geoms = [(g, r) for g, r in back if r["ALLEY_TRDA"] in codes]

    fig, ax = plt.subplots(figsize=(7, 7))
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for g, r in geoms:
        name, color = codes[r["ALLEY_TRDA"]]
        add_polygon(ax, g, facecolor=color, edgecolor=color, lw=1.8, alpha=0.4, zorder=1)
        cx, cy = g.centroid.x, g.centroid.y
        ax.text(cx, cy, name, ha="center", va="center", color=INK_PRIMARY, fontsize=10, fontweight="bold")
        gminx, gminy, gmaxx, gmaxy = g.bounds
        minx, miny = min(minx, gminx), min(miny, gminy)
        maxx, maxy = max(maxx, gmaxx), max(maxy, gmaxy)

    ax.set_xlim(minx - 100, maxx + 100)
    ax.set_ylim(miny - 100, maxy + 100)
    style_axes(ax, "상권배후지 자기겹침 예시: 경리단길남/북측·이태원역북측 (최대 65% 중첩)")
    fig.tight_layout()
    out = os.path.join(FIG_DIR, "example_alley_self_overlap.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


def plot_ratio_histogram():
    """상권별 '최대 면적 비율로 속하는 행정동' 비율 분포 + 60% 기준선."""
    path = os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv")
    by_trdar = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["TRDAR_CD"]
            ratio = float(row["ratio_of_trdar"])
            if code not in by_trdar or ratio > by_trdar[code]:
                by_trdar[code] = ratio
    ratios = list(by_trdar.values())

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.set_facecolor(SURFACE)
    n, bins, patches = ax.hist(
        ratios, bins=25, range=(0, 1), color=SEQ_BLUE, edgecolor=SURFACE, linewidth=1.2,
    )
    ax.axvline(0.6, color=CAT["orange"], linewidth=2, linestyle="--")
    ax.text(0.605, ax.get_ylim()[1] * 0.92, "60% 기준선", color=CAT["orange"], fontsize=10)

    n_below_60 = sum(1 for r in ratios if r < 0.6)
    ax.text(
        0.02, ax.get_ylim()[1] * 0.92,
        f"60% 미만: {n_below_60}개 상권 ({n_below_60/len(ratios)*100:.1f}%)",
        color=INK_SECONDARY, fontsize=10,
    )

    ax.set_xlabel("상권 면적 중 최대 비중 행정동이 차지하는 비율", color=INK_SECONDARY, fontsize=10)
    ax.set_ylabel("상권 수", color=INK_SECONDARY, fontsize=10)
    ax.set_title(f"상권({len(ratios)}개)별 최대 행정동 귀속 비율 분포", color=INK_PRIMARY, fontsize=13)
    ax.tick_params(colors=INK_MUTED)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)

    fig.tight_layout()
    out = os.path.join(FIG_DIR, "ratio_histogram_trdar_dong.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


def plot_summary_bar():
    """1,650개 상권을 행정동 귀속 안정성 기준 3개 그룹으로 분류한 요약 막대."""
    path = os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv")
    by_trdar = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["TRDAR_CD"]
            ratio = float(row["ratio_of_trdar"])
            if code not in by_trdar or ratio > by_trdar[code]:
                by_trdar[code] = ratio

    single = sum(1 for r in by_trdar.values() if r >= 0.999)
    clear_majority = sum(1 for r in by_trdar.values() if 0.6 <= r < 0.999)
    ambiguous = sum(1 for r in by_trdar.values() if r < 0.6)

    labels = ["단일 행정동\n(겹침 없음)", "60%↑ 다수결\n귀속 가능", "60%↓ 애매\n(가중분배 필요)"]
    values = [single, clear_majority, ambiguous]
    colors = [CAT["aqua"], CAT["blue"], CAT["orange"]]

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.set_facecolor(SURFACE)
    bars = ax.bar(labels, values, color=colors, width=0.6)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 15, f"{v}개\n({v/len(by_trdar)*100:.1f}%)",
                ha="center", va="bottom", color=INK_PRIMARY, fontsize=10)

    ax.set_ylabel("상권 수", color=INK_SECONDARY, fontsize=10)
    ax.set_title(f"상권({len(by_trdar)}개) 행정동 귀속 유형 분류", color=INK_PRIMARY, fontsize=13)
    ax.tick_params(colors=INK_MUTED)
    ax.set_ylim(0, max(values) * 1.25)
    ax.grid(axis="y", color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(GRIDLINE)

    fig.tight_layout()
    out = os.path.join(FIG_DIR, "summary_trdar_dong_classification.png")
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    print(f"{out} 저장")


if __name__ == "__main__":
    plot_trdar_dong_example()
    plot_alley_self_overlap_example()
    plot_ratio_histogram()
    plot_summary_bar()
