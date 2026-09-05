"""PR #14 리뷰(ziholee) 회귀 테스트 — 층별용도 부분 수집을 구 전체 완료로 오판하지 않는지.

이전엔 층별용도_{구}.csv에 데이터가 1행이라도 있으면 그 구의 모든 법정동을
`_fully_covered_dong_codes()`가 완료로 반환했다. run_gu()는 API 예산 소진으로
일부 법정동만 처리한 채 부분 파일을 저장할 수 있으므로, 그 구의 나머지 법정동이
--targeted에서 영구 제외될 위험이 있었다(2026-09-05 발견).
"""
import csv
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_building_register as ibr  # noqa: E402


class FullyCoveredDongCodesTests(unittest.TestCase):
    def test_partial_file_only_covers_dongs_actually_present(self):
        orig_out_dir = ibr.OUT_DIR
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ibr.OUT_DIR = Path(tmp)
            try:
                # 법정동 두 개(1111000000, 1111100000)가 대상인 구인데,
                # 실제로는 1111000000 소속 PNU 행만 수집돼 있다(부분 수집 시나리오).
                path = ibr.OUT_DIR / "층별용도_테스트구.csv"
                with path.open("w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=[
                        "mgmBldrgstPk", "PNU", "지번주소", "도로명주소", "층구분",
                        "층번호", "층번호명", "층면적_㎡", "용도코드", "용도", "상세용도", "용도군", "구조",
                    ])
                    w.writeheader()
                    w.writerow({
                        "mgmBldrgstPk": "PK1", "PNU": "1111000000" + "1" + "0001" + "0000",
                        "지번주소": "", "도로명주소": "", "층구분": "지상", "층번호": "1",
                        "층번호명": "1층", "층면적_㎡": "50", "용도코드": "", "용도": "소매점",
                        "상세용도": "", "용도군": "근린생활1", "구조": "",
                    })

                covered = ibr._fully_covered_dong_codes()
                self.assertIn("1111000000", covered, "실제 데이터가 있는 법정동은 covered여야 한다")
                self.assertNotIn(
                    "1111100000", covered,
                    "데이터가 전혀 없는 법정동을 구 전체 완료로 오판하면 안 된다(핵심 회귀 지점)",
                )
            finally:
                ibr.OUT_DIR = orig_out_dir


if __name__ == "__main__":
    unittest.main()
