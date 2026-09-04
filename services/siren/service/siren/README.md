# 폐업 위험 사이렌 서비스 (v1)

운영 중인 프랜차이즈 음식점 가맹점의 폐업 위험을 5개 신호·2층 구조로 분석한다.

## 2층 모델

- `market_risk` — 상권×업종 실측 (SR-01 폐업률, SR-02.market 매출 추세, SR-03 경쟁업체). 서울시 공개데이터.
- `branch_risk` — 가맹점 월별 운영보고서 (SR-02.branch 매출 감소, SR-05 수익성 악화).
- `review_signal` — SR-04 리뷰 반응. **보조 신호**로 종합 점수에 미포함.

종합 점수·등급은 두 층이 모두 계산될 때만 산출한다. 시장 통계를 가맹점 폐업 점수로 합산하지 않는다.

## 모듈

| 파일 | 역할 |
| --- | --- |
| `models.py` | v1 요청·응답 계약, 입력 검증, `extra="forbid"` |
| `reports.py` | 월별 운영보고서 → 파생 손익 지표 |
| `risk_signals.py` | 5개 결정론적 계산기 + provisional 정책 |
| `pipeline.py` | 2층 조립, Evidence, data_provenance, 권한별 투영 |
| `alerts.py` | 점주·본사 알림 이벤트, 멱등키, dispatch `disabled` |
| `explanation.py` | Evidence 기반 결정론 설명 (LLM 어댑터는 나중) |
| `hq_summary.py` | 본사 집계 (위험 비율·평균 점수 별도) |
| `api.py` | FastAPI 경계 |
| `demo/` | 대회용 합성 데이터 생성기 (코어와 분리, 파일 접근은 여기서만) |

## 실행

```bash
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
/Users/parkjunwoo/Documents/data-analysis/.venv/bin/uvicorn service.siren.api:app --reload
```

엔드포인트: `POST /internal/risk-sirens/analyze`, `POST /internal/risk-sirens/hq-summary`, `GET /health`.

## 데모 데이터셋

```bash
python -m service.siren.demo.baseline      # artifacts/risk-siren/demo/market_baseline.json (서울시 실측 집계)
python -m service.siren.demo.build --reuse # 20 가맹점 × 24개월 운영보고서 + 결과 + 롤링 타임라인
```

`SIREN_SEOUL_DATA_DIR` 로 서울시 데이터 경로 지정 (기본 `/Users/parkjunwoo/Documents/data-analysis/data`).

## 원칙

- 위험 점수·등급·알림 발동은 결정론적 코드가 계산한다.
- LLM은 입력 해석·리뷰 분류 보조·Evidence 설명만 담당한다 (수치 승격 금지).
- 데이터가 없으면 `missing_data`·`uncertainty`. 안전으로 낮추지 않는다.
- 합성 데이터는 `source`에 `synthetic_*`, 응답 `data_provenance`에 고지.
- 점수 정책 `risk-siren-v1-provisional` — 데이터팀·사람 승인 전 운영 확정값 아님.
- 실제 이메일·인앱 발송은 `disabled`.
