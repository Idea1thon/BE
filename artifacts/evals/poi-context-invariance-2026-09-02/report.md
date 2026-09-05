# Kakao POI context 불변성 QA

- 생성: GPT(Codex), 2026-09-02T10:34:56.840005+00:00
- 요청: 송파구 잠실동 · 10개 업종
- 결과: PASS
- 불변 조건: 후보 ID·정렬, fit_tier, 긍정/반대 근거, FC-11 변화지표 코드, entry_health 라벨
- POI 경계: complete context의 지점 grain 관측만 허용하며 FC·성공 outcome·등급·정렬 입력이 아니다.

| 업종 | 후보 수 | 결과 | 오류 |
| --- | ---: | --- | ---: |
| CS100001 | 27 | PASS | 0 |
| CS100002 | 27 | PASS | 0 |
| CS100003 | 27 | PASS | 0 |
| CS100004 | 27 | PASS | 0 |
| CS100005 | 27 | PASS | 0 |
| CS100006 | 27 | PASS | 0 |
| CS100007 | 27 | PASS | 0 |
| CS100008 | 27 | PASS | 0 |
| CS100009 | 27 | PASS | 0 |
| CS100010 | 27 | PASS | 0 |
