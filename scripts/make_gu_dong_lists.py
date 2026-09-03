import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

gu_names = {
    "11110": "종로구",
    "11140": "중구",
    "11170": "용산구",
    "11200": "성동구",
    "11215": "광진구",
    "11230": "동대문구",
    "11260": "중랑구",
    "11290": "성북구",
    "11305": "강북구",
    "11320": "도봉구",
    "11350": "노원구",
    "11380": "은평구",
    "11410": "서대문구",
    "11440": "마포구",
    "11470": "양천구",
    "11500": "강서구",
    "11530": "구로구",
    "11545": "금천구",
    "11560": "영등포구",
    "11590": "동작구",
    "11620": "관악구",
    "11650": "서초구",
    "11680": "강남구",
    "11710": "송파구",
    "11740": "강동구",
}

src = os.path.join(ROOT, "data/영역/행정동/서울시 상권분석서비스(영역-행정동).csv")
out_dir = os.path.join(ROOT, "dong_lists")
os.makedirs(out_dir, exist_ok=True)

by_gu = {code: [] for code in gu_names}

with open(src, encoding="cp949") as f:
    reader = csv.reader(f)
    next(reader)
    for row in reader:
        dong_code, dong_name = row[0], row[1]
        prefix = dong_code[:5]
        if prefix in by_gu:
            by_gu[prefix].append((dong_code, dong_name))

for code, gu in gu_names.items():
    dongs = sorted(by_gu[code], key=lambda x: x[0])
    var_name = {
        "종로구": "jongno", "중구": "junggu", "용산구": "yongsan", "성동구": "seongdong",
        "광진구": "gwangjin", "동대문구": "dongdaemun", "중랑구": "jungnang", "성북구": "seongbuk",
        "강북구": "gangbuk", "도봉구": "dobong", "노원구": "nowon", "은평구": "eunpyeong",
        "서대문구": "seodaemun", "마포구": "mapo", "양천구": "yangcheon", "강서구": "gangseo",
        "구로구": "guro", "금천구": "geumcheon", "영등포구": "yeongdeungpo", "동작구": "dongjak",
        "관악구": "gwanak", "서초구": "seocho", "강남구": "gangnam", "송파구": "songpa",
        "강동구": "gangdong",
    }[gu]
    fname = os.path.join(out_dir, f"{var_name}_dong_list.py")
    with open(fname, "w", encoding="utf-8") as out:
        out.write(f"{var_name}_dong_list = [\n")
        for _, name in dongs:
            out.write(f'    "{name}",\n')
        out.write("]\n")
    print(f"{fname}: {len(dongs)}개")
