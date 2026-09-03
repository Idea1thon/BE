# 네이버 뉴스 현재 시점 snapshot 인수인계

> 이 변경은 **GPT(Codex)가 2026-09-02에 구현·실호출·검증**했다. 다음 Claude Code
> 실행은 네이버 뉴스 연결부를 다시 전체 탐색하지 말고 이 문서와 아래 고정 파일부터
> 확인한다.
>
> **2026-09-03 개정 (Claude Code)**: 지역 매칭을 **제목만**으로 축소했다(기존
> `title + description`). description은 주제·서울 scope 판정에만 쓰고 저장하지
> 않는다. 기존 snapshot(100행)의 지역 태그는 제목 기준으로 in-place 재계산했다
> (sigungu 39→15, dong 32→23). `seoul_scope`·`topic_tags`는 원 계산 유지.
> manifest에 `region_matching_revised_at_utc`·`region_matching_revision_note`
> 기록. 검토: `recommendation-quality-review.md`(2026-09-03 절), 결함 케이스 F29.
>
> **최신성**: `source_freshness.naver_news_snapshot`에 `observed_end_period`
> (=수집일)·`retrieved_at_utc`·`snapshot_age_days`·`is_stale`
> (`NEWS_SNAPSHOT_STALE_DAYS`=45 초과)·`update_cadence="manual_snapshot"` 기록.
> stale이면 `context_notes` 1줄 + `coverage-summary.json` `news_context.any_stale`.
> 재수집 신호일 뿐 판정·정렬은 불변. 새 결과가 필요하면 아래 수동 명령을 실행해
> snapshot을 교체한다.

## 결정된 운영 범위

- 네이버 뉴스는 현재 시점에 사용자가 직접 검색한 `서울시 재개발` 검색 결과만 사용한다.
- 자동화, cron/heartbeat, 예약 실행, 백그라운드 API 호출은 만들지 않았다.
- 추천 실행 시 `scripts/recommendation_pipeline.py`는 API를 호출하지 않고 이미 저장된
  JSONL snapshot만 읽는다.
- 새 시점 결과가 필요할 때만 수동 수집 명령을 실행해 snapshot 파일을 교체한다.

## 현재 snapshot

수동 수집 시각은 `2026-09-02T13:09:00+00:00`(한국시간 2026-09-02 22:09:00)이다.

| 항목 | 값 |
| --- | --- |
| 검색어 | `서울시 재개발` |
| 정렬 | `date` |
| 쿼리당 반환 상한 | 100건 |
| 원시 반환 | 100건 |
| URL exact 중복 제거 | 0건 |
| 정규화 결과 | 100건 |
| 발행일 범위 | 2026-09-01~2026-09-02 |
| 본문/description | 저장하지 않음 |
| 전체 검색 일치 건수 | 209,542건(전수 목록이 아닌 API 총량 참고값) |

파일:

- `data/뉴스/naver_news_snapshot.jsonl`
- `data/뉴스/naver_news_snapshot_manifest.json`

manifest에는 endpoint, 검색어, 정렬, 쿼리별 `total`, 원시/정규화/중복 제거 건수,
`retrieved_at_utc`, `automation=false`를 남겼다. 제목·발행일·URL·검색어·서울
자치구/행정동·시설/개발 주제 파생 태그만 저장한다.

## 코드 연결

1. `scripts/ingest_naver_news_snapshot.py`
   - Naver API HUB 뉴스 endpoint를 수동으로 한 번씩 호출한다.
   - `.env`의 `NAVER_CLIENT_ID`·`NAVER_CLIENT_SECRET`와 NCP 헤더를 사용한다.
   - 기본 검색어는 `서울시 재개발`이며 `--query`로 명시할 수 있다.
   - 호출 예산은 `NAVER_NEWS_MONTHLY_LIMIT`, `NAVER_NEWS_DAILY_LIMIT`,
     `NAVER_NEWS_PER_RUN_LIMIT`으로 가드한다.
2. `scripts/recommendation_pipeline.py`
   - `load_naver_news_snapshot()`이 고정 JSONL + manifest만 로드한다.
   - `load_news_catalogs()`가 빅카인즈 snapshot과 네이버 snapshot을 함께 읽는다.
   - 후보별 자치구·행정동 기사량을
     `FC-51_네이버뉴스_자치구기사량`·`FC-51_네이버뉴스_행정동기사량`으로
     `evidence[]`에 연결한다(2026-09-03 이름에서 `_시설개발_` 제거 — snapshot이
     이미 "재개발" 검색이라 topic 필터가 전량 통과, 이름이 필터 강도를 과장했음).
     `topic_match_rate`·주제태그 상위 4개도 함께 노출한다.
   - 두 뉴스 채널 모두 `context_notes`·`evidence[]`·`미래신호/FC-51-news`에만
     기록하고 `reasons`·`counter_evidence`·`fit_tier`·정렬·예측모델에는 넣지 않는다.
3. `artifacts/20-method/rag-evidence-schema.json`
   - `naver_news_api_snapshot`, `news_url_exact_dedup` 정규화명을 허용한다.

## 재실행 명령

새 결과가 필요할 때만 사람이 직접 실행한다.

```bash
./.venv/bin/python3 scripts/ingest_naver_news_snapshot.py \
  --query "서울시 재개발" \
  --display 100 --sort date
```

추천 실행:

```bash
./.venv/bin/python3 scripts/recommendation_pipeline.py \
  --sido 서울특별시 --sigungu 송파구 --dong 잠실동 \
  --industry-code CS100010 --limit 30
```

비교 QA에서 두 뉴스 snapshot을 모두 끄려면 `--no-news-context`를 붙인다.

## 검증 결과

- 네이버 JSONL: 100행, URL unique 100, 발행일 2026-09-01~2026-09-02,
  description/body 필드 없음.
- 잠실동 커피 일반 실행: 19 후보, 네이버·빅카인즈 각각 19/19 coverage,
  `schema_error_count=0`.
- 뉴스 포함/제외 비교: 후보 19개, ID·순서·fit tier·긍정/반대 근거·위치·신뢰도
  변경 0건.
- `validate_harness.py`: `PASS: harness structure and core contracts are present`.

## 해석 제한

- 네이버 API의 `total`은 전체 일치 건수 참고값이며, 저장한 100건/쿼리는 현재 상위
  결과 snapshot이다.
- 기사 보도량은 공식 도시계획사업의 확정·추진단계·정확한 사업구역·상권 수요·창업
  성공을 뜻하지 않는다.
- 행정동명은 제목과 description에서 메모리상 파생했으며 description 원문은 버린다.
  통용명 alias는 여러 공식 행정동에 같은 기사가 잡힐 수 있다.
- 뉴스는 지역특성과 좋은 입지를 직접 판정하는 점수가 아니라, 공식
  도시계획사업·정비사업을 보완하는 근거 맥락이다.
