"""
민감도 분석: rent_store_panel.csv에서 원본 데이터 모순 행(전체_점포_수=0인데
프랜차이즈_점포_수>0, 124건 — docs/PROGRESS.md·docs/data_result_2.md 11-3b 참고)을
제외하고 상관계수를 재계산해, rent_store_correlation.py의 원래 결과와 비교한다.

패널 CSV 자체는 건드리지 않고(원자료 그대로 보존), 이 스크립트 안에서만 제외 후
재계산한다. 지역x분기 독립표본(11-2의 n=1,260)도 모순 행을 뺀 뒤 업종 10개를
다시 합산해 재생성한다.
"""
import pandas as pd
import numpy as np

panel = pd.read_csv('output/rent_store_panel.csv', encoding='utf-8-sig')
rent_metrics = ['임대료', '공실률', '투자수익률', '소득수익률', '자본수익률']
store_metrics = ['전체_점포_수', '개업_율', '폐업_률', '프랜차이즈_비율']
FOOD_NAMES = panel[['서비스_업종_코드', '서비스_업종_코드_명']].drop_duplicates()


def corr(a, b, log_a=False, log_b=False):
    x = np.log1p(a) if log_a else a
    y = np.log1p(b) if log_b else b
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return np.nan, m.sum()
    return x[m].corr(y[m]), m.sum()


bad = (panel['전체_점포_수'] == 0) & (panel['프랜차이즈_점포_수'] > 0)
print(f"[0] 제외 대상: {bad.sum()}건 / 전체 {len(panel)}행")
print(panel[bad]['서비스_업종_코드_명'].value_counts().to_string())

clean = panel[~bad].copy()

# ---------- 1. 풀링 상관계수 (기존 output/rent_store_correlation_pooled.csv와 비교) ----------
old_pooled = pd.read_csv('output/rent_store_correlation_pooled.csv', index_col=0)

pooled = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
n_used = pd.DataFrame(index=rent_metrics, columns=store_metrics, dtype=float)
for rm in rent_metrics:
    log_r = rm == '임대료'
    for sm in store_metrics:
        log_s = sm == '전체_점포_수'
        r, n = corr(clean[rm], clean[sm], log_r, log_s)
        pooled.loc[rm, sm] = r
        n_used.loc[rm, sm] = n

print(f"\n[1] 풀링 상관계수 재계산 (n={len(clean)}행, 기존 {len(panel)}행 대비 {bad.sum()}건 제외):")
print(pooled.round(3))
print("\n[1] 기존 값(output/rent_store_correlation_pooled.csv):")
print(old_pooled.round(3))
print("\n[1] 차이(재계산 - 기존):")
print((pooled - old_pooled).round(3))

# ---------- 2. 업종별 breakdown (임대료 기준) ----------
old_by_biz = pd.read_csv('output/rent_store_correlation_by_industry.csv', index_col=0)
name_by_code = dict(zip(FOOD_NAMES['서비스_업종_코드'], FOOD_NAMES['서비스_업종_코드_명']))

by_biz = pd.DataFrame(index=old_by_biz.index, columns=store_metrics, dtype=float)
for code, name in name_by_code.items():
    sub = clean[clean['서비스_업종_코드'] == code]
    for sm in store_metrics:
        log_s = sm == '전체_점포_수'
        r, n = corr(sub['임대료'], sub[sm], True, log_s)
        by_biz.loc[name, sm] = r

print("\n[2] 업종별 임대료 상관계수 재계산:")
print(by_biz.round(3))
print("\n[2] 기존 값:")
print(old_by_biz.round(3))
print("\n[2] 차이(재계산 - 기존, |차이|>=0.02만 표시):")
diff = (by_biz - old_by_biz).round(3)
print(diff[(diff.abs() >= 0.02)].dropna(how='all').dropna(axis=1, how='all'))

# ---------- 3. 지역x분기 독립표본 재생성 (모순 행 제외 후 업종 10개 재합산) ----------
old_rq = pd.read_csv('output/rent_store_region_quarter.csv', encoding='utf-8-sig')

count_cols = ['전체_점포_수', '개업_점포_수', '폐업_점포_수', '프랜차이즈_점포_수']
rq = clean.groupby(['임대료_지역', '기준_년분기_코드'])[count_cols].sum().reset_index()
rent_cols = ['임대료_지역', '기준_년분기_코드'] + rent_metrics
rent_long = panel[rent_cols].drop_duplicates()
rq = rq.merge(rent_long, on=['임대료_지역', '기준_년분기_코드'], how='left')
rq['개업_율'] = np.where(rq['전체_점포_수'] > 0, rq['개업_점포_수'] / rq['전체_점포_수'] * 100, 0)
rq['폐업_률'] = np.where(rq['전체_점포_수'] > 0, rq['폐업_점포_수'] / rq['전체_점포_수'] * 100, 0)
rq['프랜차이즈_비율'] = np.where(rq['전체_점포_수'] > 0, rq['프랜차이즈_점포_수'] / rq['전체_점포_수'] * 100, 0)

print(f"\n[3] 지역x분기 독립표본 재생성: {rq.shape[0]}행 (기존 {old_rq.shape[0]}행)")
rq_corr = pd.Series(index=store_metrics, dtype=float)
old_rq_corr = pd.Series(index=store_metrics, dtype=float)
for sm in store_metrics:
    log_s = sm == '전체_점포_수'
    r, n = corr(rq['임대료'], rq[sm], True, log_s)
    rq_corr[sm] = r
    r_old, n_old = corr(old_rq['임대료'], old_rq[sm], True, log_s)
    old_rq_corr[sm] = r_old

print("[3] 재계산:")
print(rq_corr.round(3))
print("[3] 기존:")
print(old_rq_corr.round(3))
print("[3] 차이:")
print((rq_corr - old_rq_corr).round(3))
