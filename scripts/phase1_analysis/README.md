# scripts/phase1_analysis/

**Phase 1 (2026-08) 대회 분석 스크립트 아카이브.** `docs/data_result.md`·`data_result_2.md`·`data_result_3.md`·`docs/최종_분석_보고서.md`(1~24장)의 분석을 만든 스크립트다. 하네스(증거 중심 입지 추천)와는 별개이며, 하네스 코드는 이 스크립트들을 import 하지 않는다.

2026-09-02에 `scripts/` 루트에서 이곳으로 이동했다. 이동 시 대부분의 `ROOT = dirname(dirname(...))` 를 `dirname(dirname(dirname(...)))` 로 보정해 저장소 루트에서 `python scripts/phase1_analysis/<name>.py` 로 실행 가능하다. 일부는 `data/`·`output/` 상대경로를 하드코딩해 **cwd = 저장소 루트**를 가정한다.

이 스크립트들이 쓰던 `output/{seoul,songpa,gu}/`·`output/rent_*`·`output/figures/{seoul,songpa,rent_*}/` CSV·그림은 2026-09-02에 삭제됐다(재실행하면 재생성).

## 그룹

| 접두어 | 내용 | 산출(삭제됨) |
| --- | --- | --- |
| `seoul_*` (6) | 서울 전체 상관·다층·패널·다변량·분기추세·변화추세 레이어 | `output/seoul/`, `output/figures/seoul/` |
| `songpa_*` (9) | 송파구 케이스: 상관·다층·패널·다변량·시간프로파일·유동↔상주/직장·멤버십 | `output/songpa/`, `output/figures/songpa/` |
| `gu_*` (3) | 자치구별 분기추세·상주매출·직장매출 추세 | `output/gu/` |
| `rent_*` (11) | 임대료 ↔ 매출·점포·유동·변화지표 상관, 권역 매칭·멤버십, 크로스워크 겹침 | `output/rent_*`, `output/figures/rent_*` |
| `plot_*` (12) | 위 분석들의 시각화 (`plot_overlap_examples` 는 겹침 예시 4장 — 그림은 `output/figures/` 에 커밋 유지) | `output/figures/{gu,rent_*}/` |
| `scatter_*` (2) | 연령↔외식매출, 고용률↔매출 산점도 | — |
| `scoring_*` (2) | **기존 Top-K 스코어링 모델** (`scoring_topk_recommend` + `scoring_evaluate_all`). ADR-001 에 따라 운영 순위 금지. `evaluating-legacy-ranking` 스킬의 감사 입력 | `output/scoring/` (이전 세션에서 삭제) |
| `make_combined_csv.py` | 18장~ 결합 데이터셋 빌더 (`dong_lists/` import) | `seoul_gu_dong_list.csv` 등 |

## 하네스 루트에 남긴 관련 스크립트

- `scripts/change_indicator_store_correlation.py` — 상권변화지표 LL/LH/HL/HH 라벨 검증 (`.claude/CLAUDE.md` 가 경로로 인용)
- `scripts/build_overlap_crosswalks.py` — `output/crosswalks/` 생성기 (하네스 `overlapping_units` 사용)
- `scripts/make_gu_dong_lists.py` — 폼 자치구·행정동 목록 (웹서비스 청사진 C3 자산)
- `scripts/check_dataset_update_frequency.py` · `plot_dataset_update_frequency.py` — `output/dataset_update_frequency*` 생성기 (데이터 감사 입력)
