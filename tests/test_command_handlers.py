import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from telegram.constants import ParseMode


class _Reply:
    def __init__(self):
        self.messages = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kwargs):
        self.messages.append({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})


class _Cursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self):
        self.executed = []
        self._select_results = {}

    def set_select(self, sql_match, rows=None, row=None):
        self._select_results[sql_match] = _Cursor(rows=rows, row=row)

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))
        for pattern, cursor in self._select_results.items():
            if pattern in sql:
                return cursor
        return _Cursor(rows=[])

    async def commit(self):
        return None


def _make_update(user_id=1):
    u = type("_U", (), {})()
    u.effective_user = type("_EU", (), {"id": user_id})()
    u.effective_chat = type("_EC", (), {"id": user_id})()
    u.effective_message = _Reply()
    u.message = u.effective_message
    return u


class CommandHandlersTests(unittest.TestCase):
    """Characterization tests — capture current command handler behavior."""

    def _call(self, handler, update, *, accounts=None):
        db = _FakeDB()
        if accounts is not None:
            db.set_select("SELECT * FROM accounts", rows=accounts)
        with (
            patch("commands.get_db", return_value=db),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.get_accounts", return_value=accounts or []),
            patch("commands.save_session", AsyncMock()),
            patch("commands.clear_session", AsyncMock()),
        ):
            asyncio.run(handler(update, None))
        return db, update

    def test_cuentas_no_accounts(self):
        from commands import cmd_cuentas
        update = _make_update()
        _, update = self._call(cmd_cuentas, update, accounts=[])
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("No tienes cuentas", msgs[0]["text"])
        self.assertEqual(msgs[0]["parse_mode"], ParseMode.HTML)

    def test_cuentas_with_accounts(self):
        from commands import cmd_cuentas
        accounts = [
            {"name": "Nomina", "type": "NOMINA", "balance": 1500.0},
            {"name": "Ahorros", "type": "AHORROS", "balance": 500.0},
        ]
        update = _make_update()
        _, update = self._call(cmd_cuentas, update, accounts=accounts)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("Nomina", msgs[0]["text"])
        self.assertIn("2000.00", msgs[0]["text"])

    def test_gasto_no_accounts(self):
        from commands import cmd_gasto
        update = _make_update()
        _, update = self._call(cmd_gasto, update, accounts=[])
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("crear una cuenta", msgs[0]["text"])

    def test_gasto_starts_flow(self):
        from commands import cmd_gasto
        accounts = [{"name": "Nomina", "type": "NOMINA", "balance": 1500.0}]
        update = _make_update()
        _, update = self._call(cmd_gasto, update, accounts=accounts)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("gastaste", msgs[0]["text"])

    def test_ingreso_no_accounts(self):
        from commands import cmd_ingreso
        update = _make_update()
        _, update = self._call(cmd_ingreso, update, accounts=[])
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("crear una cuenta", msgs[0]["text"])

    def test_ingreso_starts_flow(self):
        from commands import cmd_ingreso
        accounts = [{"name": "Nomina", "type": "NOMINA", "balance": 1500.0}]
        update = _make_update()
        _, update = self._call(cmd_ingreso, update, accounts=accounts)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("ingreso", msgs[0]["text"])

    def test_traspaso_insufficient(self):
        from commands import cmd_traspaso
        accounts = [{"name": "Nomina", "type": "NOMINA", "balance": 1500.0, "id": 1}]
        update = _make_update()
        _, update = self._call(cmd_traspaso, update, accounts=accounts)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("menos 2 cuentas", msgs[0]["text"])

    def test_traspaso_shows_selection(self):
        from commands import cmd_traspaso
        accounts = [
            {"name": "Nomina", "type": "NOMINA", "balance": 1500.0, "id": 1},
            {"name": "Ahorros", "type": "AHORROS", "balance": 500.0, "id": 2},
        ]
        update = _make_update()
        _, update = self._call(cmd_traspaso, update, accounts=accounts)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("Transferencia", msgs[0]["text"])

    def test_recurrente_no_records(self):
        from commands import cmd_recurrente
        db = _FakeDB()
        db.set_select("SELECT * FROM recurring_expenses", rows=[])
        with (
            patch("commands.get_db", return_value=db),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.get_accounts", return_value=[]),
            patch("commands.clear_session", AsyncMock()),
        ):
            update = _make_update()
            asyncio.run(cmd_recurrente(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("No tienes", msgs[0]["text"])

    def test_alertas_shows_message(self):
        from commands import cmd_alertas
        db = _FakeDB()
        db.set_select("low_balance_alerts", rows=[
            {"name": "Caja", "balance": 50.0, "threshold": 100.0, "enabled": True},
        ])
        with (
            patch("commands.get_db", return_value=db),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.clear_session", AsyncMock()),
        ):
            update = _make_update()
            asyncio.run(cmd_alertas(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("Caja", msgs[0]["text"])

    def test_presupuesto_no_budgets(self):
        from commands import cmd_presupuesto
        db = _FakeDB()
        db.set_select("budgets", rows=[])
        with (
            patch("commands.get_db", return_value=db),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.clear_session", AsyncMock()),
        ):
            update = _make_update()
            asyncio.run(cmd_presupuesto(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("No tienes", msgs[0]["text"])

    def test_metas_no_goals(self):
        from commands import cmd_metas
        db = _FakeDB()
        db.set_select("savings_goals", rows=[])
        with (
            patch("commands.get_db", return_value=db),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.clear_session", AsyncMock()),
        ):
            update = _make_update()
            asyncio.run(cmd_metas(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("No tienes", msgs[0]["text"])

    def test_buscar_no_keyword(self):
        from commands import cmd_buscar
        update = _make_update()
        update.message.text = "/buscar"
        with (
            patch("commands.get_db", return_value=_FakeDB()),
            patch("commands.get_or_create_user", return_value=1),
            patch("commands.clear_session", AsyncMock()),
        ):
            asyncio.run(cmd_buscar(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("Uso:", msgs[0]["text"])
