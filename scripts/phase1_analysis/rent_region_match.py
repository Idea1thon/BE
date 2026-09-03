import re
import shapefile
from shapely.geometry import shape as shp_shape
from shapely.strtree import STRtree
import pandas as pd

def load(path):
    sf = shapefile.Reader(path)
    fields = [f[0] for f in sf.fields[1:]]
    recs, geoms = [], []
    for sr in sf.shapeRecords():
        recs.append(dict(zip(fields, sr.record)))
        geoms.append(shp_shape(sr.shape.__geo_interface__))
    return recs, geoms

dong_recs, dong_geoms = load('data/영역/행정동/서울시 상권분석서비스(영역-행정동).shp')
sg_recs, sg_geoms     = load('data/영역/상권/서울시 상권분석서비스(영역-상권).shp')
bg_recs, bg_geoms     = load('data/영역/상권배후지/서울시 상권분석서비스(영역-상권배후지).shp')

dong_tree = STRtree(dong_geoms)
bg_tree = STRtree(bg_geoms)

rent = pd.read_csv('data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv', encoding='utf-8-sig', skiprows=2, header=None, usecols=[0, 1])
rent.columns = ['대분류', '지역']
rent_names = sorted(set(rent['지역'].dropna()) - {'소계', '지역별(2)'})

EXCLUDED = {'강남대로', '도산대로', '양재말죽거리', '테헤란로', '숙명여대'}

SUFFIXES = ['입구역', '사거리', '역', '동', '시장', '지역']

def base_form(token):
    for suf in SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf):
            return token[: -len(suf)]
    return token

def tokens(name):
    parts = re.split(r'[/·,]', name)
    return [p.strip() for p in parts if p.strip()]

def candidates(name):
    idxs = set()
    for t in tokens(name):
        base = base_form(t)
        for i, r in enumerate(sg_recs):
            nm = r['TRDAR_CD_N']
            if nm.startswith(t) or nm.startswith(base):
                idxs.add(i)
    return sorted(idxs, key=lambda i: -sg_recs[i]['RELM_AR'])

def pick_representative(idxs):
    """iterative 60:40 rule: merge next-largest candidate while its share stays >= 40%"""
    if not idxs:
        return [], None, None
    selected = [idxs[0]]
    running_area = sg_recs[idxs[0]]['RELM_AR']
    for i in idxs[1:]:
        a = sg_recs[i]['RELM_AR']
        share = a / (running_area + a)
        if share >= 0.4:
            selected.append(i)
            running_area += a
        else:
            break
    # area-weighted representative coordinate
    total = sum(sg_recs[i]['RELM_AR'] for i in selected)
    x = sum(sg_recs[i]['XCNTS_VALU'] * sg_recs[i]['RELM_AR'] for i in selected) / total
    y = sum(sg_recs[i]['YDNTS_VALU'] * sg_recs[i]['RELM_AR'] for i in selected) / total
    return selected, x, y

def contain_dong(x, y):
    from shapely.geometry import Point
    p = Point(x, y)
    for i in dong_tree.query(p):
        if dong_geoms[i].contains(p):
            return dong_recs[i]['ADSTRD_NM'], dong_recs[i]['ADSTRD_CD']
    # fallback: nearest
    return None, None

def contain_bg(x, y):
    from shapely.geometry import Point
    p = Point(x, y)
    for i in bg_tree.query(p):
        if bg_geoms[i].contains(p):
            return bg_recs[i]['ALLEY_TR_1'], bg_recs[i]['ALLEY_TRDA']
    return None, None

rows = []
for name in rent_names:
    if name in EXCLUDED:
        continue
    idxs = candidates(name)
    if not idxs:
        rows.append((name, '매칭없음', None, None, None, None, None, None, None, None))
        continue
    sel, x, y = pick_representative(idxs)
    sel_names = [sg_recs[i]['TRDAR_CD_N'] for i in sel]
    mode = '합집합' if len(sel) > 1 else '단일'
    dong_nm, dong_cd = contain_dong(x, y)
    bg_nm, bg_cd = contain_bg(x, y)
    # ' | '로 join: 상권_코드_명 자체에 ', '가 포함된 경우(예: "신촌역(신촌역, 신촌로터리)")가 있어
    # ', '를 구분자로 쓰면 다운스트림(rent_store_correlation.py)에서 잘못 분리됨 -> 충돌 없는 구분자로 교체
    # 소속_행정동_코드/소속_상권배후지_코드: 추정매출-행정동.csv/추정매출-상권배후지.csv(cp949)의
    # 이름 컬럼이 '·' 등 특수문자에서 인코딩이 깨져 있는 경우가 있어(예: 종로1·2·3·4가동 -> ?로 깨짐,
    # 11-1b와 동일한 문제) 이름이 아닌 코드로 조인해야 함 -> shapefile에서 코드도 함께 뽑아둔다.
    rows.append((name, mode, ' | '.join(sel_names), len(idxs), x, y,
                 dong_nm, int(dong_cd) if dong_cd else None,
                 bg_nm, int(bg_cd) if bg_cd else None))

out = pd.DataFrame(rows, columns=['임대료_지역', '대표결정방식', '선택된_상권', '전체후보수', 'X', 'Y',
                                   '소속_행정동', '소속_행정동_코드', '소속_상권배후지', '소속_상권배후지_코드'])
out.to_csv('output/rent_region_representative.csv', index=False, encoding='utf-8-sig')
print(out.to_string())
print()
print('매칭없음(0건) 개수:', (out['대표결정방식']=='매칭없음').sum())
print('단일 대표:', (out['대표결정방식']=='단일').sum())
print('합집합 대표:', (out['대표결정방식']=='합집합').sum())
