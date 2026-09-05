"""
임대료·공실률·수익률 x 유동인구(총_유동인구_수) 상관관계를, 13장과 동일하게
상권/행정동/상권배후지 세 공간 단위 모두에서 계산한다.

11~13장(점포/매출)과의 차이점: 유동인구 데이터는 서비스_업종_코드(업종) 구분이 없는
순수 인구 지표라 지역x분기 단위로 바로 붙는다(업종 10개로 explode할 필요 없음) — 그래서
패널 크기가 훨씬 작다(상권/행정동 63개 지역 x 20개 분기 = 1,260행, 상권배후지는 39개 지역
x 20개 분기 = 780행).

- 상권: output/rent_region_representative.csv의 '선택된_상권' -> 상권_코드 매핑 재사용(11~13장과
  동일한 shapefile 기반 이름 매칭). 합집합 지역(19개)은 유동인구를 합산.
- 행정동/상권배후지: 10-3c(2026-08-09)부터 point-in-polygon 대표좌표 방식 대신
  scripts/rent_region_membership.py의 면적가중 다중소속 테이블 사용. 행정동은 63개 전부 커버(지역당
  평균 2.56개 행정동에 weight로 걸침), 상권배후지는 63개 중 60개 커버(기존 point-in-polygon 39개
  보다 넓음 — crosswalk_trdar_alley_spatial.csv의 실제 공간 교차 기준). 여러 행정동/배후지에 걸친
  지역은 유동인구에 weight를 곱해 합산(가중평균과 동치).
- data/길단위인구/*.csv는 점포/매출 데이터와 달리 연도별로 안 쪼개져 있고 단일 파일에
  2021Q1~2026Q1(21개 분기) 전체가 이미 들어있어 파일 통합이 필요 없다.
- 공통분기: 임대료(2021Q1~2025Q4, 20개) x 유동인구(2021Q1~2026Q1, 21개) 교집합 = 2021Q1~2025Q4(20개).
- 상관계수는 log1p 변환 Pearson r(유동인구·임대료 모두 우측으로 치우쳐 있음).
"""
import re
import pandas as pd
import numpy as np
import shapefile

POP_COL = '총_유동인구_수'
rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']


def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()


def build_rent_long(pop_quarters, region_names):
    raw = pd.read_csv('data/매장용빌딩 임대료,공실률 및 수익률 통계 데이터.csv', encoding='utf-8-sig', header=None)
    region_col = raw.iloc[2:, 1].reset_index(drop=True)
    quarter_labels = raw.iloc[0, 2:].tolist()
    unique_quarters = []
    seen = set()
    for q in quarter_labels:
        if q not in seen:
            unique_quarters.append(q)
            seen.add(q)
    data = raw.iloc[2:, 2:].reset_index(drop=True)
    data.columns = pd.MultiIndex.from_product([unique_quarters, rent_metrics])

    rent_qcodes = []
    for q in unique_quarters:
        y, qtr = re.match(r'(\d{4}) (\d)/4', q).groups()
        rent_qcodes.append((q, int(y) * 10 + int(qtr)))
    quarters = sorted(set(q for _, q in rent_qcodes) & set(pop_quarters))

    long_rows = []
    for q, qcode in rent_qcodes:
        if qcode not in quarters:
            continue
        block = data[q].copy()
        block.insert(0, '임대료_지역', region_col)
        block.insert(1, '기준_년분기_코드', qcode)
        long_rows.append(block)
    rent_long = pd.concat(long_rows, ignore_index=True)
    for m in rent_metrics:
        rent_long[m] = pd.to_numeric(rent_long[m].replace('-', np.nan), errors='coerce')
    rent_long = rent_long[rent_long['임대료_지역'].isin(region_names)]
    return rent_long, quarters


def run_sg():
    """상권 단위: 11~13장과 동일한 shapefile 기반 이름 매칭 재사용."""
    print(f"\n{'=' * 60}\n[상권] 분석 시작\n{'=' * 60}")
    rep = pd.read_csv('output/rent_region_representative.csv', encoding='utf-8-sig')

    sf = shapefile.Reader('data/영역/상권/서울시 상권분석서비스(영역-상권).shp')
    sf_fields = [f[0] for f in sf.fields[1:]]
    name2code = {}
    for sr in sf.shapeRecords():
        rec = dict(zip(sf_fields, sr.record))
        name2code[rec['TRDAR_CD_N']] = int(rec['TRDAR_CD'])

    region_codes = []
    for _, row in rep.iterrows():
        for nm in str(row['선택된_상권']).split(' | '):
            code = name2code.get(nm)
            if code is not None:
                region_codes.append((row['임대료_지역'], code))
    region_map = pd.DataFrame(region_codes, columns=['임대료_지역', '상권_코드'])
    codes = set(region_map['상권_코드'])
    print(f"[1] 겹치는 영역 {rep.shape[0]}개 -> 상권_코드 {len(codes)}개 매핑")

    pop = pd.read_csv('data/길단위인구/서울시 상권분석서비스(길단위인구-상권).csv', encoding='cp949')
    pop = pop[pop['상권_코드'].astype(int).isin(codes)][['기준_년분기_코드', '상권_코드', POP_COL]]
    pop['상권_코드'] = pop['상권_코드'].astype(int)

    return finish(pop, '상권_코드', region_map, rep['임대료_지역'], 'rent_flow_sg')


def run_unit(unit_label, code_col, membership_id_col, membership_file, out_prefix):
    print(f"\n{'=' * 60}\n[{unit_label}] 분석 시작\n{'=' * 60}")
    membership = pd.read_csv(f'output/{membership_file}', encoding='utf-8-sig')
    region_map = membership[['임대료_지역', membership_id_col, 'weight']].rename(columns={membership_id_col: code_col})
    region_map[code_col] = region_map[code_col].astype(int)
    codes = set(region_map[code_col])
    n_regions = region_map['임대료_지역'].nunique()
    avg_n = region_map.groupby('임대료_지역').size().mean()
    print(f"[1] 커버: {n_regions}/63개 지역 -> {code_col} {len(codes)}개 "
          f"(면적가중 다중소속, 지역당 평균 {avg_n:.2f}개)")

    pop = pd.read_csv(f'data/길단위인구/서울시 상권분석서비스(길단위인구-{unit_label}).csv', encoding='cp949')
    pop = pop[pop[code_col].astype(int).isin(codes)][['기준_년분기_코드', code_col, POP_COL]]
    pop[code_col] = pop[code_col].astype(int)

    return finish(pop, code_col, region_map, region_map['임대료_지역'], out_prefix, weighted=True)


def finish(pop, code_col, region_map, region_names, out_prefix, weighted=False):
    pop_quarters = sorted(pop['기준_년분기_코드'].unique())
    print(f"[2] 유동인구 데이터 필터: {pop.shape[0]}행, 분기 {pop_quarters[0]}~{pop_quarters[-1]} ({len(pop_quarters)}개)")

    rent_long, quarters = build_rent_long(pop_quarters, region_names)
    print(f"[3] 공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개), 임대료 롱포맷 {rent_long.shape[0]}행")

    pop = pop[pop['기준_년분기_코드'].isin(quarters)]
    pop = pop.merge(region_map, on=code_col, how='inner')
    if weighted:
        # 행정동/상권배후지: 여러 곳에 면적가중 다중소속 -> weight 곱한 뒤 합산(가중합=가중평균, weight 합=1)
        pop[POP_COL] = pop[POP_COL] * pop['weight']
    # 상권 단위 합집합 지역(19개, weighted=False)은 그대로 합산
    agg = pop.groupby(['임대료_지역', '기준_년분기_코드'])[POP_COL].sum().reset_index()
    print(f"[4] 유동인구 집계: {agg.shape[0]}행 (지역x분기)")

    panel = agg.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
    panel.to_csv(f'output/{out_prefix}_panel.csv', index=False, encoding='utf-8-sig')
    print(f"[5] 결합 패널: {panel.shape[0]}행 -> output/{out_prefix}_panel.csv")

    result = pd.Series(index=rent_metrics, dtype=float)
    n_used = pd.Series(index=rent_metrics, dtype=float)
    for rm in rent_metrics:
        log_r = rm == '임대료'
        r, n = corr(panel[rm], panel[POP_COL], log_r, True)
        result[rm] = r
        n_used[rm] = n
    result.to_csv(f'output/{out_prefix}_correlation.csv', encoding='utf-8-sig', header=['총_유동인구_수'])
    print(f'\n[6] 임대료 지표 x 총_유동인구_수 상관계수 (n={int(n_used.iloc[0])}):')
    print(result.round(3))

    return result, panel.shape[0]


sg_result, sg_n = run_sg()
dong_result, dong_n = run_unit('행정동', '행정동_코드', '행정동_코드', 'rent_region_dong_membership.csv', 'rent_flow_dong')
bg_result, bg_n = run_unit('상권배후지', '상권배후지_코드', '상권배후지_코드', 'rent_region_alley_membership.csv', 'rent_flow_bg')

print(f"\n{'=' * 60}\n[요약] 임대료 지표 x 총_유동인구_수, 공간 단위 비교 (log1p Pearson r)\n{'=' * 60}")
summary = pd.DataFrame({
    f'상권(n={sg_n})': sg_result,
    f'행정동(n={dong_n})': dong_result,
    f'상권배후지(n={bg_n})': bg_result,
})
print(summary.round(3))
summary.to_csv('output/rent_flow_unit_comparison.csv', encoding='utf-8-sig')
