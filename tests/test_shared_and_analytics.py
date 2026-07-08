import asyncio
import unittest
from datetime import datetime

from finance_analytics import _build_anomalies, _format_panel_text
from finance_shared import _cb_suffix_int, _extract_tags, _month_window, parse_amount


class _FakeCursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, monthly_totals):
        self.monthly_totals = monthly_totals

    async def execute(self, sql, params=()):
        if "GROUP BY" in sql:
            raise AssertionError("unexpected grouped query")
        if "FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?" in sql:
            uid, start, end = params
            key = (uid, start[:7])
            rows = [
                {"category": category, "amount": amount}
                for category, amount in self.monthly_totals.get(key, {}).items()
            ]
            return _FakeCursor(rows=rows)
        if "SELECT type,amount,category,description FROM transactions" in sql:
            return _FakeCursor(rows=[
                {"type": "INGRESO", "amount": 1000.0, "category": "Sueldo", "description": ""},
                {"type": "GASTO", "amount": 250.0, "category": "Comida", "description": "Cena #food"},
                {"type": "GASTO", "amount": 120.0, "category": "Transporte", "description": "Taxi #move"},
            ])
        if "SELECT * FROM accounts WHERE user_id=? ORDER BY created_at" in sql:
            return _FakeCursor(rows=[{"balance": 300.0}, {"balance": 200.0}])
        raise AssertionError(f"unexpected query: {sql}")


class SharedHelpersTests(unittest.TestCase):
    def test_extract_tags(self):
        self.assertEqual(_extract_tags("Compra #Food #Bills"), ["food", "bills"])

    def test_callback_suffix_int(self):
        self.assertEqual(_cb_suffix_int("acc_123", "acc_"), 123)
        self.assertIsNone(_cb_suffix_int("acc_x", "acc_"))

    def test_parse_amount(self):
        self.assertEqual(parse_amount("12.5"), 12.5)
        self.assertIsNone(parse_amount("abc"))

    def test_month_window_bounds(self):
        start, end = _month_window(datetime(2026, 7, 8, 11, 40, 53))
        self.assertEqual(start.day, 1)
        self.assertEqual(end.day, 31)
        self.assertEqual(start.hour, 0)
        self.assertEqual(end.hour, 23)


class AnalyticsTests(unittest.TestCase):
    def test_anomaly_detection_and_panel_output(self):
        monthly_totals = {
            (1, "2026-07"): {"Comida": 250.0, "Transporte": 120.0},
            (1, "2026-06"): {"Comida": 100.0, "Transporte": 110.0},
            (1, "2026-05"): {"Comida": 90.0, "Transporte": 100.0},
            (1, "2026-04"): {"Comida": 80.0, "Transporte": 90.0},
        }
        db = _FakeDB(monthly_totals)
        anomalies = asyncio.run(_build_anomalies(db, 1))
        self.assertTrue(any(cat == "Comida" for cat, _, _ in anomalies))

        snapshot = {
            "income": 1000.0,
            "expense": 370.0,
            "balance": 630.0,
            "cash": 500.0,
            "projected_balance": 620.0,
            "by_cat": {"Comida": 250.0, "Transporte": 120.0},
            "tags": {"food": 1, "move": 1},
            "now": datetime(2026, 7, 8),
        }
        panel = _format_panel_text(snapshot, anomalies)
        self.assertIn("Panel financiero", panel)
        self.assertIn("Anomalías detectadas", panel)


if __name__ == "__main__":
    unittest.main()
