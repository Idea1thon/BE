"""
scripts/rent_store_correlation.py(임대료 x 점포-상권, 11장)와 같은 목적이지만,
공간 단위를 행정동/상권배후지로 바꿔 11장 결론(임대료 x 전체_점포_수 약~중간, 나머지 미약)이
단위 선택에 흔들리지 않는지 로버스트니스 체크한다. scripts/rent_sales_dong_bg_correlation.py(13장)와
동일한 패턴을 점포 데이터에 적용한 버전.

10-3c의 crosswalk 면적가중 다중소속(scripts/rent_region_membership.py)을 그대로 재사용:
- 행정동: output/rent_region_dong_membership.csv — 63개 지역 전부, 지역당 평균 2.56개 행정동
- 상권배후지: output/rent_region_alley_membership.csv — 63개 중 60개, 지역당 평균 6.33개 배후지

점포 데이터는 카운트(전체_점포_수/개업_점포_수/폐업_점포_수/프랜차이즈_점포_수)이므로, 11장에서
합집합 지역에 쓴 "원시 카운트를 합산한 뒤 비율을 재계산" 관례를 그대로 확장한다: 행정동/배후지별
원시 카운트에 weight를 곱해 합산(weight 합=1이므로 가중합=면적가중평균)한 다음, 그 가중합산된
카운트에서 개업_율/폐업_률/프랜차이즈_비율을 다시 계산한다 — weight를 비율에 직접 곱하면 안 됨
(비율의 가중평균 != 가중합산된 분자/분모의 비율).

공통분기: 2021Q1~2025Q4(20개), 11~14장과 동일.
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

# 11장(rent_store_correlation.py)과 동일한 연도별 파일 목록/스키마 통일 규칙:
# 컬럼명이 연도별로 다르므로(점포_수 vs 전체_점포_수 등) 매핑 테이블로 흡수.
# 2025년 폴더는 2026년 폴더 파일(20251~20261 포함)과 중복이라 건너뜀.
# 주의: "서울시_상권분석서비스"(밑줄) vs "서울시 상권분석서비스"(공백) 표기가 같은 연도 폴더
# 안에서도 파일마다 다르다(예: 2023년 폴더는 -행정동만 공백, -상권/-상권배후지는 밑줄) —
# 원본 파일명 자체의 불일치라 유닛별로 정확한 파일명을 그대로 하드코딩한다.
YEAR_FILES = {
    '행정동': [
        '2021년/서울시_상권분석서비스(점포-행정동)_2021년.csv',
        '2022년/서울시_상권분석서비스(점포-행정동)_2022년.csv',
        '2023년/서울시 상권분석서비스(점포-행정동)_2023년.csv',
        '2024년/서울시 상권분석서비스(점포-행정동)_2024년.csv',
        '2026년/서울시 상권분석서비스(점포-행정동).csv',
    ],
    '상권배후지': [
        '2021년/서울시_상권분석서비스(점포-상권배후지)_2021년.csv',
        '2022년/서울시_상권분석서비스(점포-상권배후지)_2022년.csv',
        '2023년/서울시_상권분석서비스(점포-상권배후지)_2023년.csv',
        '2024년/서울시 상권분석서비스(점포-상권배후지)_2024년.csv',
        '2026년/서울시 상권분석서비스(점포-상권배후지).csv',
    ],
}
DATA_ROOT = 'data/점포/'

COLUMN_ALIASES = {
    'stdr_yyqu_cd': '기준_년분기_코드',
    'svc_induty_cd': '서비스_업종_코드', 'svc_induty_cd_nm': '서비스_업종_코드_명',
    # 2026-08-09 수정: rent_store_correlation.py(11장)와 동일한 이유로 '점포_수'가 아니라
    # '유사_업종_점포_수'가 신 스키마의 '전체_점포_수'(=일반+프랜차이즈)와 대응된다.
    # 상세: docs/data_result_2.md 11-1d.
    'stor_co': '전체_점포_수', '유사_업종_점포_수': '전체_점포_수',
    'frc_stor_co': '프랜차이즈_점포_수', 'opbiz_rt': '개업_율', 'opbiz_stor_co': '개업_점포_수',
    'clsbiz_rt': '폐업_률', 'clsbiz_stor_co': '폐업_점포_수',
}
COUNT_COLS = ['전체_점포_수', '프랜차이즈_점포_수', '개업_점포_수', '폐업_점포_수']

rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
store_metrics = ['전체_점포_수', '개업_율', '폐업_률', '프랜차이즈_비율']


def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()


def load_store(unit_file_key, code_col_raw, codes):
    parts = []
    for path_tmpl in YEAR_FILES[unit_file_key]:
        path = DATA_ROOT + path_tmpl
        try:
            df = pd.read_csv(path, encoding='cp949')
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding='utf-8')
        df = df.rename(columns=COLUMN_ALIASES)
        df = df[df['서비스_업종_코드'].isin(FOOD_CODES)]
        df[code_col_raw] = df[code_col_raw].astype(int)
        df = df[df[code_col_raw].isin(codes)]
        cols = ['기준_년분기_코드', code_col_raw, '서비스_업종_코드', '서비스_업종_코드_명'] + COUNT_COLS
        parts.append(df[cols])
    store = pd.concat(parts, ignore_index=True)
    store = store.drop_duplicates(subset=['기준_년분기_코드', code_col_raw, '서비스_업종_코드'])
    return store


def build_rent_long(quarters_hint, region_names):
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
    quarters = sorted(set(q for _, q in rent_qcodes) & set(quarters_hint))

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


def run(unit_label, unit_file_key, code_col_raw, membership_id_col, membership_file, out_prefix):
    print(f"\n{'=' * 60}\n[{unit_label}] 분석 시작\n{'=' * 60}")
    membership = pd.read_csv(f'output/{membership_file}', encoding='utf-8-sig')
    region_map = membership[['임대료_지역', membership_id_col, 'weight']].rename(
        columns={membership_id_col: code_col_raw})
    region_map[code_col_raw] = region_map[code_col_raw].astype(int)
    codes = set(region_map[code_col_raw])
    n_regions = region_map['임대료_지역'].nunique()
    avg_n = region_map.groupby('임대료_지역').size().mean()
    print(f"[1] 커버: {n_regions}/63개 지역 -> {code_col_raw} {len(codes)}개 "
          f"(면적가중 다중소속, 지역당 평균 {avg_n:.2f}개)")

    store = load_store(unit_file_key, code_col_raw, codes)
    store_quarters = sorted(store['기준_년분기_코드'].unique())
    print(f"[2] 점포 데이터 통합: {store.shape[0]}행, 분기 {store_quarters[0]}~{store_quarters[-1]} ({len(store_quarters)}개)")

    rent_long, quarters = build_rent_long(store_quarters, region_map['임대료_지역'])
    print(f"[3] 공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개), 임대료 롱포맷 {rent_long.shape[0]}행")

    store = store[store['기준_년분기_코드'].isin(quarters)]
    # code_col_raw 하나가 여러 임대료_지역에 걸쳐 있을 수 있어(면적가중 다중소속) 1:N으로 늘어남 --
    # 원시 카운트에 weight를 곱한 뒤 지역별로 합산(=가중평균, weight 합=1)하고, 비율은 그 합산된
    # 카운트에서 다시 계산한다(비율 자체에 weight를 곱하면 분모가 다른 지역들의 비율을 잘못 섞게 됨).
    store = store.merge(region_map, on=code_col_raw, how='inner')
    for c in COUNT_COLS:
        store[c] = store[c] * store['weight']

    agg = store.groupby(['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명'])[COUNT_COLS].sum().reset_index()
    agg['개업_율'] = np.where(agg['전체_점포_수'] > 0, agg['개업_점포_수'] / agg['전체_점포_수'] * 100, 0)
    agg['폐업_률'] = np.where(agg['전체_점포_수'] > 0, agg['폐업_점포_수'] / agg['전체_점포_수'] * 100, 0)
    agg['프랜차이즈_비율'] = np.where(agg['전체_점포_수'] > 0, agg['프랜차이즈_점포_수'] / agg['전체_점포_수'] * 100, 0)
    print(f"[4] 점포 집계: {agg.shape[0]}행 (지역x분기x업종)")

    regions = region_map['임대료_지역'].unique()
    grid = pd.MultiIndex.from_product([regions, quarters, FOOD_CODES.keys()],
                                       names=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드']).to_frame(index=False)
    grid['서비스_업종_코드_명'] = grid['서비스_업종_코드'].map(FOOD_CODES)
    agg_full = grid.merge(agg, on=['임대료_지역', '기준_년분기_코드', '서비스_업종_코드', '서비스_업종_코드_명'], how='left')
    for c in COUNT_COLS + ['개업_율', '폐업_률', '프랜차이즈_비율']:
        agg_full[c] = agg_full[c].fillna(0)

    panel = agg_full.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
    panel.to_csv(f'output/{out_prefix}_panel.csv', index=False, encoding='utf-8-sig')
    print(f"[5] 결합 패널: {panel.shape[0]}행 -> output/{out_prefix}_panel.csv")

    region_quarter = panel.groupby(['임대료_지역', '기준_년분기_코드'])[COUNT_COLS].sum().reset_index()
    region_quarter = region_quarter.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='left')
    region_quarter['개업_율'] = np.where(region_quarter['전체_점포_수'] > 0,
                                       region_quarter['개업_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
    region_quarter['폐업_률'] = np.where(region_quarter['전체_점포_수'] > 0,
                                       region_quarter['폐업_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
    region_quarter['프랜차이즈_비율'] = np.where(region_quarter['전체_점포_수'] > 0,
                                            region_quarter['프랜차이즈_점포_수'] / region_quarter['전체_점포_수'] * 100, 0)
    region_quarter.to_csv(f'output/{out_prefix}_region_quarter.csv', index=False, encoding='utf-8-sig')
    print(f"[5b] 지역x분기 독립표본: {region_quarter.shape[0]}행 -> output/{out_prefix}_region_quarter.csv")

    pooled = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
    n_used = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
    for rm in rent_metrics:
        log_r = rm == '임대료'
        for sm in store_metrics:
            log_s = sm == '전체_점포_수'
            r, n = corr(panel[rm], panel[sm], log_r, log_s)
            pooled.loc[rm, sm] = r
            n_used.loc[rm, sm] = n
    pooled.to_csv(f'output/{out_prefix}_correlation_pooled.csv', encoding='utf-8-sig')
    print(f'\n[6-1] 전체 풀링 상관계수 ({unit_label}):')
    print(pooled.round(3))
    print('표본수(n):')
    print(n_used.astype(int))

    by_biz = pd.DataFrame(index=list(FOOD_CODES.values()), columns=store_metrics, dtype=float)
    for code, name in FOOD_CODES.items():
        sub = panel[panel['서비스_업종_코드'] == code]
        for sm in store_metrics:
            log_s = sm == '전체_점포_수'
            r, n = corr(sub['임대료'], sub[sm], True, log_s)
            by_biz.loc[name, sm] = r
    by_biz.to_csv(f'output/{out_prefix}_correlation_by_industry.csv', encoding='utf-8-sig')
    print(f'\n[6-2] 업종별 임대료 상관계수 ({unit_label}):')
    print(by_biz.round(3))

    rq_corr = pd.Series(index=store_metrics, dtype=float)
    for sm in store_metrics:
        log_s = sm == '전체_점포_수'
        r, n = corr(region_quarter['임대료'], region_quarter[sm], True, log_s)
        rq_corr[sm] = r
    print(f'\n[6-3] 지역x분기 독립표본(n={region_quarter.shape[0]}) 임대료 상관계수 ({unit_label}):')
    print(rq_corr.round(3))

    return pooled, rq_corr, region_quarter.shape[0], panel.shape[0]


dong_pooled, dong_rq, dong_n, dong_panel_n = run(
    '행정동', '행정동', '행정동_코드', '행정동_코드', 'rent_region_dong_membership.csv', 'rent_store_dong')
bg_pooled, bg_rq, bg_n, bg_panel_n = run(
    '상권배후지', '상권배후지', '상권_코드', '상권배후지_코드', 'rent_region_alley_membership.csv', 'rent_store_bg')

print(f"\n{'=' * 60}\n[요약] 임대료 x 점포 상관계수, 공간 단위 비교 (log1p Pearson r)\n{'=' * 60}")
summary = pd.DataFrame({
    '상권(11-2)': [0.246, -0.036, -0.026, -0.039],
    '행정동': dong_pooled.loc['임대료'].values,
    '상권배후지': bg_pooled.loc['임대료'].values,
}, index=store_metrics)
print(summary.round(3))
summary.to_csv('output/rent_store_unit_comparison_pooled.csv', encoding='utf-8-sig')

summary_rq = pd.DataFrame({
    '상권(11-2, n=1,260)': [0.360, -0.077, -0.068, -0.123],
    '행정동': dong_rq.values,
    '상권배후지': bg_rq.values,
}, index=store_metrics)
print(f"\n지역x분기 독립표본 기준 (n: 행정동={dong_n} / 상권배후지={bg_n}):")
print(summary_rq.round(3))
summary_rq.to_csv('output/rent_store_unit_comparison_region_quarter.csv', encoding='utf-8-sig')
