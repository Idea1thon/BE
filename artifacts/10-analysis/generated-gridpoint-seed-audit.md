# 격자 합성 후보 seed 감사 결과

검사일: 2026-09-02 (2026-09-02 후속: 명칭 고정 + 10개 업종 metric + tier 캡 반영)
대상: `output/generated_evidence/`
검사 목적: `scripts/generate_gridpoint_evidence.py`가 만든 자치구별 격자 합성 후보 seed + 접근성 맥락 레코드의 실제 의미, 공간 커버리지, 원자료 재현성, 추천 파이프라인 사용 가능성 확인

## 결론

이 산출물은 **개별 상가·개별 매물 데이터셋이 아니다.** 공식 명칭은 **"공개 데이터 파생 격자 합성 후보 seed + 접근성/경쟁 맥락"**이다. `gridpoint_evidence.jsonl`의 각 행은 프로젝트가 100m 격자로 만든 **합성 좌표**에 공개 음식점 인허가·역·버스·아파트의 반경 집계값을 붙인 `project_generated` 맥락 레코드다. 실제 점포명·관리번호·주소·호실·공실·월세·면적·주차·매물 링크는 **없다** — 이 공백은 이 산출물로 메워지지 않는다.

**2026-09-02 후속 반영:**
- `metrics`에 10개 외식 업종별 `active_CS1000NN_license_count_500m`를 전부 기록(이전엔 CS100010 하나만). `generator.params.industries`에 업종 목록·필터 범위 명시.
- `recommendation_pipeline.py`: `synthetic_anchor=true` 후보는 `추천` 상한을 `조건부 검토`로 캡한다(실제 임대 가능 호실 미확인). 반대근거에 그 사유를 기록한다.

따라서 현재 등급은 다음과 같다.

| 구성요소 | 판정 | 사용 범위 |
| --- | --- | --- |
| `seeds.json` | `conditional` | 합성 격자 후보 seed 확장. `synthetic_anchor=true`와 실제 매물 아님 고지를 유지할 때만 사용 |
| `gridpoint_evidence.jsonl` | `conditional` | `--include-generated-points` 실행 시 `evidence_id`로 매칭돼 합성 후보 evidence·context_notes에 반경 인허가 경쟁 metric으로 연결(2026-09-02). 등급·정렬 미반영. 역·버스·아파트는 실행 시 재계산 |
| `manifest.json`, `_seoul_index.json` | provenance/audit metadata | 생성 규칙·건수·지역 범위 확인 |
| 실제 개별 상가·공실·매물 | `missing` | 별도 공식 매물/제휴 데이터 필요 |

파일 metadata에는 `generated_by`가 모델명으로 기록되어 있지 않고 `scripts/generate_gridpoint_evidence.py`, `gen-evidence-v1`, 생성일만 기록되어 있다. 따라서 “Claude가 생성했다”는 생성 주체는 파일만으로 독립 확인할 수 없고, 아래 내용은 파일과 원자료를 기준으로 감사했다.

## 재검증 로그

2026-09-02 현재 workspace의 파일을 다시 읽어 26개 디렉터리·367개 레코드·367개 seed를 재검증했다.

- `_seoul_index.json`: 서울 25개 자치구, 250개 레코드, `total_schema_errors=0`
- 전체 레코드: 생성일 `2026-09-02` 일치, 업종별 인허가 metric 10개 일치, `generator.params.industries` 10개 코드 일치
- Schema·PIP·좌표 변환·원자료 반경 집계·상업 필터·80m dedup 오류: 0
- 행정동 coverage: 자치구 대표점 224/425, 상권 host 236/367로 기존 감사 수치와 일치
- `recommendation_pipeline.py --include-generated-points --limit 20`: 실행 PASS, Schema 오류 0, 합성 후보 6개 모두 `조건부 검토` 이하. 생성 metrics RAG 연결은 2026-09-02 반영 (아래 '추천 파이프라인 연결 상태')

## 보유 범위와 생성 규칙

- 정본 자치구 묶음: 서울 25개 자치구 × 10개 = **250개 레코드**
- 별도 행정동 요청 묶음: `송파구-잠실동` **117개 레코드**
- 디렉터리: 26개, 레코드·seed 합계: 각각 367개
- 행정동 요청 `잠실동`은 공식 행정동 하나가 아니라 `잠실2동·잠실3동·잠실7동`으로 해석되는 별칭 묶음이다.
- 생성 절차: 행정동 경계 PIP → 100m 격자 → 반경 150m 내 영업 중 음식점 인허가 3건 이상 → 역·아파트 80m 이내 제거 → 최원점 표본추출
- 모든 자치구 묶음은 `per_region_limit=10`으로 지역 대표점만 남겼다. 따라서 “자치구 파일”이지 “행정동별 전수 파일”이 아니다.
- 생성 레코드는 10개 외식 업종별 `active_CS1000NN_license_count_500m`를 전부 기록한다(2026-09-02 반영). `generator.params.industries`에 10개 업종 코드와 필터 범위를 명시한다. 상업 필터는 여전히 전체 음식점 인허가 활동 기준이다.
- 상업 필터는 특정 업종 매물이나 상가 공간을 찾은 것이 아니라 **전체 음식점 인허가 활동**을 사용한다. 상가·근생·소매·서비스 시설 전체 커버리지가 아니다.

## 전수 검증 결과

367개 레코드와 367개 seed를 JSON Schema 및 원자료 재계산으로 검사했다.

| 검사 항목 | 결과 | 판정 |
| --- | ---: | --- |
| JSONL 파싱 오류 | 0 | 통과 |
| `gen-evidence-v1` Schema 오류 | 0/367 | 통과 |
| manifest·seed·JSONL 건수 불일치 | 0 | 통과 |
| evidence/anchor ID 중복 | 0 | 통과 |
| seed와 evidence ID 순서 불일치 | 0 | 통과 |
| EPSG:5181 좌표 오류·100m 격자 이탈 | 0 | 통과 |
| EPSG:5181→WGS84 변환값 불일치 | 0 | 통과 |
| 요청 행정동 경계 밖 좌표 | 0 | 통과 |
| 공식 행정동 shape 기준 소속 오류 | 0 | 통과 |
| 반경 150m 음식점 3건 미만 | 0 | 통과 |
| 반경 500m 음식점·업종별 집계 재계산 불일치 | 0 | 통과 (구 감사: 커피만) |
| 역·아파트 80m dedup 위반 | 0 | 통과 |
| 현재 workspace에서 source file 미존재 | 0 | 통과 |
| 상권 host 부착 | 236/367 (64.3%) | 131개는 행정동 배경 fallback |

좌표와 집계 규칙은 정상이나, 위 통과는 “실제 상가가 존재한다”는 검증이 아니다. 생성 좌표가 도로·공원·건물 외부인지, 실제 임대 가능한 호실인지까지는 확인하지 않는다.

## 행정동 커버리지

25개 자치구 manifest의 `region_dongs`는 공식 행정동 shape 기준 425/425개를 열거한다. 그러나 실제 10개 대표점이 놓인 행정동은 **224/425개(52.7%)**뿐이다. 나머지 201개 행정동에는 생성 레코드가 없다.

| 범위 | 실제 생성 레코드 | 레코드가 있는 공식 행정동 | 공식 행정동 수 |
| --- | ---: | ---: | ---: |
| 서울 25개 자치구 묶음 | 250 | 224 | 425 |
| 송파구 `잠실동` 별칭 묶음 | 117 | 3 (`잠실2·3·7동`) | 3 |

`송파구-전체`와 `송파구-잠실동`은 서로 다른 산출물이며, 좌표 1개가 80m 이내로 중복된다. 두 파일을 단순 concat하면 중복 후보가 생길 수 있으므로 별도 scope로 유지해야 한다.

`seoul_gu_dong_list.csv`는 425행이지만 `?`가 들어간 행정동명이 7개 있어(`종로1?2?3?4가동` 등) shape의 `·` 표기와 exact join이 어긋난다. geometry 검증에는 영향이 없었지만 UI 동 목록·문자열 join에는 수정 또는 NFC/구분자 정규화가 필요하다.

## 추천 파이프라인 연결 상태 (2026-09-02 연결 완료)

`--include-generated-points`는 `seeds.json` + **`gridpoint_evidence.jsonl`을 함께 읽는다**(`recommendation_pipeline.py` `load_generated_seeds`가 `evidence_id`로 매칭).

합성 후보(`synthetic_anchor=true`)의 RAG `evidence[]`에 반경 인허가 맥락이 연결된다:
- `active_{요청업종}_license_count_500m`, `active_food_license_count_500m` 2개 evidence item — `spatial_grain=지점`, `source_type=derived`, `source_path=data/인허가/음식점_인허가_서울.csv` + 생성 jsonl, `normalization=radius_license_count_by_industry`, interpretation·limitation에 "경쟁 규모이며 매물·수요·성공 아님 · fit_tier·정렬 미반영".
- `context_notes`에 10개 업종별 인허가 수 전체 브레이크다운.
- `feature_build.coverage.gridpoint_generated_evidence`, `source_freshness.gridpoint_license_context`, `feature_build.sources`에 인허가·생성 jsonl 경로 기록.
- 매칭 실패 시(`gridpoint_evidence.jsonl` 없음/`evidence_id` 불일치) → `missing_features`에 기록, seed 연결은 유지.

역·버스·아파트 metrics는 파이프라인이 실행 시점 원자료로 **재계산**하므로 생성 레코드 값을 재사용하지 않는다(생성↔실행 사이 데이터 갱신 시 실행값이 정본). 인허가 경쟁 metric만 생성 레코드에서 가져온다.

## 재현성과 사용 전 주의

- `source_files`는 현재 workspace에서 모두 확인됐지만, 핵심 원자료 `data/인허가/음식점_인허가_서울.csv`는 `.gitignore` 대상이다. commit만 전달받은 다음 Agent는 같은 367개를 재생성할 수 없다.
- 모든 레코드의 `listing_url`·`address_point`는 null이고, `is_synthetic_anchor=true`다. 이를 실제 상가 주소나 매물로 표현하면 안 된다.
- `active_CS1000NN_license_count_500m`는 해당 업종의 **영업 중 인허가 수**(경쟁 규모)일 뿐 매물 수·공실·성공이 아니다. 10개 업종 전부 기록되므로 코드별로만 해석한다.
- 250개 대표점은 업종 선택·행정동 선택을 위한 전수 coverage가 아니다. 행정동 전수 응답이 필요하면 공식 행정동 단위로 다시 생성하거나, 한 자치구 10점 표본이라는 제한을 UI에 표시해야 한다.

## 다음 조치

1. ~~명칭 고정~~ → 완료(2026-09-02). 문서명 `generated-gridpoint-seed-audit.md`, 명칭 "공개 데이터 파생 격자 합성 후보 seed + 접근성/경쟁 맥락".
2. ~~업종 provenance~~ → 부분 완료(2026-09-02): `generator.params.industries`·`industry_metric_scope`·`commercial_filter_basis` 기록, 10개 업종 metric 전부 산출. 원자료 행 수·checksum은 잔여.
3. ~~`gridpoint_evidence.jsonl` RAG 연결~~ → 완료(2026-09-02). 반경 인허가 metric을 합성 후보 evidence·context_notes에 연결. 역·버스·아파트는 실행 시 재계산.
4. 행정동별 완전성을 요구하면 425개 행정동별 생성 또는 최소 행정동 coverage 표를 추가한다. 현재 자치구 10점 표본은 대체할 수 없다.
5. `seoul_gu_dong_list.csv`의 `?` 구분자 손상을 정정하고 geometry source와 행정동 코드 기반으로 UI 목록을 고정한다.
6. 실제 임대료·면적·주차·공실·상세주소가 필요하면 별도 적법한 매물/제휴 데이터 슬롯을 연결한다.

근거: `scripts/generate_gridpoint_evidence.py`, `artifacts/20-method/generated-evidence-schema.json`, `output/generated_evidence/_seoul_index.json`, `seoul_gu_dong_list.csv`.
