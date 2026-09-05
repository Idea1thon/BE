"""PR #14 리뷰(ziholee) 회귀 테스트 — 공실률 일부 API 실패 시 기존 스냅샷 보존.

fetch_all_rows()가 오류 envelope(RESULT.CODE != INFO-000)를 빈 페이지/정상 종료로
취급하면, 상가유형 중 하나만 실패해도 나머지 성공분으로 기존 CSV를 덮어써
그 유형의 공실률이 조용히 사라진다(2026-09-05 발견).
"""
import csv
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "scripts"))

import ingest_vacancy_rate as ivr  # noqa: E402


def _ok_payload(rows: list[dict], total: int) -> dict:
    return {"SttsApiTblData": [
        {"head": [{"list_total_count": total}, {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상"}}]},
        {"row": rows},
    ]}


def _error_payload() -> dict:
    # 실제 R-ONE API 실호출로 확인한 오류/무자료 응답 형태(SttsApiTblData 래퍼 없음).
    return {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}


def _empty_ok_payload(total: int = 0) -> dict:
    return _ok_payload([], total=total)


def _row(cls_fullnm: str, value: float) -> dict:
    return {"CLS_FULLNM": cls_fullnm, "WRTTIME_IDTFR_ID": "202403", "DTA_VAL": value}


class FetchAllRowsErrorDetectionTests(unittest.TestCase):
    def test_error_envelope_raises_instead_of_silent_empty(self):
        with patch.object(ivr, "get_table_data", return_value=_error_payload()):
            with self.assertRaises(ivr.VacancyFetchError):
                ivr.fetch_all_rows("SOME_ID")

    def test_normal_response_does_not_raise(self):
        payload = _ok_payload([_row("서울>강남>강남대로", 5.0)], total=1)
        with patch.object(ivr, "get_table_data", return_value=payload):
            rows = ivr.fetch_all_rows("SOME_ID")
        self.assertEqual(len(rows), 1)

    def test_paginates_until_first_page_total_is_reached(self):
        page_one = _ok_payload([_row("서울>강남>강남대로", 5.0)], total=2)
        page_two = _ok_payload([_row("서울>강남>테헤란로", 6.0)], total=2)

        with patch.object(ivr, "get_table_data", side_effect=[page_one, page_two]) as fetch:
            rows = ivr.fetch_all_rows("SOME_ID")

        self.assertEqual(len(rows), 2)
        self.assertEqual([call.kwargs["page"] for call in fetch.call_args_list], [1, 2])

    def test_empty_later_page_with_unfilled_total_raises(self):
        page_one = _ok_payload([_row("서울>강남>강남대로", 5.0)], total=2)
        page_two = _empty_ok_payload(total=2)

        with patch.object(ivr, "get_table_data", side_effect=[page_one, page_two]):
            with self.assertRaises(ivr.VacancyFetchError):
                ivr.fetch_all_rows("SOME_ID")

    def test_success_envelope_without_total_raises(self):
        payload = _ok_payload([_row("서울>강남>강남대로", 5.0)], total=1)
        del payload["SttsApiTblData"][0]["head"][0]["list_total_count"]

        with patch.object(ivr, "get_table_data", return_value=payload):
            with self.assertRaises(ivr.VacancyFetchError):
                ivr.fetch_all_rows("SOME_ID")


class MainPreservesSnapshotOnPartialFailureTests(unittest.TestCase):
    def test_one_type_failing_keeps_existing_csv_and_exits_nonzero(self):
        old_content = "상가유형,지표,grain,권역,R_ONE_상권,기준_년분기_코드,값\n기존,공실률,상권,강남,강남대로,20243,1.0\n"
        with self._tmp_out() as out_path:
            out_path.write_text(old_content, encoding="utf-8-sig")

            def fake_get_table_data(statbl_id, cycle, **kw):
                if statbl_id == ivr.STATBL_IDS["소규모상가"]:
                    return _error_payload()
                return _ok_payload([_row("서울>강남>강남대로", 3.0)], total=1)

            with patch.object(ivr, "get_table_data", side_effect=fake_get_table_data):
                code = ivr.main()

            self.assertEqual(code, 1)
            self.assertEqual(out_path.read_text(encoding="utf-8-sig"), old_content,
                              "일부 API 실패 시 기존 스냅샷이 그대로 보존돼야 한다")

    def test_required_type_empty_normal_response_keeps_existing_csv(self):
        old_content = "상가유형,지표,grain,권역,R_ONE_상권,기준_년분기_코드,값\n기존,공실률,상권,강남,강남대로,20243,1.0\n"
        with self._tmp_out() as out_path:
            out_path.write_text(old_content, encoding="utf-8-sig")

            def fake_get_table_data(statbl_id, cycle, **kw):
                if statbl_id == ivr.STATBL_IDS["소규모상가"]:
                    return _empty_ok_payload(total=0)
                return _ok_payload([_row("서울>강남>강남대로", 3.0)], total=1)

            with patch.object(ivr, "get_table_data", side_effect=fake_get_table_data):
                code = ivr.main()

            self.assertEqual(code, 1)
            self.assertEqual(out_path.read_text(encoding="utf-8-sig"), old_content,
                             "필수 상가유형의 정상 빈 응답도 기존 스냅샷을 보존해야 한다")

    def _tmp_out(self):
        import tempfile

        class _Ctx:
            def __enter__(ctx_self):
                ctx_self.tmpdir = tempfile.TemporaryDirectory()
                path = Path(ctx_self.tmpdir.name) / "R-ONE_공실률_분기.csv"
                ivr._orig_out = ivr.OUT
                ivr._orig_manifest = ivr.MANIFEST
                ivr.OUT = str(path)
                ivr.MANIFEST = str(Path(ctx_self.tmpdir.name) / "manifest_공실률.json")
                return path

            def __exit__(ctx_self, *exc):
                ivr.OUT = ivr._orig_out
                ivr.MANIFEST = ivr._orig_manifest
                ctx_self.tmpdir.cleanup()

        return _Ctx()


if __name__ == "__main__":
    unittest.main()
