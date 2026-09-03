# 빅카인즈 뉴스 추천 파이프라인 인수인계

> 이 변경은 **GPT(Codex)가 2026-09-02에 구현·검증**했다. 다음 작업자는 뉴스
> 연결부를 다시 전체 탐색하지 말고 이 문서와 아래 고정 파일부터 확인한다.
>
> **2026-09-03 개정 (Claude Code)**: 지역 매칭 텍스트 축소. 자치구·행정동 태그는
> **제목 부분문자열 + `위치` 필드 토큰 정확일치**로만 계산한다(`_match_tokens`).
> `키워드`·`특성추출`·`통합 분류`·`기관`은 짧은 행정동명 오탐이 많아 지역
> 매칭에서 제외했고, 주제·서울 scope 판정에만 쓴다. 빅카인즈 재-ingest 완료
> (2,522행 불변, sigungu-tagged 1885→1817, dong-tagged 1481→1396).
> `위치`는 다중 지명 추출이라 곁다리 행정동이 남을 수 있어(묵동·번동 잔여 오탐)
> `evidence[]` limitation에 "제목 언급이 더 강한 신호"로 명시. 검토 기록은
> `artifacts/30-review/recommendation-quality-review.md`(2026-09-03 절), 결함
> 케이스 F29.
>
> **최신성**: `source_freshness.bigkinds_news`에 `observed_end_period`(=검색기간
> 종료일)·`retrieved_at_utc`(=`ingested_at_utc`)·`snapshot_age_days`·`is_stale`
> (`NEWS_SNAPSHOT_STALE_DAYS`=45 초과)·`update_cadence="manual_snapshot"` 기록.
> stale이면 `context_notes` 1줄 + `coverage-summary.json` `news_context.any_stale`.
> 판정·정렬 불변. 새 snapshot을 추가할 때 SHA-256·query metadata 사람 확인은
> 종전과 동일.

## 현재 상태

다운로드 파일 `서울시 랜드마크 시설 계획20240101-20260901.xlsx`를 빅카인즈
export로 확인하고, `scripts/ingest_bigkinds_news.py`로 다음 산출물을 생성했다.

- `data/뉴스/bigkinds_news_서울시_랜드마크_시설_계획_20240101_20260901.jsonl`
- `data/뉴스/bigkinds_news_서울시_랜드마크_시설_계획_20240101_20260901_manifest.json`

정규화 결과는 원본 2,616행 중 제외 표시 71행과 URL 중복 23행을 제거한
2,522행이다. 원본 본문은 저장하지 않는다. 검색어·기간은 export 내부 필드가
아니어서 manifest의 `query_metadata_verified=false`와
`filename_or_cli_inferred`로 명시했다.

## 코드 연결 지점

1. `scripts/ingest_bigkinds_news.py`
   - 외부 패키지 없는 XLSX XML parser
   - 분석제외(`중복`/`예외`) 처리
   - 뉴스 식별자 → URL → 일자·제목·언론사 중복 제거
   - 서울 자치구·행정동 metadata term match
   - 공식 행정동명과 통용명 alias(예: `잠실2동`↔`잠실동`) 저장
   - 주제 태그: 랜드마크·재개발·재건축·도시개발·복합개발·정비사업·MICE·시설·계획
2. `scripts/recommendation_pipeline.py`
   - `NewsCatalog`: 정규화 snapshot loader (`topic_match_count` 포함)
   - `news_context_for_candidate()`: 후보의 자치구·행정동별 기사량 + `topic_match_rate` 계산
   - `build_candidate()`: `FC-51_뉴스_자치구기사량`·`FC-51_뉴스_행정동기사량`을
     `evidence[]`에 추가(2026-09-03 이름에서 `_시설개발_` 제거 — snapshot이 이미
     주제 한정 검색이라 필터 강도 과장이었음). interpretation·context_notes에
     `topic_match_rate`·주제태그 상위 4개 노출
   - `--no-news-context`: 뉴스 제외 비교 QA용
3. `artifacts/20-method/rag-evidence-schema.json`
   - 뉴스 정규화 enum과 FC-51-news 중립 맥락 설명 추가

## 실행 예시

```bash
./.venv/bin/python3 scripts/recommendation_pipeline.py \
  --sido 서울특별시 --sigungu 송파구 --dong 잠실동 \
  --industry-code CS100010 \
  --out output/recommendation_runs/jamsil-coffee-news-mvp --limit 30
```

검증 완료 산출물:

- `output/recommendation_runs/jamsil-coffee-news-mvp/`
- `output/recommendation_runs/yeonnam-coffee-news-mvp/`
- 두 실행 모두 `schema_error_count=0`, `bigkinds_news.matched=후보 수`
- `artifacts/30-review/recommendation-quality-review.md`의 뉴스 QA 항목 참조

## 의미와 금지사항

- 뉴스 기사량은 보도량의 보조 맥락이다. 공식 도시계획사업·정비사업의 추진단계나
  확정 여부를 대신하지 않는다.
- 기사량을 성공확률, 수요, 매출, 공실, 임대 가능성으로 해석하지 않는다.
- `reasons`·`counter_evidence`·`fit_tier`·정렬·예측모델에 넣지 않는다.
- 행정동 alias 매칭은 여러 공식 동에 같은 기사가 잡힐 수 있으므로 evidence의
  limitation과 `exact` 건수를 함께 확인한다.
- 빅카인즈 export는 검색어·기간 메타데이터를 내부에서 검증할 수 없으므로 새
  snapshot을 추가할 때 CLI 값을 실제 export 조건과 대조하고 manifest를 갱신한다.

## 남은 작업

- 최신 snapshot 재생성 시 원본 파일 SHA-256과 query metadata를 사람 확인
- 빅카인즈 API/공식 집계 접근 시 동일 normalized schema로 교체하고 호출 예산 기록
- 기사량과 `data/도시계획사업/`의 공식 사업 레코드를 사업명·자치구·위치로 별도
  교차검증할지 결정
- headline/topic 정밀도와 행정동 exact/alias coverage를 여러 자치구에서 재검증
