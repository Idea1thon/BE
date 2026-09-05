import csv
import importlib.util
import os

var_to_gu = {
    "jongno": "종로구", "junggu": "중구", "yongsan": "용산구", "seongdong": "성동구",
    "gwangjin": "광진구", "dongdaemun": "동대문구", "jungnang": "중랑구", "seongbuk": "성북구",
    "gangbuk": "강북구", "dobong": "도봉구", "nowon": "노원구", "eunpyeong": "은평구",
    "seodaemun": "서대문구", "mapo": "마포구", "yangcheon": "양천구", "gangseo": "강서구",
    "guro": "구로구", "geumcheon": "금천구", "yeongdeungpo": "영등포구", "dongjak": "동작구",
    "gwanak": "관악구", "seocho": "서초구", "gangnam": "강남구", "songpa": "송파구",
    "gangdong": "강동구",
}

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
base = os.path.join(ROOT, "dong_lists")
out_path = os.path.join(ROOT, "seoul_gu_dong_list.csv")

rows = []
for var_name, gu in var_to_gu.items():
    fpath = os.path.join(base, f"{var_name}_dong_list.py")
    spec = importlib.util.spec_from_file_location(var_name, fpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dong_list = getattr(mod, f"{var_name}_dong_list")
    for dong in dong_list:
        rows.append((gu, dong))

with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["자치구", "행정동"])
    writer.writerows(rows)

print(f"{out_path} 작성 완료: {len(rows)}행")
