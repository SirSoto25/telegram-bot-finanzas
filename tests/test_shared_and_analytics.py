import asyncio
import unittest
from datetime import datetime, timezone

from finance_analytics import _build_anomalies, _format_panel_text
from finance_shared import _cb_suffix_int, _extract_tags, _month_window, parse_amount, session_is_expired
from handlers_registry import register_handlers
from finance_state import get_accounts, get_or_create_user
from finance_ui import _acct_kb, _confirm_kb, _kb, multi_kb


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


class _FakeApplication:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler):
        self.handlers.append(handler)


class _StateCursor:
    def __init__(self, row=None, rows=None, lastrowid=None):
        self._row = row
        self._rows = rows or []
        self.lastrowid = lastrowid

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _StateDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SELECT id FROM users"):
            return _StateCursor(row=None)
        if sql.startswith("INSERT INTO users"):
            return _StateCursor(lastrowid=42)
        if sql.startswith("SELECT * FROM accounts"):
            return _StateCursor(rows=[{"id": 1, "name": "Caja", "balance": 100.0}])
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        return None


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

    def test_session_expiry_handles_timezone_awareness(self):
        created = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 7, 8, 10, 31, 0, tzinfo=timezone.utc)
        self.assertTrue(session_is_expired(created, 30, now=now))


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

    def test_handler_registry_registers_expected_commands(self):
        app = _FakeApplication()
        handlers = {name: (lambda *args, **kwargs: None) for name in [
            "cmd_start", "cmd_help", "cmd_menu", "cmd_cancel", "cmd_cuentas",
            "cmd_nueva_cuenta", "cmd_borrar_cuenta", "cmd_gasto", "cmd_ingreso",
            "cmd_traspaso", "cmd_deshacer", "cmd_redondeo", "cmd_redondeo_toggle",
            "cmd_redondeo_cuenta", "cmd_recurrente", "cmd_agregar_recurrente",
            "cmd_borrar_recurrente", "cmd_resumen", "cmd_stats", "cmd_tendencia",
            "cmd_panel", "cmd_forecast", "cmd_anomalias", "cmd_tags",
            "cmd_sugerircategoria", "cmd_exportar", "cmd_alertas",
            "cmd_agregar_alerta", "cmd_borrar_alerta", "cmd_reset",
            "cmd_presupuesto", "cmd_presupuestoset", "cmd_buscar", "cmd_metas",
            "cmd_nuevameta", "cmd_aportarmeta", "cmd_agregaringresorecurrente",
            "cmd_ingresorecurrente", "handle_menu_callback",
            "handle_resumen_callback", "handle_budget_callback",
            "handle_callback", "handle_flow_callback", "handle_text",
        ]}
        register_handlers(app, handlers)
        self.assertGreaterEqual(len(app.handlers), 40)

    def test_state_helpers_use_database_adapter(self):
        db = _StateDB()
        self.assertEqual(asyncio.run(get_or_create_user(db, 9)), 42)
        self.assertEqual(asyncio.run(get_accounts(db, 9))[0]["name"], "Caja")

    def test_keyboard_builders(self):
        kb = _kb([("A", "a"), ("B", "b")])
        self.assertEqual(len(kb.inline_keyboard), 2)
        ack = _acct_kb([{"name": "Caja", "balance": 12.3, "id": 7}], "exp_acc")
        self.assertEqual(len(ack.inline_keyboard), 1)
        mkb = multi_kb([("Uno", "1"), ("Dos", "2")], "type")
        self.assertEqual(len(mkb.inline_keyboard), 2)
        ckb = _confirm_kb("ok", "ignored")
        self.assertEqual(len(ckb.inline_keyboard), 2)


if __name__ == "__main__":
    unittest.main()
