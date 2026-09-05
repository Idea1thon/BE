"""
scripts/rent_sales_correlation.py(임대료 x 추정매출-상권)와 같은 목적이지만,
공간 단위를 행정동/상권배후지로 바꿔 결과가 단위 선택에 흔들리지 않는지 로버스트니스 체크한다.

10-3c(2026-08-09)부터 행정동/상권배후지 소속을 point-in-polygon 대표좌표 방식이 아니라
scripts/rent_region_membership.py가 만든 면적가중 다중소속 테이블로 판정한다:
- 행정동: output/rent_region_dong_membership.csv 사용 — 63개 지역 전부 커버(100%), 지역당 평균
  2.56개 행정동에 겹침 면적 비중(weight, 지역별 합=1)으로 걸쳐 있음. 각 행정동의 매출값에 weight를
  곱해 합산한 가중평균을 그 지역의 대표 매출로 쓴다(10-3b에서 확인된, 대표좌표가 면적 1위가 아닌
  동에 찍히는 9건의 오차를 제거).
- 상권배후지: output/rent_region_alley_membership.csv 사용 — 63개 중 60개 커버(기존 point-in-polygon
  방식은 39개, 코드identity 방식은 20개 — crosswalk_trdar_alley_spatial.csv의 실제 공간 교차 기준이라
  더 넓게 잡힘, 10-3c 참고).

두 단위 모두 rent_sales_correlation.py와 동일하게 data/추정매출/2021년~2026년 5개 파일을 통합해
공통분기 2021Q1~2025Q4(20개 분기)로 계산한다.
"""
import re
import pandas as pd
import numpy as np

FOOD_CODES = {
    'CS100001': '한식음식점', 'CS100002': '중식음식점', 'CS100003': '일식음식점',
    'CS100004': '양식음식점', 'CS100005': '제과점', 'CS100006': '패스트푸드점',
    'CS100007': '치킨전문점', 'CS100008': '분식전문점', 'CS100009': '호프-간이주점',
    'CS100010': '커피-음료',
}

YEAR_FILES = {
    2021: '2021/서울시_상권분석서비스(추정매출-{unit})_2021년.csv',
    2022: '2022/서울시_상권분석서비스(추정매출-{unit})_2022년.csv',
    2023: '2023/서울시_상권분석서비스(추정매출-{unit})_2023년.csv',
    2024: '2024/서울시 상권분석서비스(추정매출-{unit})_2024년.csv',
    2026: '2026/서울시 상권분석서비스(추정매출-{unit}).csv',
}
DATA_ROOT = 'data/추정매출/'

rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
sales_metrics = ['당월_매출_금액', '당월_매출_건수', '건당_매출액']


def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()


def load_sales(unit, code_col, codes):
    parts = []
    for path_tmpl in YEAR_FILES.values():
        path = DATA_ROOT + path_tmpl.format(unit=unit)
        # 대부분 cp949지만 data/추정매출/2024/...(추정매출-상권배후지)_2024년.csv 한 파일만
        # 예외적으로 utf-8로 저장돼 있음(원본 파일 자체의 인코딩 불일치, 확인 완료) -> 폴백 처리
        try:
            df = pd.read_csv(path, encoding='cp949')
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding='utf-8')
        df = df[df['서비스_업종_코드'].isin(FOOD_CODES)]
        df = df[df[code_col].astype(int).isin(codes)]
        cols = ['기준_년분기_코드', code_col, '서비스_업종_코드', '서비스_업종_코드_명',
                '당월_매출_금액', '당월_매출_건수']
        parts.append(df[cols])
    sales = pd.concat(parts, ignore_index=True)
    sales[code_col] = sales[code_col].astype(int)
    sales = sales.drop_duplicates(subset=['기준_년분기_코드', code_col, '서비스_업종_코드'])
    return sales


def build_rent_long(sales_quarters, region_names):
    raw = pd.read_csv('data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv', encoding='utf-8-sig', header=None)
    region_col = raw.iloc[2:, 1].reset_index(drop=True)
    metric_names = rent_metrics
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
    quarters = sorted(set(q for _, q in rent_qcodes) & set(sales_quarters))

    long_rows = []
    for q, qcode in rent_qcodes:
        if qcode not in quarters:
            continue
        block = data[q].copy()
        block.insert(0, '임대료_지역', region_col)
        block.insert(1, '기준_년분기_코드', qcode)
        long_rows.append(block)
    rent_long = pd.concat(long_rows, ignore_index=True)
    for m in metric_names:
        rent_long[m] = pd.to_numeric(rent_long[m].replace('-', np.nan), errors='coerce')
    rent_long = rent_long[rent_long['임대료_지역'].isin(region_names)]
    return rent_long, quarters


def run(unit_label, unit_file_key, code_col, membership_id_col, membership_file, out_prefix):
    print(f"\n{'=' * 60}\n[{unit_label}] 분석 시작\n{'=' * 60}")
    membership = pd.read_csv(f'output/{membership_file}', encoding='utf-8-sig')
    region_map = membership[['임대료_지역', membership_id_col, 'weight']].rename(columns={membership_id_col: code_col})
    region_map[code_col] = region_map[code_col].astype(int)
    codes = set(region_map[code_col])
    n_regions = region_map['임대료_지역'].nunique()
    avg_n = region_map.groupby('임대료_지역').size().mean()
    print(f"[1] 커버: {n_regions}/63개 지역 -> {code_col} {len(codes)}개 "
          f"(면적가중 다중소속, 지역당 평균 {avg_n:.2f}개)")

    sales = load_sales(unit_file_key, code_col, codes)
    sales_quarters = sorted(sales['기준_년분기_코드'].unique())
    print(f"[2] 매출 데이터 통합: {sales.shape[0]}행, 분기 {sales_quarters[0]}~{sales_quarters[-1]} ({len(sales_quarters)}개)")

    rent_long, quarters = build_rent_long(sales_quarters, region_map['임대료_지역'])
    print(f"[3] 공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개), 임대료 롱포맷 {rent_long.shape[0]}행")

    sales = sales[sales['기준_년분기_코드'].isin(quarters)]
    # code_col 하나가 여러 임대료_지역에 걸쳐 있을 수 있어(면적가중 다중소속) 1:N으로 늘어남 --
    # 결합 전 weight를 곱해 지역별로 겹침 면적 비중만큼만 반영되도록 함(weight 합=1 -> 가중합=가중평균).
    sales = sales.merge(region_map, on=code_col, how='inner')
    sales['당월_매출_금액'] = sales['당월_매출_금액'] * sales['weight']
    sales['당월_매출_건수'] = sales['당월_매출_건수'] * sales['weight']

    agg = sales.groupby(['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명']).agg(
        당월_매출_금액=('당월_매출_금액', 'sum'),
        당월_매출_건수=('당월_매출_건수', 'sum'),
    ).reset_index()
    agg['건당_매출액'] = np.where(agg['당월_매출_건수'] > 0, agg['당월_매출_금액'] / agg['당월_매출_건수'], 0)
    print(f"[4] 매출 집계: {agg.shape[0]}행 (지역x분기x업종)")

    regions = region_map['임대료_지역'].unique()
    grid = pd.MultiIndex.from_product([regions, quarters, FOOD_CODES.keys()],
                                       names=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드']).to_frame(index=False)
    grid['서비스_업종_코드_명'] = grid['서비스_업종_코드'].map(FOOD_CODES)
    agg_full = grid.merge(agg, on=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명'], how='left')
    for c in ['당월_매출_금액', '당월_매출_건수', '건당_매출액']:
        agg_full[c] = agg_full[c].fillna(0)

    panel = agg_full.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
    panel.to_csv(f'output/{out_prefix}_panel.csv', index=False, encoding='utf-8-sig')
    print(f"[5] 결합 패널: {panel.shape[0]}행 -> output/{out_prefix}_panel.csv")

    region_quarter = panel.groupby(['임대료_지역', '기준_년분기_코드'])[['당월_매출_금액', '당월_매출_건수']].sum().reset_index()
    region_quarter = region_quarter.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='left')
    region_quarter['건당_매출액'] = np.where(region_quarter['당월_매출_건수'] > 0,
                                         region_quarter['당월_매출_금액'] / region_quarter['당월_매출_건수'], 0)
    region_quarter.to_csv(f'output/{out_prefix}_region_quarter.csv', index=False, encoding='utf-8-sig')
    print(f"[5b] 지역x분기 독립표본: {region_quarter.shape[0]}행 -> output/{out_prefix}_region_quarter.csv")

    pooled = pd.DataFrame(index=rent_metrics, columns=sales_metrics, dtype=float)
    n_used = pd.DataFrame(index=rent_metrics, columns=sales_metrics, dtype=float)
    for rm in rent_metrics:
        log_r = rm == '임대료'
        for sm in sales_metrics:
            r, n = corr(panel[rm], panel[sm], log_r, True)
            pooled.loc[rm, sm] = r
            n_used.loc[rm, sm] = n
    pooled.to_csv(f'output/{out_prefix}_correlation_pooled.csv', encoding='utf-8-sig')
    print(f'\n[6-1] 전체 풀링 상관계수 ({unit_label}):')
    print(pooled.round(3))
    print('표본수(n):')
    print(n_used.astype(int))

    by_biz = pd.DataFrame(index=list(FOOD_CODES.values()), columns=sales_metrics, dtype=float)
    for code, name in FOOD_CODES.items():
        sub = panel[panel['서비스_업종_코드'] == code]
        for sm in sales_metrics:
            r, n = corr(sub['임대료'], sub[sm], True, True)
            by_biz.loc[name, sm] = r
    by_biz.to_csv(f'output/{out_prefix}_correlation_by_industry.csv', encoding='utf-8-sig')
    print(f'\n[6-2] 업종별 임대료 상관계수 ({unit_label}):')
    print(by_biz.round(3))

    rq_corr = pd.Series(index=sales_metrics, dtype=float)
    for sm in sales_metrics:
        r, n = corr(region_quarter['임대료'], region_quarter[sm], True, True)
        rq_corr[sm] = r
    print(f'\n[6-3] 지역x분기 독립표본(n={region_quarter.shape[0]}) 임대료 상관계수 ({unit_label}):')
    print(rq_corr.round(3))

    return pooled, rq_corr, region_quarter.shape[0], panel.shape[0]


dong_pooled, dong_rq, dong_n, dong_panel_n = run(
    '행정동', '행정동', '행정동_코드', '행정동_코드', 'rent_region_dong_membership.csv', 'rent_sales_dong')
bg_pooled, bg_rq, bg_n, bg_panel_n = run(
    '상권배후지', '상권배후지', '상권배후지_코드', '상권배후지_코드', 'rent_region_alley_membership.csv', 'rent_sales_bg')

print(f"\n{'=' * 60}\n[요약] 임대료 x 매출 상관계수, 공간 단위 비교 (log1p Pearson r)\n{'=' * 60}")
summary = pd.DataFrame({
    '상권(11-2/12-2)': [0.19, 0.21, 0.14],
    '행정동': dong_pooled.loc['임대료'].values,
    '상권배후지': bg_pooled.loc['임대료'].values,
}, index=sales_metrics)
print(summary.round(3))
summary.to_csv('output/rent_sales_unit_comparison_pooled.csv', encoding='utf-8-sig')

summary_rq = pd.DataFrame({
    '상권(11-2/12-2)': [0.37, 0.35, 0.09],
    '행정동': dong_rq.values,
    '상권배후지': bg_rq.values,
}, index=sales_metrics)
print(f"\n지역x분기 독립표본 기준 (n: 상권=1,260 / 행정동={dong_n} / 상권배후지={bg_n}):")
print(summary_rq.round(3))
summary_rq.to_csv('output/rent_sales_unit_comparison_region_quarter.csv', encoding='utf-8-sig')
