# 원천 데이터 취득 (저장소 미포함)

이 저장소에는 `data/` · `output/` 이 없다. 서빙(`recommendation_pipeline.py --source db`)은
PostgreSQL 에서만 읽으므로 데이터 파일이 필요 없고, 이식(`scripts/migrate_postgres*.py`)
할 때만 아래 원천 파일이 필요하다.

## 이식에 필요한 원천

| 폴더 | 내용 | 취득처 |
| --- | --- | --- |
| `data/영역/{상권,상권배후지,행정동}/` | 공간 폴리곤 shp | 서울 열린데이터광장 상권분석서비스 — 영역 |
| `data/점포/{YYYY}년/` | 상권분석 (점포) 분기 CSV | 〃 — 점포 |
| `data/추정매출/{YYYY}/` | 상권분석 (추정매출) 분기 CSV | 〃 — 추정매출 |
| `data/길단위인구/` | 상권분석 (길단위인구) 분기 CSV | 〃 — 길단위인구 |
| `data/상권변화지표/` | 상권분석 (상권변화지표) 분기 CSV | 〃 — 상권변화지표 |
| `data/상주인구/` `data/직장인구/` | 상권분석 상주·직장인구 | 〃 |
| `data/공동주택/아파트단지_서울.csv` | K-apt 단지 + 지오코딩 | `scripts/ingest_apartment_complex.py` + `scripts/geocode.py` |
| `data/도시철도역사/역사정보_서울.csv` | 도시철도 역사 | `scripts/ingest_subway_stations.py` |
| `data/버스정류장/버스정류소_서울.csv` | 버스 정류소 | `scripts/ingest_bus_stops.py` |
| `data/카카오POI/*.csv` | 카카오 로컬 POI 스냅샷 | `scripts/ingest_kakao_poi*.py` |
| `data/네이버트렌드/업종_검색트렌드_월.csv` | 네이버 데이터랩 업종 검색 트렌드 | `scripts/ingest_naver_trend.py` |
| `data/뉴스/*.jsonl` (+ `_manifest.json`) | 빅카인즈·네이버 뉴스 스냅샷 | `scripts/ingest_bigkinds_news.py` · `ingest_naver_news_snapshot.py` |
| `data/임대료/R-ONE_임대동향_분기.csv` | 한국부동산원 R-ONE 임대동향 | `scripts/ingest_rent_trend.py` |
| `data/인허가/음식점_인허가_서울.csv` | 식품 인허가 (음식점) | `scripts/ingest_food_license.py` |
| `output/feature_validation/naver_seasonality.csv` | 네이버 계절성 파생 | `scripts/analyze_naver_seasonality.py` |
| `output/crosswalks/crosswalk_rone_trdar.csv` | R-ONE↔상권 명칭 crosswalk | `scripts/build_rone_trdar_crosswalk.py` |
| `output/generated_evidence/` | 격자 생성 좌표 근거 (옵션, `--include-generated-points`) | `scripts/generate_gridpoint_evidence.py` |

또는 분석 저장소(`data-analysis`)에서 통째로 복사.

## 이식 순서

```bash
psql "$DATABASE_URL" -f db/001_location_schema.sql
.venv/bin/python3 scripts/migrate_postgres.py --phase all --years 2021 2022 2023 2024 2025 2026
.venv/bin/python3 scripts/migrate_postgres_context.py --phase all
```

세부는 [`postgres-migration-plan.md`](postgres-migration-plan.md).
