# R-ONE Open API 인수인계

> **작성 주체:** GPT(Codex)  
> **작성일:** 2026-09-02  
> **목적:** Claude Code가 R-ONE API 키·엔드포인트·응답 구조를 다시 전체 탐색하지 않고 후속 이식 작업을 이어가기 위한 포인터.

## 결론

사용자의 `RONE_API_KEY`로 한국부동산원 R-ONE Open API 실호출을 완료했다. 통계표 목록과 표 데이터 모두 `HTTP 200`/`INFO-000`으로 응답했으며, 입지 분석에 직접 관련된 중대형 상가 임대료·공실률 표에서 서울 기준 2026년 2분기 값까지 확인했다.

## 공식 사용법

공식 개발가이드: <https://www.reb.or.kr/r-one/portal/openapi/openApiDevPage.do>

- Base URL: `https://www.reb.or.kr/r-one/openapi/`
- 통계표 목록: `GET /SttsApiTbl.do`
- 통계자료: `GET /SttsApiTblData.do`
- 인증: query parameter `KEY`
- 형식: `Type=json` (미지정 시 XML)
- 페이지: `pIndex`(1부터), `pSize`
- 통계자료 주요 파라미터: `STATBL_ID`, `DTACYCLE_CD`, `CLS_ID`, `ITM_ID`, 선택적 `START_WRTTIME`, `END_WRTTIME`
- 운영 호출은 가능한 한 `CLS_ID`·`ITM_ID`를 함께 지정한다. 생략하면 여러 지역·지표가 섞일 수 있다.

## 저장소 구현

- 키 템플릿: `.env.example`의 `RONE_API_KEY=`
- 키 로더: `scripts/_env.py`의 `require("RONE_API_KEY")`
- API 래퍼: `scripts/rone_api.py`
- 기존 파일 이식: `scripts/ingest_rent_trend.py` → `data/임대료/R-ONE_임대동향_분기.csv`

래퍼는 API 원본 JSON을 기본 출력하며, `--summary`를 사용하면 응답 건수와 첫/마지막 행만 출력한다. 키는 URL·로그에 노출하지 않는다.

## 재현 명령

```bash
# 통계표 목록에서 임대료 표를 찾기
PYTHONPATH=scripts .venv/bin/python3 scripts/rone_api.py tables --contains 임대료

# 서울 중대형 상가 임대료: 2024Q3~ 최신 응답 요약
PYTHONPATH=scripts .venv/bin/python3 scripts/rone_api.py data \
  --statbl-id T244363134858603 --cycle QY \
  --cls-id 500002 --itm-id 100001 --summary

# 서울 중대형 상가 공실률: 2024Q3~ 최신 응답 요약
PYTHONPATH=scripts .venv/bin/python3 scripts/rone_api.py data \
  --statbl-id T249633134845544 --cycle QY \
  --cls-id 500002 --itm-id 100001 --summary
```

실제 확인 결과:

| 표 | 표 ID | 서울 `CLS_ID` | 지표 `ITM_ID` | 반환 기간 | 단위 | 첫 값 → 마지막 값 |
| --- | --- | ---: | ---: | --- | --- | ---: |
| 중대형 상가 임대료 | `T244363134858603` | 500002 | 100001 | 2024Q3~2026Q2 | 천원/㎡ | 54.7556 → 56.7081 |
| 중대형 상가 공실률 | `T249633134845544` | 500002 | 100001 | 2024Q3~2026Q2 | % | 8.6563 → 9.6720 |

응답 행의 핵심 필드는 `WRTTIME_IDTFR_ID`, `WRTTIME_DESC`, `CLS_ID`, `CLS_FULLNM`, `ITM_ID`, `ITM_NM`, `DTA_VAL`, `UI_NM`이다.

## 현재 표 ID 참고

2024년 3분기 이후 표 목록에서 확인한 상가 유형별 ID는 다음과 같다. ID는 API 표 목록에서 다시 확인할 수 있으며, 코드를 영구적인 의미값으로 간주하지 않는다.

| 지표 | 오피스 | 중대형 상가 | 소규모 상가 | 집합 상가 |
| --- | --- | --- | --- | --- |
| 임대료 | `TT249843134237374` | `T244363134858603` | `T248223134698125` | `T244913134948657` |
| 공실률 | `TT244763134428698` | `T249633134845544` | `T241833134686576` | `T243283134931290` |

확인된 지역 코드 예시는 `CLS_ID=500001` 전국, `CLS_ID=500002` 서울, `CLS_ID=510003` 서울>도심, `CLS_ID=510004` 서울>강남이다. 임대료·공실률 표의 확인된 지표 코드는 `ITM_ID=100001`이다.

## 후속 작업과 설계 경계

1. ~~API 응답을 `data/임대료/`의 기존 long 스키마로 변환하는 갱신 스크립트를 별도로 만든다.~~ **완료(2026-09-05)**: `scripts/ingest_vacancy_rate.py` → `data/임대료/R-ONE_공실률_분기.csv` + `manifest_공실률.json`(호출 로그·표 ID 보존).
2. ~~공실률 API 표를 FC-21에 연결할 때는 현재 `매장용빌딩...csv` 권역 데이터와 기간·정의·상가유형을 비교한다.~~ **완료(2026-09-05)**: `매장용빌딩...csv`는 2026-08-31 운영 제외 데이터 삭제로 이미 소실되어 비교 대상 자체가 없음 — API 실호출 값을 FC-21 유일 소스로 채택. `context.rent_index`(store_type='소규모상가', indicator='공실률')에 적재, `recommendation_pipeline.py`가 crosswalk `join_eligible=yes` 경유로 evidence 연결(상세: [[vacancy_rate_fc21_ingest]] 메모리, `output/cost_dimension/`).
3. R-ONE 상권명/권역은 서울시 상권분석서비스의 상권·상권배후지·행정동과 동일 grain이 아니다. GPT(Codex)가 2026-09-02 1차 명칭 proxy crosswalk를 `output/crosswalks/crosswalk_rone_trdar.csv`에 생성했지만, `join_eligible=yes`만 후보 연결에 사용하고 review·복합권역·미해결은 보류한다. 상세 규칙은 `artifacts/handoff_rone_trdar_crosswalk.md`를 먼저 읽는다.
4. R-ONE 임대료·공실률은 비용/시장 배경 근거다. 매출 성공 여부, 성공확률, 임대 매물 존재를 뜻하지 않는다.

## 보안

- 실제 API 키는 `.env`에만 둔다. `.env.example`, 문서, 로그, 커밋에는 넣지 않는다.
- Claude Code에서 키 값 자체를 출력하거나 URL을 복사해 공유하지 않는다.
