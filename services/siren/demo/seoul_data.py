"""서울시 공개데이터 로더 (데모 baseline 전용).

원천:
  data/인허가/음식점_상권분기_패널.csv        (utf-8-sig) 분기 폐업/개업 (2021Q1~2026Q3)
  data/추정매출/*/*(추정매출-상권)*.csv        (cp949)     분기 추정매출 (2021Q1~2026Q1)
  data/인허가/음식점_인허가_서울.csv           (utf-8-sig) 개별 인허가 좌표+일자 (SR-03)
  data/영역/상권/서울시 상권분석서비스(영역-상권).csv (cp949) 상권 좌표·자치구·행정동
"""

from __future__ import annotations

import csv
import glob
import os
import unicodedata
from pathlib import Path

DEFAULT_DATA_DIR = "/Users/parkjunwoo/Documents/data-analysis/data"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def data_dir() -> Path:
    return Path(os.environ.get("SIREN_SEOUL_DATA_DIR", DEFAULT_DATA_DIR))


def _q_from_yyq(code: str) -> str:
    return f"{code[:4]}Q{code[4]}"


def load_trade_area_meta() -> dict[str, dict]:
    path = data_dir() / "영역/상권/서울시 상권분석서비스(영역-상권).csv"
    meta: dict[str, dict] = {}
    with open(path, encoding="cp949") as f:
        for row in csv.DictReader(f):
            meta[row["상권_코드"]] = {
                "trade_area_code": row["상권_코드"],
                "name": nfc(row["상권_코드_명"]),
                "category": nfc(row["상권_구분_코드_명"]),
                "gu_code": row["자치구_코드"],
                "gu_name": nfc(row["자치구_코드_명"]),
                "admin_dong_code": row["행정동_코드"],
                "admin_dong_name": nfc(row["행정동_코드_명"]),
                "x_5181": float(row["엑스좌표_값"]),
                "y_5181": float(row["와이좌표_값"]),
            }
    return meta


def load_closure_panel() -> dict[tuple[str, str], list[dict]]:
    """(trade_area_code, industry_code) -> [closure quarter dicts], time-ordered."""
    path = data_dir() / "인허가/음식점_상권분기_패널.csv"
    out: dict[tuple[str, str], list[dict]] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (row["상권_코드"], row["업종코드"])
            out.setdefault(key, []).append(
                {
                    "quarter": _q_from_yyq(row["기준_년분기_코드"]),
                    "active_count_end": int(row["영업중_수"]),
                    "new_openings": int(row["신규개업_수"]),
                    "closures": int(row["폐업_수"]),
                    "quarter_status": nfc(row["분기_상태"]),
                }
            )
    for rows in out.values():
        rows.sort(key=lambda r: r["quarter"])
    return out


def _sales_files() -> list[str]:
    files = [
        p
        for p in glob.glob(str(data_dir() / "추정매출/*/*.csv"))
        if nfc("(추정매출-상권)") in nfc(p)
    ]
    return sorted(files)


def load_market_sales() -> dict[tuple[str, str], list[dict]]:
    """(trade_area_code, industry_code) -> [sales quarter dicts], time-ordered, deduped."""
    out: dict[tuple[str, str], dict[str, dict]] = {}
    for path in _sales_files():
        with open(path, encoding="cp949") as f:
            for row in csv.DictReader(f):
                ind = row.get("서비스_업종_코드", "")
                if not ind.startswith("CS1000"):
                    continue
                key = (row["상권_코드"], ind)
                q = _q_from_yyq(row["기준_년분기_코드"])
                out.setdefault(key, {})[q] = {
                    "quarter": q,
                    "amount_krw": float(row["당월_매출_금액"] or 0),
                    "txn_count": int(row["당월_매출_건수"] or 0),
                }
    return {k: [v[q] for q in sorted(v)] for k, v in out.items()}


def load_permits_near(
    centers: list[dict],
    max_radius_m: float = 350.0,
    permit_date_floor: str = "2023-06-01",
) -> dict[str, list[dict]]:
    """For each center {branch_id, x_5181, y_5181}, recent permits within max_radius_m.

    Only permits with ``인허가일자 >= permit_date_floor`` are kept (SR-03 only needs
    the recent-3m / previous-3m windows), which keeps the artifact small.
    Returns branch_id -> [{x, y, industry_code, permit_date, close_date, active}].
    """
    path = data_dir() / "인허가/음식점_인허가_서울.csv"
    buckets: dict[str, list[dict]] = {c["branch_id"]: [] for c in centers}
    r2 = max_radius_m * max_radius_m
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pdate = row["인허가일자"]
            if not pdate or pdate < permit_date_floor:
                continue
            sx, sy = row["좌표X_5181"], row["좌표Y_5181"]
            if not sx or not sy:
                continue
            x, y = float(sx), float(sy)
            rec = None
            for c in centers:
                dx, dy = x - c["x_5181"], y - c["y_5181"]
                if dx * dx + dy * dy <= r2:
                    if rec is None:
                        rec = {
                            "x_5181": round(x, 2),
                            "y_5181": round(y, 2),
                            "industry_code": row["업종코드"] or None,
                            "permit_date": pdate,
                            "close_date": row["폐업일자"] or None,
                            "active": nfc(row["영업상태명"]).startswith("영업"),
                        }
                    buckets[c["branch_id"]].append(rec)
    return buckets
