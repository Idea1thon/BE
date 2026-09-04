"""데모/대회용 합성 데이터 생성기.

`service/siren/` 코어(순수 계산)와 분리된 서브패키지다. 여기서만 파일을 읽고
합성 데이터를 만든다. 코어 계산기는 이 모듈을 import 하지 않는다.

산출물:
  artifacts/risk-siren/demo/market_baseline.json   상권×업종 실측 집계 (서울시 공개데이터)
  artifacts/risk-siren/demo/branch_reports/*.json  가맹점별 24개월 운영보고서 + 리뷰 (합성)
  artifacts/risk-siren/demo/requests/*.json        RiskSirenRequest 페이로드
  artifacts/risk-siren/demo/results/*.json         analyze() 결과
  artifacts/risk-siren/demo/hq_summary.json        본사 집계
"""
