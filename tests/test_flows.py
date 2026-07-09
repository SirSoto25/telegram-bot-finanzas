import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from telegram.constants import ParseMode


class _Reply:
    def __init__(self):
        self.messages = []

    async def reply_text(self, text, parse_mode=None, **kwargs):
        self.messages.append({"text": text, "parse_mode": parse_mode})


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
        if "UPDATE" in sql or "INSERT" in sql or "DELETE" in sql:
            return _Cursor(lastrowid=1)
        return _Cursor(rows=[])

    async def commit(self):
        return None


def _make_update(user_id=1, text=""):
    u = type("_U", (), {})()
    u.effective_user = type("_EU", (), {"id": user_id})()
    u.effective_chat = type("_EC", (), {"id": user_id})()
    u.effective_message = _Reply()
    u.message = u.effective_message
    u.message.text = text
    return u


class FlowStateMachineTests(unittest.TestCase):
    """Characterization tests for text-input flow handlers."""

    def _call(self, handler, update, *, session_state=None, session_data=None):
        db = _FakeDB()
        session_row = None
        if session_state:
            session_row = {
                "state": session_state,
                "data": json.dumps(session_data or {}),
                "created_at": datetime.now().isoformat(),
            }
        with (
            patch("bot_pythonanywhere.get_db", return_value=db),
            patch("bot_pythonanywhere.get_or_create_user", return_value=1),
            patch("bot_pythonanywhere.get_accounts", return_value=[]),
            patch("bot_pythonanywhere.get_session", AsyncMock(return_value=session_row)),
            patch("bot_pythonanywhere._check_session_expiry", AsyncMock(return_value=False)),
            patch("bot_pythonanywhere.save_session", AsyncMock()),
            patch("bot_pythonanywhere.clear_session", AsyncMock()),
        ):
            asyncio.run(handler(update, None))
        return db, update

    def test_no_session_shows_prompt(self):
        from bot_pythonanywhere import handle_text
        update = _make_update(text="hello")
        _, update = self._call(handle_text, update, session_state=None)
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("/start", msgs[0]["text"])

    def test_expired_session_shows_prompt(self):
        from bot_pythonanywhere import handle_text
        with patch("bot_pythonanywhere._check_session_expiry", AsyncMock(return_value=True)):
            update = _make_update(text="hello")
            with (
                patch("bot_pythonanywhere.get_db", return_value=_FakeDB()),
                patch("bot_pythonanywhere.get_session", AsyncMock(return_value={"state": "x", "data": "{}", "created_at": "old"})),
            ):
                asyncio.run(handle_text(update, None))
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("expirada", msgs[0]["text"])

    def test_cancel_text_clears_session(self):
        from bot_pythonanywhere import handle_text
        db = _FakeDB()
        with (
            patch("bot_pythonanywhere.get_db", return_value=db),
            patch("bot_pythonanywhere.get_session", AsyncMock(return_value={"state": "waiting_expense_amount", "data": "{}", "created_at": datetime.now().isoformat()})),
            patch("bot_pythonanywhere._check_session_expiry", AsyncMock(return_value=False)),
            patch("bot_pythonanywhere.clear_session", AsyncMock()) as mock_clear,
        ):
            update = _make_update(text="/cancel")
            asyncio.run(handle_text(update, None))
            mock_clear.assert_called_once()
        self.assertIn("cancelada", update.effective_message.messages[0]["text"])

    def test_unknown_state_shows_prompt(self):
        from bot_pythonanywhere import handle_text
        update = _make_update(text="any text")
        _, update = self._call(handle_text, update, session_state="nonexistent_state")
        msgs = update.effective_message.messages
        self.assertEqual(len(msgs), 1)
        self.assertIn("/start", msgs[0]["text"])
