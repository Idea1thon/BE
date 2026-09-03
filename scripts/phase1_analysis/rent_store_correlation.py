"""
겹치는 영역(output/rent_region_representative.csv, 63개)에 한정하여
매장용빌딩 임대료·공실률·수익률 데이터와 점포 데이터(업종별 점포수/개업율/폐업율)의
상관관계를 계산한다.

공통분기: 임대료 데이터(2021Q1~2025Q4, 20개 분기) x 점포 데이터(2021Q1~2026Q1, 21개 분기)
교집합 = 20211~20254 (20개 분기, 임대료 데이터 범위 전체) — 2026-08-07 데이터 갱신으로
점포 데이터가 2021년까지 확장되면서 기존 4개 분기(2025Q1~2025Q4)에서 20개 분기로 늘어남.
"""
import re
import pandas as pd
import numpy as np
import shapefile

FOOD_CODES = {
    'CS100001': '한식음식점', 'CS100002': '중식음식점', 'CS100003': '일식음식점',
    'CS100004': '양식음식점', 'CS100005': '제과점', 'CS100006': '패스트푸드점',
    'CS100007': '치킨전문점', 'CS100008': '분식전문점', 'CS100009': '호프-간이주점',
    'CS100010': '커피-음료',
}

# 점포 데이터: data/점포/ 밑이 연도별 폴더로 재구성됨. 연도별로 스키마가 제각각이라
# (컬럼명 한글/영문, '점포_수' vs '전체_점포_수' 등) 파일별로 인코딩·컬럼 매핑을 지정해 통일한다.
# 2026년 폴더 파일은 20251~20254(2025년 폴더와 중복)와 20261을 모두 포함하므로,
# 2025년 폴더 파일은 건너뛰고 2026년 폴더 파일 하나로 20251~20261을 커버한다.
STORE_FILES = [
    ('data/점포/2021년/서울시_상권분석서비스(점포-상권)_2021년.csv', 'cp949'),
    ('data/점포/2022년/서울시_상권분석서비스(점포-상권)_2022년.csv', 'cp949'),
    ('data/점포/2023년/서울시_상권분석서비스(점포-상권)_2023년.csv', 'cp949'),
    ('data/점포/2024년/서울시 상권분석서비스(점포-상권)_2024년.csv', 'cp949'),
    ('data/점포/2026년/서울시 상권분석서비스(점포-상권).csv', 'cp949'),
]
COLUMN_ALIASES = {
    'stdr_yyqu_cd': '기준_년분기_코드', 'trdar_cd': '상권_코드',
    'svc_induty_cd': '서비스_업종_코드', 'svc_induty_cd_nm': '서비스_업종_코드_명',
    # 2026-08-09 수정: 2021~2024년 파일의 '점포_수'는 프랜차이즈 점포를 제외한 값이고,
    # '유사_업종_점포_수'가 '점포_수'+'프랜차이즈_점포_수'와 정확히 일치하는(4개 연도 전수 검증,
    # 100% 일치, 평균 7.5~7.7% 과소) 진짜 "전체" 컬럼이다. 2025~2026년 파일의 '전체_점포_수'
    # (=일반_점포_수+프랜차이즈_점포_수)와 개념이 같은 건 '점포_수'가 아니라 '유사_업종_점포_수'.
    # 예전엔 '점포_수'를 '전체_점포_수'로 잘못 alias해 2021~2024년 구간(20개 분기 중 16개)이
    # 체계적으로 과소산정됐음 -- 상세: docs/data_result_2.md 11-1d.
    'stor_co': '전체_점포_수', '유사_업종_점포_수': '전체_점포_수',
    'frc_stor_co': '프랜차이즈_점포_수', 'opbiz_rt': '개업_율', 'opbiz_stor_co': '개업_점포_수',
    'clsbiz_rt': '폐업_률', 'clsbiz_stor_co': '폐업_점포_수',
}
STORE_COLS = ['기준_년분기_코드', '상권_코드', '서비스_업종_코드', '서비스_업종_코드_명',
              '전체_점포_수', '프랜차이즈_점포_수', '개업_점포_수', '폐업_점포_수']

# ---------- 1. 겹치는 영역 63개 -> 상권_코드 매핑 ----------
rep = pd.read_csv('output/rent_region_representative.csv', encoding='utf-8-sig')

# 상권_코드_명은 shapefile(.shp/.dbf)에서 가져온다 — 같은 이름을 담은
# data/영역/상권/서울시 상권분석서비스(영역-상권).csv는 원본 자체가 손상되어 있어
# (예: '종로·청계 관광특구'의 '·'가 파일 안에 문자 그대로 '?'로 깨져 저장됨) 이름 매칭에 쓰면 안 됨.
# rent_region_match.py가 '선택된_상권'을 만들 때도 이 shapefile을 소스로 썼으므로 동일 소스를 써야
# 인코딩이 항상 일치한다.
sf = shapefile.Reader('data/영역/상권/서울시 상권분석서비스(영역-상권).shp')
sf_fields = [f[0] for f in sf.fields[1:]]
name2code = {}
for sr in sf.shapeRecords():
    rec = dict(zip(sf_fields, sr.record))
    name2code[rec['TRDAR_CD_N']] = int(rec['TRDAR_CD'])

region_codes = []  # (임대료_지역, 상권_코드)
unmatched = []
for _, row in rep.iterrows():
    # ' | '로 분리: 상권_코드_명 자체에 ', '가 포함된 경우(예: "신촌역(신촌역, 신촌로터리)",
    # "신림역 5번(신림동주민센터, 신림동별빛거리)")가 있어 ', '를 구분자로 쓰면 이름이 잘못 쪼개짐
    for nm in str(row['선택된_상권']).split(' | '):
        code = name2code.get(nm)
        if code is not None:
            region_codes.append((row['임대료_지역'], code))
        else:
            unmatched.append((row['임대료_지역'], nm))
region_codes = pd.DataFrame(region_codes, columns=['임대료_지역', '상권_코드'])
if unmatched:
    print(f"[1] 경고: 상권_코드 매칭 실패 {len(unmatched)}건 -> {unmatched}")
print(f"[1] 겹치는 영역 {rep.shape[0]}개 -> 상권_코드 {region_codes['상권_코드'].nunique()}개 매핑")

# ---------- 2. 점포 데이터: 연도별 파일 통합 (임대료 QUARTERS는 이 결과 확인 후 정함) ----------
store_parts = []
for path, enc in STORE_FILES:
    df = pd.read_csv(path, encoding=enc)
    df = df.rename(columns=COLUMN_ALIASES)
    df = df[df['서비스_업종_코드'].isin(FOOD_CODES)]
    df = df[df['상권_코드'].astype(int).isin(region_codes['상권_코드'])]
    store_parts.append(df[STORE_COLS])
store = pd.concat(store_parts, ignore_index=True)
store = store.drop_duplicates(subset=['기준_년분기_코드', '상권_코드', '서비스_업종_코드'])
store_quarters = sorted(store['기준_년분기_코드'].unique())
print(f"[2] 점포 데이터 통합: {store.shape[0]}행, 분기 {store_quarters[0]}~{store_quarters[-1]} ({len(store_quarters)}개)")

# ---------- 3. 임대료 데이터: wide -> long ----------
raw = pd.read_csv('data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv', encoding='utf-8-sig', header=None)
region_col = raw.iloc[2:, 1].reset_index(drop=True)
metric_names = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
quarter_labels = raw.iloc[0, 2:].tolist()  # '2021 1/4' 반복 5번씩
unique_quarters = []
seen = set()
for q in quarter_labels:
    if q not in seen:
        unique_quarters.append(q)
        seen.add(q)

data = raw.iloc[2:, 2:].reset_index(drop=True)
data.columns = pd.MultiIndex.from_product([unique_quarters, metric_names])

# 공통분기 = 임대료 전체 분기 x 점포 전체 분기 교집합
rent_qcodes = []
for q in unique_quarters:
    y, qtr = re.match(r'(\d{4}) (\d)/4', q).groups()
    rent_qcodes.append((q, int(y) * 10 + int(qtr)))
QUARTERS = sorted(set(q for _, q in rent_qcodes) & set(store_quarters))
print(f"[3] 공통분기: {QUARTERS[0]}~{QUARTERS[-1]} ({len(QUARTERS)}개)")

long_rows = []
for q, qcode in rent_qcodes:
    if qcode not in QUARTERS:
        continue
    block = data[q].copy()
    block.insert(0, '임대료_지역', region_col)
    block.insert(1, '기준_년분기_코드', qcode)
    long_rows.append(block)

rent_long = pd.concat(long_rows, ignore_index=True)
for m in metric_names:
    rent_long[m] = pd.to_numeric(rent_long[m].replace('-', np.nan), errors='coerce')
rent_long = rent_long[rent_long['임대료_지역'].isin(rep['임대료_지역'])]
print(f"[3] 임대료 롱포맷: {rent_long.shape[0]}행 (지역 {rent_long['임대료_지역'].nunique()}개 x 분기 {len(QUARTERS)}개)")

# ---------- 4. 점포 데이터: 상권_코드 -> 임대료_지역 집계 ----------
store = store[store['기준_년분기_코드'].isin(QUARTERS)]
store['상권_코드'] = store['상권_코드'].astype(int)

# 상권_코드 -> 임대료_지역 (합집합인 경우 여러 코드가 같은 지역으로 매핑됨)
store = store.merge(region_codes, on='상권_코드', how='inner')

agg = store.groupby(['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명']).agg(
    전체_점포_수=('전체_점포_수', 'sum'),
    프랜차이즈_점포_수=('프랜차이즈_점포_수', 'sum'),
    개업_점포_수=('개업_점포_수', 'sum'),
    폐업_점포_수=('폐업_점포_수', 'sum'),
).reset_index()
agg['개업_율'] = np.where(agg['전체_점포_수'] > 0, agg['개업_점포_수'] / agg['전체_점포_수'] * 100, 0)
agg['폐업_률'] = np.where(agg['전체_점포_수'] > 0, agg['폐업_점포_수'] / agg['전체_점포_수'] * 100, 0)
agg['프랜차이즈_비율'] = np.where(agg['전체_점포_수'] > 0, agg['프랜차이즈_점포_수'] / agg['전체_점포_수'] * 100, 0)
print(f"[4] 점포 집계: {agg.shape[0]}행 (지역x분기x업종)")

# 지역x분기x업종 풀 격자 생성 후 없는 조합은 0으로 채움 (그 업종 점포가 아예 없었다는 뜻)
# 주의: 전체_점포_수=0인 행이 전부 이 격자 채움 때문은 아님 — 서울시 원본 점포 데이터 자체에도
# 점포_수=0인데 프랜차이즈_점포_수>0인 모순 행이 존재함(예: 가락시장 2021Q1 제과점: 점포_수=0,
# 프랜차이즈_점포_수=2). 파이프라인 버그가 아니라 원자료의 특이값이며, 이 케이스는 아래 프랜차이즈_비율
# 계산에서 분모가 0이라 0으로 떨어져 실제 프랜차이즈 존재를 과소 반영한다. 상세: docs/PROGRESS.md 참고.
regions = rep['임대료_지역'].unique()
grid = pd.MultiIndex.from_product([regions, QUARTERS, FOOD_CODES.keys()],
                                   names=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드']).to_frame(index=False)
grid['서비스_업종_코드_명'] = grid['서비스_업종_코드'].map(FOOD_CODES)
agg_full = grid.merge(agg, on=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명'], how='left')
for c in ['전체_점포_수', '프랜차이즈_점포_수', '개업_점포_수', '폐업_점포_수', '개업_율', '폐업_률', '프랜차이즈_비율']:
    agg_full[c] = agg_full[c].fillna(0)

# ---------- 5. 병합 (broadcast: 임대료 값 1개가 업종 10개 행에 복제) ----------
panel = agg_full.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
panel.to_csv('output/rent_store_panel.csv', index=False, encoding='utf-8-sig')
print(f"[5] 결합 패널: {panel.shape[0]}행 -> output/rent_store_panel.csv")

# ---------- 5b. 지역x분기 단위로 업종 10개 합산 (유사반복pseudo-replication 검증용 독립표본) ----------
count_cols = ['전체_점포_수', '개업_점포_수', '폐업_점포_수', '프랜차이즈_점포_수']
region_quarter = panel.groupby(['임대료_지역', '기준_년분기_코드'])[count_cols].sum().reset_index()
region_quarter = region_quarter.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='left')
region_quarter['개업_율'] = np.where(region_quarter['전체_점포_수'] > 0,
                                   region_quarter['개업_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
region_quarter['폐업_률'] = np.where(region_quarter['전체_점포_수'] > 0,
                                   region_quarter['폐업_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
region_quarter['프랜차이즈_비율'] = np.where(region_quarter['전체_점포_수'] > 0,
                                        region_quarter['프랜차이즈_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
region_quarter.to_csv('output/rent_store_region_quarter.csv', index=False, encoding='utf-8-sig')
print(f"[5b] 지역x분기 독립표본: {region_quarter.shape[0]}행 -> output/rent_store_region_quarter.csv")

# ---------- 6. 상관관계 계산 ----------
rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
store_metrics = ['전체_점포_수', '개업_율', '폐업_률', '프랜차이즈_비율']

def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()

# 6-1. 전체 풀링 상관관계 (log1p: 임대료/전체_점포_수만, 비율지표는 원값)
pooled = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
n_used = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
for rm in rent_metrics:
    log_r = rm == '임대료'
    for sm in store_metrics:
        log_s = sm == '전체_점포_수'
        r, n = corr(panel[rm], panel[sm], log_r, log_s)
        pooled.loc[rm, sm] = r
        n_used.loc[rm, sm] = n
pooled.to_csv('output/rent_store_correlation_pooled.csv', encoding='utf-8-sig')
print('\n[6-1] 전체 풀링 상관계수 (log1p: 임대료, 전체_점포_수):')
print(pooled.round(3))
print('\n표본수(n):')
print(n_used.astype(int))

# 6-2. 업종별 breakdown: 임대료 vs 각 store_metric
by_biz = pd.DataFrame(index=list(FOOD_CODES.values()), columns=store_metrics, dtype=float)
for code, name in FOOD_CODES.items():
    sub = panel[panel['서비스_업종_코드'] == code]
    for sm in store_metrics:
        log_s = sm == '전체_점포_수'
        r, n = corr(sub['임대료'], sub[sm], True, log_s)
        by_biz.loc[name, sm] = r
by_biz.to_csv('output/rent_store_correlation_by_industry.csv', encoding='utf-8-sig')
print('\n[6-2] 업종별 임대료 상관계수:')
print(by_biz.round(3))

# 6-3. 지역x분기 독립표본(유사반복 검증)
rq_corr = pd.Series(index=store_metrics, dtype=float)
for sm in store_metrics:
    log_s = sm == '전체_점포_수'
    r, n = corr(region_quarter['임대료'], region_quarter[sm], True, log_s)
    rq_corr[sm] = r
print(f'\n[6-3] 지역x분기 독립표본(n={region_quarter.shape[0]}) 임대료 상관계수:')
print(rq_corr.round(3))
