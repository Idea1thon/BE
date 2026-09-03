"""이식 스크립트의 원천 데이터 루트.

기본: ~/Documents/서울창업입지_원천데이터/
  외국인생활인구/  LONG_FOREIGNER_DONG_*.csv, TEMP_FOREIGNER_DONG_*.csv
  식품인허가/      식품_일반음식점_서울특별시.csv, 식품_휴게음식점_서울특별시.csv
  고용률/          고용률.csv
  도시계획사업/    UQ120_도시계획사업/, 사업장목록.xls
  임대동향/        임대동향 지역별 임대료/임대가격지수 (R-ONE 상권별)

.env 의 RAW_DATA_DIR 로 위치를 덮어쓸 수 있고, 각 스크립트의 --src 가 최우선.
"""
import os

from _env import load_env

load_env()
RAW_ROOT = os.environ.get("RAW_DATA_DIR", "").strip() or \
    os.path.expanduser("~/Documents/서울창업입지_원천데이터")


def raw(*parts: str) -> str:
    return os.path.join(RAW_ROOT, *parts)
