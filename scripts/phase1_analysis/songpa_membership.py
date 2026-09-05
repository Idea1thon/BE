"""송파구에 속하는 상권/상권배후지 코드를 '겹침 크로스워크' 기준으로 판별.

라벨(SIGNGU_CD_NM) 대신 실제 지오메트리 겹침(crosswalk_trdar_dong.csv,
crosswalk_alley_dong.csv)을 근거로 삼아, 행정동 경계를 걸친 상권/배후지도
누락 없이 포함시킨다.
"""
import csv
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CROSSWALKS = os.path.join(ROOT, "output", "crosswalks")
OUT = os.path.join(ROOT, "output", "songpa")
os.makedirs(OUT, exist_ok=True)

SONGPA_GU_CODE_PREFIX = "11710"


def load_dong_list():
    spec = importlib.util.spec_from_file_location(
        "songpa_dong_list", os.path.join(ROOT, "dong_lists", "songpa_dong_list.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.songpa_dong_list


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_membership(rows, code_field, name_field, ratio_field):
    """행정동 코드가 송파구 접두사인 행만 남기고, 코드별로 겹침 정보를 모은다."""
    songpa_rows = [r for r in rows if r["ADSTRD_CD"].startswith(SONGPA_GU_CODE_PREFIX)]
    by_code = {}
    for r in songpa_rows:
        by_code.setdefault(r[code_field], []).append(r)

    members = []
    for code, group in by_code.items():
        group.sort(key=lambda x: -float(x[ratio_field]))
        best = group[0]
        members.append({
            code_field: code,
            f"{code_field}_NM": best[name_field],
            "matched_dong_count": len(group),
            "dominant_dong_nm": best["ADSTRD_NM"],
            "dominant_ratio": best[ratio_field],
            "matched_dongs": ";".join(f"{g['ADSTRD_NM']}({float(g[ratio_field]):.2f})" for g in group),
        })
    members.sort(key=lambda x: -float(x["dominant_ratio"]))
    return members


def main():
    dong_names = load_dong_list()
    print(f"송파구 행정동 {len(dong_names)}개 (dong_lists/songpa_dong_list.py 기준)\n")

    trdar_dong = read_csv(os.path.join(CROSSWALKS, "crosswalk_trdar_dong.csv"))
    alley_dong = read_csv(os.path.join(CROSSWALKS, "crosswalk_alley_dong.csv"))

    trdar_members = build_membership(trdar_dong, "TRDAR_CD", "TRDAR_CD_NM", "ratio_of_trdar")
    alley_members = build_membership(alley_dong, "ALLEY_TRDA", "ALLEY_TRDA_NM", "ratio_of_alley")

    with open(os.path.join(OUT, "songpa_trdar_membership.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "TRDAR_CD", "TRDAR_CD_NM", "matched_dong_count", "dominant_dong_nm", "dominant_ratio", "matched_dongs",
        ])
        writer.writeheader()
        writer.writerows(trdar_members)

    with open(os.path.join(OUT, "songpa_alley_membership.csv"), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "ALLEY_TRDA", "ALLEY_TRDA_NM", "matched_dong_count", "dominant_dong_nm", "dominant_ratio", "matched_dongs",
        ])
        writer.writeheader()
        writer.writerows(alley_members)

    n_trdar_split = sum(1 for m in trdar_members if m["matched_dong_count"] > 1)
    n_alley_split = sum(1 for m in alley_members if m["matched_dong_count"] > 1)
    print(f"송파구와 겹치는 상권: {len(trdar_members)}개 (이 중 {n_trdar_split}개는 인접 자치구/행정동과도 걸침)")
    print(f"송파구와 겹치는 상권배후지: {len(alley_members)}개 (이 중 {n_alley_split}개는 인접 자치구/행정동과도 걸침)")
    print(f"\n저장 위치: {OUT}/songpa_trdar_membership.csv, songpa_alley_membership.csv")


if __name__ == "__main__":
    main()
