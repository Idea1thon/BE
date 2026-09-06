"""Non-finite external numbers must fail before producing scores or alerts."""

import json
import unittest

from fastapi.testclient import TestClient

from services.siren.api import app
from services.siren.tests.test_risk_siren import complete_payload


class FiniteInputHttpTest(unittest.TestCase):
    def setUp(self):
        self.client = self.enterContext(TestClient(app, raise_server_exceptions=False))

    def test_nonfinite_strings_are_rejected_at_the_input_field(self):
        paths = (
            ("branch_reports", 23, "sales", "hall", "credit"),
            ("branch_reports", 23, "labor", "fulltime"),
            ("market_data", "sales_quarters", 7, "amount_krw"),
            ("market_data", "competition", "radius_m"),
            ("location", "x_5181"),
            ("location", "y_5181"),
        )
        for path in paths:
            for value in ("Infinity", "-Infinity", "NaN"):
                with self.subTest(path=path, value=value):
                    payload = complete_payload()
                    target = payload
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value
                    response = self.client.post("/internal/risk-sirens/analyze", json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn(
                        ["body", *path],
                        [error["loc"] for error in response.json()["detail"]],
                    )

    def test_json_number_overflow_returns_serializable_validation_error(self):
        payload = complete_payload()
        payload["branch_reports"][-1]["labor"]["fulltime"] = "overflow-placeholder"
        body = json.dumps(payload).replace('"overflow-placeholder"', "1e309")
        response = self.client.post(
            "/internal/risk-sirens/analyze",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "branch_reports", 23, "labor", "fulltime"],
        )

    def test_finite_payload_still_produces_a_calculated_result(self):
        response = self.client.post("/internal/risk-sirens/analyze", json=complete_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["risk"]["calculation_status"], "calculated")

    def test_ordinary_validation_error_preserves_details_and_finite_input(self):
        payload = complete_payload()
        payload["market_data"]["competition"]["radius_m"] = -1.5
        response = self.client.post("/internal/risk-sirens/analyze", json=payload)
        self.assertEqual(response.status_code, 422)
        error = response.json()["detail"][0]
        self.assertEqual(error["loc"], ["body", "market_data", "competition", "radius_m"])
        self.assertEqual(error["type"], "greater_than")
        self.assertEqual(error["input"], -1.5)
        self.assertEqual(error["ctx"], {"gt": 0.0})
