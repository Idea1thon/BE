"""
임대료 x 상권변화지표(LL/LH/HL/HH) 상관관계 — 신규 분석(2026-08-09).

4장(scripts/change_indicator_store_correlation.py, 상권변화지표 x 점포)과 같은 틀을
임대료 데이터에 적용한다. 상권변화지표는 업종 구분이 없는 상권/행정동 단위 값이라(상권배후지
버전은 없음, .claude/CLAUDE.md 참고) 유동인구(14장)처럼 지역x분기 단위로 바로 결합한다.

- gap_survive = 지역 운영(생존)영업개월평균 - 서울 운영영업개월평균  (양수면 H, 음수면 L)
- gap_close   = 지역 폐업영업개월평균 - 서울 폐업영업개월평균        (양수면 H, 음수면 L)

공간 단위 결합 방식(10-3c와 동일한 원칙 재사용):
- 상권: rent_region_representative.csv의 선택된_상권(들). 합집합 지역은 각 상권의 gap을
  상권 면적(RELM_AR)으로 가중평균(11~13장의 "카운트는 합산" 관례와 달리, gap은 원래 두
  값의 차이라는 비율성 지표라 합산이 아니라 가중평균이 맞다 — 13장 매출/14장 유동인구와
  동일한 논리로, 매출/유동인구도 절대량이라 가중합=가중평균으로 결합했음을 상기).
- 행정동: output/rent_region_dong_membership.csv(10-3c crosswalk 면적가중 다중소속)로
  gap_survive/gap_close를 가중평균.
- 사분면(LL/LH/HL/HH) 라벨은 원본 상권_변화_지표 컬럼을 그대로 쓰지 않고, 위 가중평균된
  gap_survive/gap_close의 부호로 다시 판정한다(원본 라벨은 상권/행정동 단위 원값 기준이라
  여러 상권/행정동을 블렌딩한 뒤에는 그대로 쓸 수 없음 -- L/H 정의 자체가 gap 부호이므로
  블렌딩된 gap에서 다시 판정하는 게 일관적).

공통분기: 상권변화지표(2021Q1~2026Q1, 21개) x 임대료(2021Q1~2025Q4, 20개) 교집합
= 2021Q1~2025Q4 (20개), 11~14장과 동일.
"""
import re
import numpy as np
import pandas as pd
import shapefile

rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
QUADRANT_ORDER = ['LL', 'LH', 'HL', 'HH']
QUADRANT_LABEL = {'LL': '다이나믹(LL)', 'LH': '상권확장(LH)', 'HL': '상권축소(HL)', 'HH': '정체(HH)'}


def corr(a, b, log_a=False):
    x = np.log1p(a) if log_a else a
    y = b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()


def quadrant_of(gap_survive, gap_close):
    first = 'H' if gap_survive >= 0 else 'L'
    second = 'H' if gap_close >= 0 else 'L'
    return first + second


def build_rent_long(quarters_hint, region_names):
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
    for m in rent_metrics:
        rent_long[m] = pd.to_numeric(rent_long[m].replace('-', np.nan), errors='coerce')
    rent_long = rent_long[rent_long['임대료_지역'].isin(region_names)]
    return rent_long, quarters


def analyze(unit_label, region_weight_map, ci_path, ci_code_col, out_prefix):
    """region_weight_map: DataFrame[임대료_지역, <code_col>, weight] (weight 합=1)"""
    ci = pd.read_csv(ci_path, encoding='cp949')
    ci['gap_survive'] = ci['운영_영업_개월_평균'] - ci['서울_운영_영업_개월_평균']
    ci['gap_close'] = ci['폐업_영업_개월_평균'] - ci['서울_폐업_영업_개월_평균']
    ci_quarters = sorted(ci['기준_년분기_코드'].unique())
    print(f"\n{'=' * 60}\n[{unit_label}] 분석 시작\n{'=' * 60}")
    print(f"[1] 상권변화지표 분기: {ci_quarters[0]}~{ci_quarters[-1]} ({len(ci_quarters)}개)")

    code_col = region_weight_map.columns[1]
    n_regions = region_weight_map['임대료_지역'].nunique()
    avg_n = region_weight_map.groupby('임대료_지역').size().mean()
    print(f"[2] 커버: {n_regions}/63개 지역 -> {code_col} (지역당 평균 {avg_n:.2f}개)")

    rent_long, quarters = build_rent_long(ci_quarters, region_weight_map['임대료_지역'])
    print(f"[3] 공통분기: {quarters[0]}~{quarters[-1]} ({len(quarters)}개)")

    ci = ci[ci['기준_년분기_코드'].isin(quarters)][[ci_code_col, '기준_년분기_코드', 'gap_survive', 'gap_close']]
    ci = ci.rename(columns={ci_code_col: code_col})
    ci[code_col] = ci[code_col].astype(int)

    merged = region_weight_map.merge(ci, on=code_col, how='inner')
    merged['gap_survive_w'] = merged['gap_survive'] * merged['weight']
    merged['gap_close_w'] = merged['gap_close'] * merged['weight']
    panel = merged.groupby(['임대료_지역', '기준_년분기_코드'])[['gap_survive_w', 'gap_close_w']].sum().reset_index()
    panel = panel.rename(columns={'gap_survive_w': 'gap_survive', 'gap_close_w': 'gap_close'})
    panel['사분면'] = [quadrant_of(gs, gc) for gs, gc in zip(panel['gap_survive'], panel['gap_close'])]
    panel = panel.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='inner')
    panel.to_csv(f'output/{out_prefix}_panel.csv', index=False, encoding='utf-8-sig')
    print(f"[4] 결합 패널: {panel.shape[0]}행 (지역x분기, 업종 구분 없음) -> output/{out_prefix}_panel.csv")

    # 연속값 상관관계: gap_survive/gap_close x 임대료 지표
    corr_df = pd.DataFrame(index=rent_metrics, columns=['gap_survive', 'gap_close'], dtype=float)
    n_df = pd.DataFrame(index=rent_metrics, columns=['gap_survive', 'gap_close'], dtype=float)
    for rm in rent_metrics:
        log_r = rm == '임대료'
        for gap in ['gap_survive', 'gap_close']:
            r, n = corr(panel[rm], panel[gap], log_r)
            corr_df.loc[rm, gap] = r
            n_df.loc[rm, gap] = n
    corr_df.to_csv(f'output/{out_prefix}_correlation.csv', encoding='utf-8-sig')
    print(f'\n[5] gap x 임대료지표 상관계수 ({unit_label}):')
    print(corr_df.round(3))
    print('표본수(n):')
    print(n_df.astype(int))

    # 사분면별 평균 임대료 지표
    quad = panel.groupby('사분면')[rent_metrics].mean().reindex(QUADRANT_ORDER)
    quad_n = panel.groupby('사분면').size().reindex(QUADRANT_ORDER)
    quad.to_csv(f'output/{out_prefix}_quadrant_mean.csv', encoding='utf-8-sig')
    print(f'\n[6] 사분면별 평균 임대료 지표 ({unit_label}):')
    print(quad.round(2))
    print('사분면별 표본수:')
    print(quad_n)

    return panel, corr_df, quad


# ========== 1. 상권 단위 ==========
sf = shapefile.Reader('data/영역/상권/서울시 상권분석서비스(영역-상권).shp')
sf_fields = [f[0] for f in sf.fields[1:]]
sg_recs = [dict(zip(sf_fields, sr.record)) for sr in sf.iterShapeRecords()]
name2code = {r['TRDAR_CD_N']: int(r['TRDAR_CD']) for r in sg_recs}
code2area = {int(r['TRDAR_CD']): r['RELM_AR'] for r in sg_recs}

rep = pd.read_csv('output/rent_region_representative.csv', encoding='utf-8-sig')
rep = rep[rep['대표결정방식'] != '매칭없음'].copy()

sg_rows = []
for _, row in rep.iterrows():
    names = [n.strip() for n in str(row['선택된_상권']).split(' | ')]
    codes = [name2code[n] for n in names]
    areas = [code2area[c] for c in codes]
    total = sum(areas)
    for c, a in zip(codes, areas):
        sg_rows.append({'임대료_지역': row['임대료_지역'], '상권_코드': c, 'weight': a / total})
sg_weight_map = pd.DataFrame(sg_rows)

sg_panel, sg_corr, sg_quad = analyze(
    '상권', sg_weight_map,
    'data/상권변화지표/서울시 상권분석서비스(상권변화지표-상권).csv', '상권_코드',
    'rent_change_sg')

# ========== 2. 행정동 단위 ==========
dong_membership = pd.read_csv('output/rent_region_dong_membership.csv', encoding='utf-8-sig')
dong_weight_map = dong_membership[['임대료_지역', '행정동_코드', 'weight']].copy()
dong_weight_map['행정동_코드'] = dong_weight_map['행정동_코드'].astype(int)

dong_panel, dong_corr, dong_quad = analyze(
    '행정동', dong_weight_map,
    'data/상권변화지표/서울시 상권분석서비스(상권변화지표-행정동).csv', '행정동_코드',
    'rent_change_dong')

# ========== 3. 단위 비교 요약 ==========
print(f"\n{'=' * 60}\n[요약] gap x 임대료 상관계수, 공간 단위 비교\n{'=' * 60}")
summary = pd.DataFrame({
    '상권': sg_corr.loc['임대료'],
    '행정동': dong_corr.loc['임대료'],
})
print(summary.round(3))
summary.to_csv('output/rent_change_unit_comparison.csv', encoding='utf-8-sig')
