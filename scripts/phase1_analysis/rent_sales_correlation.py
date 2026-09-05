"""
겹치는 영역(output/rent_region_representative.csv, 63개)에 한정하여
매장용빌딩 임대료·공실률·수익률 데이터와 추정매출 데이터(업종별 매출금액/매출건수)의
상관관계를 계산한다. (scripts/rent_store_correlation.py와 동일한 파이프라인 구조,
점포수 대신 매출 지표로 교체)

공통분기: 임대료 데이터(2021Q1~2025Q4, 20개 분기) x 추정매출 데이터(2021Q1~2026Q1, 21개 분기,
2026-08-08 data/추정매출/가 점포 데이터처럼 연도별 폴더로 확장됨) 교집합 = 20211~20254
(20개 분기, 임대료 데이터 범위 전체) — scripts/rent_store_correlation.py와 동일한 구간.
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

# 추정매출 데이터: data/점포/와 동일하게 연도별 폴더로 재구성됨. 스키마는 연도 전체가 동일
# (기준_년분기_코드/상권_코드/서비스_업종_코드/당월_매출_금액/당월_매출_건수 등, 한글 컬럼명 고정)
# 이라 점포 데이터처럼 컬럼명 매핑은 필요 없음. 2026년 폴더 파일이 20251~20254(2025년 폴더와 중복)와
# 20261을 모두 포함하므로, 2025년 폴더 파일은 건너뛰고 2026년 폴더 파일 하나로 20251~20261을 커버한다.
SALES_FILES = [
    'data/추정매출/2021/서울시_상권분석서비스(추정매출-상권)_2021년.csv',
    'data/추정매출/2022/서울시_상권분석서비스(추정매출-상권)_2022년.csv',
    'data/추정매출/2023/서울시_상권분석서비스(추정매출-상권)_2023년.csv',
    'data/추정매출/2024/서울시 상권분석서비스(추정매출-상권)_2024년.csv',
    'data/추정매출/2026/서울시 상권분석서비스(추정매출-상권).csv',
]
SALES_COLS = ['기준_년분기_코드', '상권_코드', '서비스_업종_코드', '서비스_업종_코드_명',
              '당월_매출_금액', '당월_매출_건수']

# ---------- 1. 겹치는 영역 63개 -> 상권_코드 매핑 (rent_store_correlation.py와 동일 소스/로직) ----------
rep = pd.read_csv('output/rent_region_representative.csv', encoding='utf-8-sig')

sf = shapefile.Reader('data/영역/상권/서울시 상권분석서비스(영역-상권).shp')
sf_fields = [f[0] for f in sf.fields[1:]]
name2code = {}
for sr in sf.shapeRecords():
    rec = dict(zip(sf_fields, sr.record))
    name2code[rec['TRDAR_CD_N']] = int(rec['TRDAR_CD'])

region_codes = []
unmatched = []
for _, row in rep.iterrows():
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

# ---------- 2. 추정매출 데이터: 연도별 파일 통합, 겹치는 상권_코드만 필터 ----------
sales_parts = []
for path in SALES_FILES:
    df = pd.read_csv(path, encoding='cp949')
    df = df[df['서비스_업종_코드'].isin(FOOD_CODES)]
    df = df[df['상권_코드'].astype(int).isin(region_codes['상권_코드'])]
    sales_parts.append(df[SALES_COLS])
sales = pd.concat(sales_parts, ignore_index=True)
sales['상권_코드'] = sales['상권_코드'].astype(int)
sales = sales.drop_duplicates(subset=['기준_년분기_코드', '상권_코드', '서비스_업종_코드'])
sales_quarters = sorted(sales['기준_년분기_코드'].unique())
print(f"[2] 추정매출 데이터 통합: {sales.shape[0]}행, 분기 {sales_quarters[0]}~{sales_quarters[-1]} ({len(sales_quarters)}개)")

# ---------- 3. 임대료 데이터: wide -> long (rent_store_correlation.py와 동일) ----------
raw = pd.read_csv('data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv', encoding='utf-8-sig', header=None)
region_col = raw.iloc[2:, 1].reset_index(drop=True)
metric_names = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
quarter_labels = raw.iloc[0, 2:].tolist()
unique_quarters = []
seen = set()
for q in quarter_labels:
    if q not in seen:
        unique_quarters.append(q)
        seen.add(q)

data = raw.iloc[2:, 2:].reset_index(drop=True)
data.columns = pd.MultiIndex.from_product([unique_quarters, metric_names])

rent_qcodes = []
for q in unique_quarters:
    y, qtr = re.match(r'(\d{4}) (\d)/4', q).groups()
    rent_qcodes.append((q, int(y) * 10 + int(qtr)))
QUARTERS = sorted(set(q for _, q in rent_qcodes) & set(sales_quarters))
print(f"[3] 공통분기: {QUARTERS} ({len(QUARTERS)}개)")

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

# ---------- 4. 매출 데이터: 상권_코드 -> 임대료_지역 집계 (합집합 지역은 매출 합산) ----------
sales = sales[sales['기준_년분기_코드'].isin(QUARTERS)]
sales = sales.merge(region_codes, on='상권_코드', how='inner')

agg = sales.groupby(['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명']).agg(
    당월_매출_금액=('당월_매출_금액', 'sum'),
    당월_매출_건수=('당월_매출_건수', 'sum'),
).reset_index()
agg['건당_매출액'] = np.where(agg['당월_매출_건수'] > 0, agg['당월_매출_금액'] / agg['당월_매출_건수'], 0)
print(f"[4] 매출 집계: {agg.shape[0]}행 (지역x분기x업종)")

# 지역x분기x업종 풀 격자 생성 후 없는 조합은 0으로 채움 (그 업종 매출 기록이 아예 없었다는 뜻,
# rent_store_correlation.py의 격자 채움과 동일한 관례)
regions = rep['임대료_지역'].unique()
grid = pd.MultiIndex.from_product([regions, QUARTERS, FOOD_CODES.keys()],
                                   names=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드']).to_frame(index=False)
grid['서비스_업종_코드_명'] = grid['서비스_업종_코드'].map(FOOD_CODES)
agg_full = grid.merge(agg, on=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명'], how='left')
for c in ['당월_매출_금액', '당월_매출_건수', '건당_매출액']:
    agg_full[c] = agg_full[c].fillna(0)

# ---------- 5. 병합 ----------
panel = agg_full.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
panel.to_csv('output/rent_sales_panel.csv', index=False, encoding='utf-8-sig')
print(f"[5] 결합 패널: {panel.shape[0]}행 -> output/rent_sales_panel.csv")

# ---------- 5b. 지역x분기 단위로 업종 10개 합산 (유사반복 검증용 독립표본) ----------
sum_cols = ['당월_매출_금액', '당월_매출_건수']
region_quarter = panel.groupby(['임대료_지역', '기준_년분기_코드'])[sum_cols].sum().reset_index()
region_quarter = region_quarter.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='left')
region_quarter['건당_매출액'] = np.where(region_quarter['당월_매출_건수'] > 0,
                                     region_quarter['당월_매출_금액'] / region_quarter['당월_매출_건수'], 0)
region_quarter.to_csv('output/rent_sales_region_quarter.csv', index=False, encoding='utf-8-sig')
print(f"[5b] 지역x분기 독립표본: {region_quarter.shape[0]}행 -> output/rent_sales_region_quarter.csv")

# ---------- 6. 상관관계 계산 ----------
rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
sales_metrics = ['당월_매출_금액', '당월_매출_건수', '건당_매출액']

def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()

# 6-1. 전체 풀링 상관관계 (log1p: 임대료/매출_금액/매출_건수/건당_매출액 모두 우측 치우침)
pooled = pd.DataFrame(index=rent_metrics, columns=sales_metrics, dtype=float)
n_used = pd.DataFrame(index=rent_metrics, columns=sales_metrics, dtype=float)
for rm in rent_metrics:
    log_r = rm == '임대료'
    for sm in sales_metrics:
        r, n = corr(panel[rm], panel[sm], log_r, True)
        pooled.loc[rm, sm] = r
        n_used.loc[rm, sm] = n
pooled.to_csv('output/rent_sales_correlation_pooled.csv', encoding='utf-8-sig')
print('\n[6-1] 전체 풀링 상관계수 (log1p: 임대료, 매출 지표 전부):')
print(pooled.round(3))
print('\n표본수(n):')
print(n_used.astype(int))

# 6-2. 업종별 breakdown: 임대료 vs 각 sales_metric
by_biz = pd.DataFrame(index=list(FOOD_CODES.values()), columns=sales_metrics, dtype=float)
for code, name in FOOD_CODES.items():
    sub = panel[panel['서비스_업종_코드'] == code]
    for sm in sales_metrics:
        r, n = corr(sub['임대료'], sub[sm], True, True)
        by_biz.loc[name, sm] = r
by_biz.to_csv('output/rent_sales_correlation_by_industry.csv', encoding='utf-8-sig')
print('\n[6-2] 업종별 임대료 상관계수:')
print(by_biz.round(3))

# 6-3. 지역x분기 독립표본(유사반복 검증)
rq_corr = pd.Series(index=sales_metrics, dtype=float)
for sm in sales_metrics:
    r, n = corr(region_quarter['임대료'], region_quarter[sm], True, True)
    rq_corr[sm] = r
print(f'\n[6-3] 지역x분기 독립표본(n={region_quarter.shape[0]}) 임대료 상관계수:')
print(rq_corr.round(3))
