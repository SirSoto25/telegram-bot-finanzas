"""
Telegram Finance Bot v2 — PythonAnywhere Edition
Flask WSGI + python-telegram-bot + Supabase
Features: cuentas, gastos/ingresos, traspasos, recurrentes, redondeo, deshacer, alertas, reportes, reset
"""

import asyncio, calendar, csv, html, io, json, logging, math, os, re, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.error import NetworkError
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters)

try:
    from supabase import create_client
    _SUPABASE_IMPORT_ERROR = None
except Exception as err:
    create_client = None
    _SUPABASE_IMPORT_ERROR = err

try:
    from postgrest.exceptions import APIError as PostgrestAPIError
except Exception:
    PostgrestAPIError = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set!")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
LEGACY_SQLITE_PATH = Path("/home/sirsoto25/bot/finance.db")
WEBHOOK_PATH = f"/{TOKEN}"

CATEGORY_MAP = {"1":"Comida","2":"Transporte","3":"Suscripciones","4":"Coche","5":"Entretenimiento","6":"Vivienda","7":"Utilidades","8":"Otros"}
ACCOUNT_TYPE_MAP = {"1":"NOMINA","2":"AHORROS","3":"INVERSION","4":"CRIPTO"}
FREQ_MAP = {"1":"SEMANAL","2":"MENSUAL","3":"TRIMESTRAL","4":"ANUAL"}
MONTHS_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CATEGORY_KBD_ITEMS = [
    ("🍕 Comida","1"),("🚌 Transporte","2"),("📺 Suscripciones","3"),("🚗 Coche","4"),
    ("🎮 Entretenimiento","5"),("🏠 Vivienda","6"),("💡 Utilidades","7"),("🏷️ Otros","8"),
]
TYPE_KBD_ITEMS = [("🏦 NOMINA","1"),("💰 AHORROS","2"),("📈 INVERSION","3"),("🪙 CRIPTO","4")]
FREQ_KBD_ITEMS = [("📅 SEMANAL","1"),("📅 MENSUAL","2"),("📅 TRIMESTRAL","3"),("📅 ANUAL","4")]

SESSION_TIMEOUT_MINUTES = 30
SYSTEM_BOT_TELEGRAM_ID = 0

SMART_CATEGORY_RULES = [
    ("Comida", ["supermercado", "mercadona", "lidl", "carrefour", "restaurante", "bar", "comida", "glovo", "uber eats", "takeaway"]),
    ("Transporte", ["metro", "bus", "tren", "taxi", "uber", "cabify", "gasolina", "parking", "peaje", "carga"]),
    ("Suscripciones", ["netflix", "spotify", "disney", "hbo", "prime", "youtube premium", "suscripcion", "subscription"]),
    ("Vivienda", ["alquiler", "hipoteca", "luz", "agua", "gas", "internet", "fibra"]),
    ("Coche", ["itv", "taller", "seguro coche", "mantenimiento", "neumatico", "garage"]),
    ("Entretenimiento", ["cine", "concierto", "juego", "gaming", "teatro", "ocio"]),
    ("Utilidades", ["impuestos", "telefono", "mantenimiento", "seguridad", "software"]),
]


def h(text):
    return html.escape(str(text))


def parse_amount(text):
    try:
        return float(text)
    except ValueError:
        return None


async def _safe_answer_callback(q):
    try:
        await q.answer()
    except NetworkError as err:
        logger.warning("answerCallbackQuery fallo: %s", err)


def _cb_suffix_int(data, prefix):
    suffix = data[len(prefix):]
    return int(suffix) if suffix.isdigit() else None

def _cb_suffix_text(data, prefix):
    if not data.startswith(prefix):
        return None
    suffix = data[len(prefix):]
    return suffix if suffix else None

def _extract_tags(text):
    if not text:
        return []
    return [t.lower() for t in re.findall(r"#([A-Za-z0-9_-]+)", text)]

def _smart_category_suggestion(text):
    if not text:
        return None
    lowered = text.lower()
    for category, keywords in SMART_CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None

def _month_shift(dt, months):
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)

def _month_window(dt):
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_day = calendar.monthrange(dt.year, dt.month)[1]
    end = dt.replace(day=end_day, hour=23, minute=59, second=59, microsecond=999999)
    return start, end


async def _ptb_error_handler(update, context):
    logger.exception("Unhandled PTB exception", exc_info=context.error)


class DBIntegrityError(Exception):
    pass


def _norm_sql(sql):
    return " ".join(sql.strip().split())


def _supabase_error_payload(err):
    args = getattr(err, "args", ())
    if args and isinstance(args[0], dict):
        return args[0]
    payload = {}
    code = getattr(err, "code", None)
    message = getattr(err, "message", None)
    details = getattr(err, "details", None)
    hint = getattr(err, "hint", None)
    if code is not None:
        payload["code"] = code
    if message:
        payload["message"] = message
    if details:
        payload["details"] = details
    if hint:
        payload["hint"] = hint
    return payload


def _is_rls_denied(err):
    if PostgrestAPIError is not None and not isinstance(err, PostgrestAPIError):
        return False
    payload = _supabase_error_payload(err)
    code = str(payload.get("code", "")).strip()
    message = str(payload.get("message", "")).lower()
    return code == "42501" and "row-level security" in message


class SupabaseCursor:
    def __init__(self, rows=None, lastrowid=None):
        self._rows = rows or []
        self.lastrowid = lastrowid

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class SupabaseDB:
    def __init__(self, url, key):
        self.client = create_client(url, key)

    async def _run(self, fn):
        try:
            return await asyncio.to_thread(fn)
        except Exception as err:
            if _is_rls_denied(err):
                raise RuntimeError(
                    "Supabase rechazo la escritura por RLS (42501). "
                    "Usa SUPABASE_KEY con la service_role key para este backend, "
                    "o crea politicas RLS que permitan INSERT/UPDATE/DELETE."
                ) from err
            raise

    async def _select_rows(self, table, columns="*", filters=None, order_by=None, desc=False, limit=None):
        def run():
            q = self.client.table(table).select(columns)
            for op, col, val in (filters or []):
                if op == "eq":
                    q = q.eq(col, val)
                elif op == "neq":
                    q = q.neq(col, val)
                elif op == "gte":
                    q = q.gte(col, val)
                elif op == "lte":
                    q = q.lte(col, val)
                elif op == "like":
                    q = q.like(col, val)
                elif op == "in":
                    q = q.in_(col, val)
                else:
                    raise ValueError(f"Filtro no soportado: {op}")
            if order_by:
                q = q.order(order_by, desc=desc)
            if limit is not None:
                q = q.limit(limit)
            res = q.execute()
            return [dict(r) for r in (res.data or [])]

        return await self._run(run)

    async def _insert_row(self, table, payload):
        def run():
            res = self.client.table(table).insert(payload).execute()
            data = res.data or []
            return dict(data[0]) if data else {}

        return await self._run(run)

    async def _insert_rows(self, table, payloads):
        if not payloads:
            return []

        def run():
            res = self.client.table(table).insert(payloads).execute()
            return [dict(r) for r in (res.data or [])]

        return await self._run(run)

    async def _upsert_row(self, table, payload, on_conflict):
        def run():
            res = self.client.table(table).upsert(payload, on_conflict=on_conflict).execute()
            data = res.data or []
            return dict(data[0]) if data else None

        row = await self._run(run)
        if row:
            return row
        conflict_cols = [c.strip() for c in on_conflict.split(",")]
        filters = [("eq", c, payload[c]) for c in conflict_cols]
        rows = await self._select_rows(table, filters=filters, limit=1)
        return rows[0] if rows else {}

    async def _update_rows(self, table, payload, filters):
        def run():
            q = self.client.table(table).update(payload)
            for op, col, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                else:
                    raise ValueError(f"Filtro de update no soportado: {op}")
            res = q.execute()
            return [dict(r) for r in (res.data or [])]

        return await self._run(run)

    async def _delete_rows(self, table, filters):
        def run():
            q = self.client.table(table).delete()
            for op, col, val in filters:
                if op == "eq":
                    q = q.eq(col, val)
                else:
                    raise ValueError(f"Filtro de delete no soportado: {op}")
            q.execute()

        await self._run(run)

    async def _apply_account_balance_delta(self, aid, delta):
        rows = await self._select_rows("accounts", columns="id,balance", filters=[("eq", "id", aid)], limit=1)
        if not rows:
            return
        cur = rows[0]["balance"] or 0
        await self._update_rows("accounts", {"balance": cur + delta}, [("eq", "id", aid)])

    async def _apply_goal_amount_delta(self, gid, uid, delta):
        rows = await self._select_rows(
            "savings_goals",
            columns="id,current_amount",
            filters=[("eq", "id", gid), ("eq", "user_id", uid)],
            limit=1
        )
        if not rows:
            return
        cur = rows[0]["current_amount"] or 0
        await self._update_rows("savings_goals", {"current_amount": cur + delta}, [("eq", "id", gid), ("eq", "user_id", uid)])

    async def execute(self, sql, params=()):
        q = _norm_sql(sql)
        p = tuple(params or ())

        # no-op controls from previous SQLite transaction flow
        if q in ("BEGIN", "ROLLBACK"):
            return SupabaseCursor()
        if q.startswith("ALTER TABLE "):
            return SupabaseCursor()

        # users
        if q == "SELECT id FROM users WHERE telegram_id=?":
            rows = await self._select_rows("users", columns="id", filters=[("eq", "telegram_id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT INTO users(telegram_id) VALUES(?)":
            row = await self._insert_row("users", {"telegram_id": p[0]})
            return SupabaseCursor(lastrowid=row.get("id"))

        # session state
        if q == "SELECT state,data,created_at FROM session_states WHERE telegram_id=?":
            rows = await self._select_rows(
                "session_states",
                columns="state,data,created_at",
                filters=[("eq", "telegram_id", p[0])],
                limit=1
            )
            return SupabaseCursor(rows)
        if q == "INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)":
            await self._upsert_row(
                "session_states",
                {"telegram_id": p[0], "state": p[1], "data": p[2], "created_at": p[3]},
                on_conflict="telegram_id"
            )
            return SupabaseCursor()
        if q == "DELETE FROM session_states WHERE telegram_id=?":
            await self._delete_rows("session_states", [("eq", "telegram_id", p[0])])
            return SupabaseCursor()

        # accounts
        if q == "SELECT * FROM accounts WHERE user_id=? ORDER BY created_at":
            rows = await self._select_rows("accounts", filters=[("eq", "user_id", p[0])], order_by="created_at")
            return SupabaseCursor(rows)
        if q == "SELECT * FROM accounts WHERE id=?":
            rows = await self._select_rows("accounts", filters=[("eq", "id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT * FROM accounts WHERE id=? AND user_id=?":
            rows = await self._select_rows("accounts", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT name FROM accounts WHERE id=?":
            rows = await self._select_rows("accounts", columns="name", filters=[("eq", "id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT name FROM accounts WHERE id=? AND user_id=?":
            rows = await self._select_rows("accounts", columns="name", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT balance FROM accounts WHERE id=?":
            rows = await self._select_rows("accounts", columns="balance", filters=[("eq", "id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT INTO accounts(user_id,name,type,balance) VALUES(?,?,?,?)":
            dup = await self._select_rows("accounts", columns="id", filters=[("eq", "user_id", p[0]), ("eq", "name", p[1])], limit=1)
            if dup:
                raise DBIntegrityError("Cuenta duplicada para el usuario")
            await self._insert_row("accounts", {"user_id": p[0], "name": p[1], "type": p[2], "balance": p[3]})
            return SupabaseCursor()
        if q == "DELETE FROM accounts WHERE id=? AND user_id=?":
            await self._delete_rows("accounts", [("eq", "id", p[0]), ("eq", "user_id", p[1])])
            return SupabaseCursor()
        if q == "DELETE FROM accounts WHERE user_id=?":
            await self._delete_rows("accounts", [("eq", "user_id", p[0])])
            return SupabaseCursor()
        if q == "UPDATE accounts SET balance=balance+? WHERE id=?":
            await self._apply_account_balance_delta(p[1], p[0])
            return SupabaseCursor()
        if q == "UPDATE accounts SET balance=balance-? WHERE id=?":
            await self._apply_account_balance_delta(p[1], -p[0])
            return SupabaseCursor()

        # transactions
        if q == "SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'":
            rows = await self._select_rows(
                "transactions",
                columns="type,amount",
                filters=[("eq", "user_id", p[0]), ("gte", "date", p[1]), ("lte", "date", p[2]), ("neq", "type", "TRANSFERENCIA")]
            )
            return SupabaseCursor(rows)
        if q == "SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?":
            rows = await self._select_rows(
                "transactions",
                columns="category,amount",
                filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("gte", "date", p[1]), ("lte", "date", p[2])]
            )
            return SupabaseCursor(rows)
        if q == "SELECT SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND category=? AND date>=? AND date<=?":
            rows = await self._select_rows(
                "transactions",
                columns="amount",
                filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("eq", "category", p[1]), ("gte", "date", p[2]), ("lte", "date", p[3])]
            )
            total = sum(r["amount"] for r in rows) if rows else None
            return SupabaseCursor([{"total": total}])
        if q == "SELECT category,SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=? GROUP BY category":
            rows = await self._select_rows(
                "transactions",
                columns="category,amount",
                filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("gte", "date", p[1]), ("lte", "date", p[2])]
            )
            grouped = {}
            for r in rows:
                grouped[r["category"]] = grouped.get(r["category"], 0) + r["amount"]
            out = [{"category": c, "total": t} for c, t in grouped.items()]
            return SupabaseCursor(out)
        if q == "SELECT * FROM transactions WHERE user_id=? AND type IN ('GASTO','INGRESO','TRANSFERENCIA') ORDER BY id DESC LIMIT 10":
            rows = await self._select_rows(
                "transactions",
                filters=[("eq", "user_id", p[0]), ("in", "type", ["GASTO", "INGRESO", "TRANSFERENCIA"])],
                order_by="id",
                desc=True,
                limit=10
            )
            return SupabaseCursor(rows)
        if q == "SELECT * FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA' ORDER BY date DESC":
            rows = await self._select_rows(
                "transactions",
                filters=[("eq", "user_id", p[0]), ("gte", "date", p[1]), ("lte", "date", p[2]), ("neq", "type", "TRANSFERENCIA")],
                order_by="date",
                desc=True
            )
            return SupabaseCursor(rows)
        if q == "SELECT * FROM transactions WHERE id=? AND user_id=?":
            rows = await self._select_rows("transactions", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? AND t.description LIKE ? ORDER BY t.date DESC LIMIT 10":
            txs = await self._select_rows(
                "transactions",
                filters=[("eq", "user_id", p[0]), ("like", "description", p[1])],
                order_by="date",
                desc=True,
                limit=10
            )
            if not txs:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({t["account_id"] for t in txs}))])
            amap = {a["id"]: a["name"] for a in arows}
            out = [dict(t, aname=amap.get(t["account_id"], "—")) for t in txs]
            return SupabaseCursor(out)
        if q == "SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? ORDER BY t.date DESC":
            txs = await self._select_rows("transactions", filters=[("eq", "user_id", p[0])], order_by="date", desc=True)
            if not txs:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({t["account_id"] for t in txs}))])
            amap = {a["id"]: a["name"] for a in arows}
            out = [dict(t, aname=amap.get(t["account_id"], "—")) for t in txs]
            return SupabaseCursor(out)
        if q == "SELECT COUNT(*) as cnt FROM transactions WHERE account_id=?":
            rows = await self._select_rows("transactions", columns="id", filters=[("eq", "account_id", p[0])])
            return SupabaseCursor([{"cnt": len(rows)}])
        if q == "DELETE FROM transactions WHERE account_id=?":
            await self._delete_rows("transactions", [("eq", "account_id", p[0])])
            return SupabaseCursor()
        if q == "DELETE FROM transactions WHERE id=?":
            await self._delete_rows("transactions", [("eq", "id", p[0])])
            return SupabaseCursor()
        if q == "DELETE FROM transactions WHERE user_id=?":
            await self._delete_rows("transactions", [("eq", "user_id", p[0])])
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category) VALUES(?,?,?,'INGRESO',?)":
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "INGRESO", "category": p[3]})
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,description) VALUES(?,?,?,'INGRESO',?,?)":
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "INGRESO", "category": p[3], "description": p[4]})
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,date) VALUES(?,?,?,'GASTO',?,?)":
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "GASTO", "category": p[3], "date": p[4]})
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,date,description) VALUES(?,?,?,'GASTO',?,?,?)":
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "GASTO", "category": p[3], "date": p[4], "description": p[5]})
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Redondeo',?,?)":
            await self._insert_row(
                "transactions",
                {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "TRANSFERENCIA", "category": "Redondeo", "description": p[3], "linked_account_id": p[4]}
            )
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Transferencia',?,?)":
            await self._insert_row(
                "transactions",
                {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "TRANSFERENCIA", "category": "Transferencia", "description": p[3], "linked_account_id": p[4]}
            )
            return SupabaseCursor()

        # recurring expenses
        if q == "SELECT * FROM recurring_expenses WHERE user_id=? ORDER BY next_date":
            rows = await self._select_rows("recurring_expenses", filters=[("eq", "user_id", p[0])], order_by="next_date")
            return SupabaseCursor(rows)
        if q == "SELECT * FROM recurring_expenses WHERE user_id=?":
            rows = await self._select_rows("recurring_expenses", filters=[("eq", "user_id", p[0])])
            return SupabaseCursor(rows)
        if q == "SELECT * FROM recurring_expenses WHERE user_id=? AND type='INGRESO' ORDER BY next_date":
            rows = await self._select_rows("recurring_expenses", filters=[("eq", "user_id", p[0]), ("eq", "type", "INGRESO")], order_by="next_date")
            return SupabaseCursor(rows)
        if q == "SELECT name FROM recurring_expenses WHERE id=? AND user_id=?":
            rows = await self._select_rows("recurring_expenses", columns="name", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT COUNT(*) as cnt FROM recurring_expenses WHERE account_id=?":
            rows = await self._select_rows("recurring_expenses", columns="id", filters=[("eq", "account_id", p[0])])
            return SupabaseCursor([{"cnt": len(rows)}])
        if q == "SELECT SUM(amount) as total FROM recurring_expenses WHERE user_id=? AND frequency='MENSUAL'":
            rows = await self._select_rows("recurring_expenses", columns="amount", filters=[("eq", "user_id", p[0]), ("eq", "frequency", "MENSUAL")])
            total = sum(r["amount"] for r in rows) if rows else None
            return SupabaseCursor([{"total": total}])
        if q == "DELETE FROM recurring_expenses WHERE account_id=?":
            await self._delete_rows("recurring_expenses", [("eq", "account_id", p[0])])
            return SupabaseCursor()
        if q == "DELETE FROM recurring_expenses WHERE id=? AND user_id=?":
            await self._delete_rows("recurring_expenses", [("eq", "id", p[0]), ("eq", "user_id", p[1])])
            return SupabaseCursor()
        if q == "DELETE FROM recurring_expenses WHERE user_id=?":
            await self._delete_rows("recurring_expenses", [("eq", "user_id", p[0])])
            return SupabaseCursor()
        if q == "INSERT INTO recurring_expenses(user_id,name,amount,frequency,next_date,category,account_id) VALUES(?,?,?,?,?,?,?)":
            await self._insert_row(
                "recurring_expenses",
                {"user_id": p[0], "name": p[1], "amount": p[2], "frequency": p[3], "next_date": p[4], "category": p[5], "account_id": p[6], "type": "GASTO"}
            )
            return SupabaseCursor()
        if q == "INSERT INTO recurring_expenses(user_id,name,amount,frequency,next_date,category,account_id,type) VALUES(?,?,?,?,?,?,?,?)":
            await self._insert_row(
                "recurring_expenses",
                {"user_id": p[0], "name": p[1], "amount": p[2], "frequency": p[3], "next_date": p[4], "category": p[5], "account_id": p[6], "type": p[7]}
            )
            return SupabaseCursor()
        if q == "SELECT r.*,u.telegram_id FROM recurring_expenses r JOIN users u ON r.user_id=u.id WHERE r.next_date<=?":
            recs = await self._select_rows("recurring_expenses", filters=[("lte", "next_date", p[0])])
            if not recs:
                return SupabaseCursor([])
            users = await self._select_rows("users", columns="id,telegram_id", filters=[("in", "id", list({r["user_id"] for r in recs}))])
            umap = {u["id"]: u["telegram_id"] for u in users}
            out = [dict(r, telegram_id=umap.get(r["user_id"])) for r in recs if r["user_id"] in umap]
            return SupabaseCursor(out)

        # alerts
        if q == "SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?":
            alerts = await self._select_rows("low_balance_alerts", filters=[("eq", "telegram_id", p[0])])
            if not alerts:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({a["account_id"] for a in alerts}))])
            amap = {a["id"]: a["name"] for a in arows}
            out = [dict(a, name=amap.get(a["account_id"], "—")) for a in alerts]
            return SupabaseCursor(out)
        if q == "SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.id=?":
            alerts = await self._select_rows("low_balance_alerts", filters=[("eq", "id", p[0])], limit=1)
            if not alerts:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("eq", "id", alerts[0]["account_id"])], limit=1)
            name = arows[0]["name"] if arows else "—"
            return SupabaseCursor([dict(alerts[0], name=name)])
        if q == "INSERT OR REPLACE INTO low_balance_alerts(telegram_id,account_id,threshold,enabled) VALUES(?,?,?,1)":
            await self._upsert_row(
                "low_balance_alerts",
                {"telegram_id": p[0], "account_id": p[1], "threshold": p[2], "enabled": True},
                on_conflict="telegram_id,account_id"
            )
            return SupabaseCursor()
        if q == "DELETE FROM low_balance_alerts WHERE id=?":
            await self._delete_rows("low_balance_alerts", [("eq", "id", p[0])])
            return SupabaseCursor()
        if q == "DELETE FROM low_balance_alerts WHERE account_id=?":
            await self._delete_rows("low_balance_alerts", [("eq", "account_id", p[0])])
            return SupabaseCursor()
        if q == "DELETE FROM low_balance_alerts WHERE telegram_id=?":
            await self._delete_rows("low_balance_alerts", [("eq", "telegram_id", p[0])])
            return SupabaseCursor()

        # roundup
        if q == "SELECT * FROM roundup_config WHERE user_id=?":
            rows = await self._select_rows("roundup_config", filters=[("eq", "user_id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT INTO roundup_config(user_id,enabled,account_id) VALUES(?,1,?)":
            await self._upsert_row("roundup_config", {"user_id": p[0], "enabled": True, "account_id": p[1]}, on_conflict="user_id")
            return SupabaseCursor()
        if q == "INSERT INTO roundup_config(user_id,enabled,account_id) VALUES(?,0,?)":
            await self._upsert_row("roundup_config", {"user_id": p[0], "enabled": False, "account_id": p[1]}, on_conflict="user_id")
            return SupabaseCursor()
        if q == "UPDATE roundup_config SET enabled=0 WHERE user_id=?":
            await self._update_rows("roundup_config", {"enabled": False}, [("eq", "user_id", p[0])])
            return SupabaseCursor()
        if q == "UPDATE roundup_config SET enabled=1 WHERE user_id=?":
            await self._update_rows("roundup_config", {"enabled": True}, [("eq", "user_id", p[0])])
            return SupabaseCursor()
        if q == "UPDATE roundup_config SET account_id=? WHERE user_id=?":
            await self._update_rows("roundup_config", {"account_id": p[0]}, [("eq", "user_id", p[1])])
            return SupabaseCursor()
        if q == "DELETE FROM roundup_config WHERE user_id=?":
            await self._delete_rows("roundup_config", [("eq", "user_id", p[0])])
            return SupabaseCursor()

        # budgets
        if q == "SELECT amount FROM budgets WHERE user_id=? AND category=? AND month=?":
            rows = await self._select_rows("budgets", columns="amount", filters=[("eq", "user_id", p[0]), ("eq", "category", p[1]), ("eq", "month", p[2])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT category,amount FROM budgets WHERE user_id=? AND month=?":
            rows = await self._select_rows("budgets", columns="category,amount", filters=[("eq", "user_id", p[0]), ("eq", "month", p[1])])
            return SupabaseCursor(rows)
        if q == "INSERT OR REPLACE INTO budgets(user_id,category,amount,month) VALUES(?,?,?,?)":
            await self._upsert_row("budgets", {"user_id": p[0], "category": p[1], "amount": p[2], "month": p[3]}, on_conflict="user_id,category,month")
            return SupabaseCursor()

        # savings goals
        if q == "SELECT * FROM savings_goals WHERE user_id=? ORDER BY created_at":
            rows = await self._select_rows("savings_goals", filters=[("eq", "user_id", p[0])], order_by="created_at")
            return SupabaseCursor(rows)
        if q == "SELECT * FROM savings_goals WHERE user_id=?":
            rows = await self._select_rows("savings_goals", filters=[("eq", "user_id", p[0])])
            return SupabaseCursor(rows)
        if q == "SELECT name FROM savings_goals WHERE id=? AND user_id=?":
            rows = await self._select_rows("savings_goals", columns="name", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT INTO savings_goals(user_id,name,target_amount,deadline) VALUES(?,?,?,?)":
            await self._insert_row("savings_goals", {"user_id": p[0], "name": p[1], "target_amount": p[2], "deadline": p[3]})
            return SupabaseCursor()
        if q == "UPDATE savings_goals SET current_amount=current_amount+? WHERE id=? AND user_id=?":
            await self._apply_goal_amount_delta(p[1], p[2], p[0])
            return SupabaseCursor()

        raise RuntimeError(f"SQL no soportado por backend Supabase: {q}")

    async def commit(self):
        return None

    async def executescript(self, _script):
        return None


async def _tx_wrap(db, ops):
    await db.execute("BEGIN")
    try:
        for sql, params in ops:
            await db.execute(sql, params)
        await db.commit()
    except Exception:
        await db.execute("ROLLBACK")
        raise


# ── DB ────────────────────────────────────────────────────────────────
async def migrate_legacy_sqlite(db):
    if not LEGACY_SQLITE_PATH.exists():
        return
    existing = await db._select_rows("users", columns="id", limit=1)
    if existing:
        return

    logger.info("Migrando datos desde SQLite legacy a Supabase...")
    conn = sqlite3.connect(str(LEGACY_SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    try:
        def legacy_rows(query):
            try:
                return [dict(r) for r in conn.execute(query).fetchall()]
            except sqlite3.OperationalError:
                return []

        user_map, account_map = {}, {}

        users = legacy_rows("SELECT * FROM users ORDER BY id")
        for u in users:
            payload = {"telegram_id": u["telegram_id"]}
            if u.get("created_at"):
                payload["created_at"] = u["created_at"]
            row = await db._upsert_row("users", payload, on_conflict="telegram_id")
            user_map[u["id"]] = row["id"]

        accounts = legacy_rows("SELECT * FROM accounts ORDER BY id")
        for a in accounts:
            payload = {
                "user_id": user_map.get(a["user_id"]),
                "name": a["name"],
                "type": a["type"],
                "balance": a["balance"],
                "created_at": a.get("created_at"),
            }
            if payload["user_id"] is None:
                continue
            row = await db._upsert_row("accounts", payload, on_conflict="user_id,name")
            account_map[a["id"]] = row["id"]

        tx_payloads = []
        for t in legacy_rows("SELECT * FROM transactions ORDER BY id"):
            uid = user_map.get(t["user_id"])
            aid = account_map.get(t["account_id"])
            lid = account_map.get(t["linked_account_id"]) if t.get("linked_account_id") is not None else None
            if uid is None or aid is None:
                continue
            tx_payloads.append({
                "user_id": uid,
                "account_id": aid,
                "amount": t["amount"],
                "type": t["type"],
                "category": t["category"],
                "description": t.get("description") or "",
                "linked_account_id": lid,
                "date": t.get("date"),
            })
        if tx_payloads:
            await db._insert_rows("transactions", tx_payloads)

        rec_payloads = []
        rec_rows = legacy_rows("SELECT * FROM recurring_expenses ORDER BY id")
        for r in rec_rows:
            uid = user_map.get(r["user_id"])
            aid = account_map.get(r["account_id"])
            if uid is None or aid is None:
                continue
            rec_payloads.append({
                "user_id": uid,
                "name": r["name"],
                "amount": r["amount"],
                "frequency": r["frequency"],
                "next_date": r["next_date"],
                "category": r["category"],
                "account_id": aid,
                "created_at": r.get("created_at"),
                "type": r.get("type") or "GASTO",
            })
        if rec_payloads:
            await db._insert_rows("recurring_expenses", rec_payloads)

        sessions = legacy_rows("SELECT * FROM session_states")
        for s in sessions:
            await db._upsert_row(
                "session_states",
                {
                    "telegram_id": s["telegram_id"],
                    "state": s.get("state"),
                    "data": s.get("data"),
                    "created_at": s.get("created_at"),
                },
                on_conflict="telegram_id"
            )

        alerts = legacy_rows("SELECT * FROM low_balance_alerts ORDER BY id")
        for a in alerts:
            aid = account_map.get(a["account_id"])
            if aid is None:
                continue
            await db._upsert_row(
                "low_balance_alerts",
                {
                    "telegram_id": a["telegram_id"],
                    "account_id": aid,
                    "threshold": a["threshold"],
                    "enabled": bool(a.get("enabled", 1)),
                },
                on_conflict="telegram_id,account_id"
            )

        roundup_rows = legacy_rows("SELECT * FROM roundup_config")
        for r in roundup_rows:
            uid = user_map.get(r["user_id"])
            aid = account_map.get(r["account_id"]) if r.get("account_id") is not None else None
            if uid is None:
                continue
            await db._upsert_row(
                "roundup_config",
                {"user_id": uid, "enabled": bool(r.get("enabled", 0)), "account_id": aid},
                on_conflict="user_id"
            )

        budgets = legacy_rows("SELECT * FROM budgets ORDER BY id")
        for b in budgets:
            uid = user_map.get(b["user_id"])
            if uid is None:
                continue
            await db._upsert_row(
                "budgets",
                {"user_id": uid, "category": b["category"], "amount": b["amount"], "month": b["month"]},
                on_conflict="user_id,category,month"
            )

        goals_payloads = []
        goals = legacy_rows("SELECT * FROM savings_goals ORDER BY id")
        for g in goals:
            uid = user_map.get(g["user_id"])
            if uid is None:
                continue
            goals_payloads.append({
                "user_id": uid,
                "name": g["name"],
                "target_amount": g["target_amount"],
                "current_amount": g.get("current_amount", 0),
                "deadline": g.get("deadline"),
                "created_at": g.get("created_at"),
            })
        if goals_payloads:
            await db._insert_rows("savings_goals", goals_payloads)
    finally:
        conn.close()

    logger.info("Migracion SQLite -> Supabase completada.")


async def init_db():
    if create_client is None:
        detail = (
            f"{type(_SUPABASE_IMPORT_ERROR).__name__}: {_SUPABASE_IMPORT_ERROR}"
            if _SUPABASE_IMPORT_ERROR is not None
            else "modulo no encontrado"
        )
        py_mm = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise RuntimeError(
            "No se pudo importar 'supabase'. "
            f"Detalle: {detail}. "
            f"Python actual: {sys.executable}. "
            f"Instala en ese mismo entorno con: python{py_mm} -m pip install --user --upgrade supabase"
        )
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Configura SUPABASE_URL y SUPABASE_KEY en variables de entorno.")

    db = SupabaseDB(SUPABASE_URL, SUPABASE_KEY)
    # Verifica conectividad/esquema base
    try:
        await db._select_rows("users", columns="id", limit=1)
    except Exception as err:
        raise RuntimeError(
            "No se pudo acceder a la tabla 'users' en Supabase. "
            "Crea el esquema en Supabase antes de iniciar el bot."
        ) from err
    await migrate_legacy_sqlite(db)
    return db

_app_db = None
async def get_db():
    global _app_db
    if _app_db is None: _app_db = await init_db()
    return _app_db

async def get_or_create_user(db, tid):
    c = await db.execute("SELECT id FROM users WHERE telegram_id=?",(tid,)); u = await c.fetchone()
    if not u:
        c = await db.execute("INSERT INTO users(telegram_id) VALUES(?)",(tid,)); await db.commit(); return c.lastrowid
    return u["id"]

async def get_session(db,tid):
    c=await db.execute("SELECT state,data,created_at FROM session_states WHERE telegram_id=?",(tid,)); return await c.fetchone()
async def save_session(db,tid,state,data=None):
    await db.execute("INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)",
                     (tid,state,json.dumps(data or {}),datetime.now().isoformat())); await db.commit()
async def clear_session(db,tid):
    await db.execute("DELETE FROM session_states WHERE telegram_id=?",(tid,)); await db.commit()

async def get_system_state(db):
    c = await db.execute("SELECT state,data,created_at FROM session_states WHERE telegram_id=?",(SYSTEM_BOT_TELEGRAM_ID,))
    return await c.fetchone()

async def save_system_state(db, state, data=None):
    await db.execute(
        "INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)",
        (SYSTEM_BOT_TELEGRAM_ID, state, json.dumps(data or {}), datetime.now().isoformat())
    )
    await db.commit()

async def _check_session_expiry(db,tid):
    s=await get_session(db,tid)
    if s and s["created_at"]:
        created=datetime.fromisoformat(s["created_at"])
        now = datetime.now(created.tzinfo) if created.tzinfo is not None else datetime.now()
        if now-created>timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            await clear_session(db,tid); return True
    return False

async def get_accounts(db,uid):
    c=await db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY created_at",(uid,)); return await c.fetchall()

async def get_monthly_tx(db,uid,months=6):
    data,now={},datetime.now()
    for i in range(months-1,-1,-1):
        d=now-timedelta(days=30*i); start=d.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        end=(d.replace(year=d.year+1,month=1,day=1)-timedelta(seconds=1) if d.month==12 else (d.replace(month=d.month+1,day=1)-timedelta(seconds=1)))
        key=f"{MONTHS_ES[start.month]} {start.year}"
        c=await db.execute("SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'",(uid,start.isoformat(),end.isoformat()))
        rows=await c.fetchall()
        data[key]={"income":sum(r["amount"] for r in rows if r["type"]=="INGRESO"),"expense":sum(r["amount"] for r in rows if r["type"]=="GASTO")}
    return data

CATEGORY_EMOJI = {"Comida":"🍕","Transporte":"🚌","Suscripciones":"📺","Coche":"🚗","Entretenimiento":"🎮","Vivienda":"🏠","Utilidades":"💡","Otros":"🏷️"}

def bar_chart(data,title,max_width=22):
    entries=sorted(data.items(),key=lambda e:e[1],reverse=True)
    if not entries: return ""
    mx=max(v for _,v in entries); chart=f"\n📊 {title}\n{'═'*44}\n"
    for label,val in entries:
        emoji=CATEGORY_EMOJI.get(label,"  ")
        bl=int((val/mx)*max_width) if mx>0 else 0
        bar="█"*bl+"░"*(max_width-bl)
        chart+=f"{emoji} {label[:12]:<12} │{bar}│ €{val:.2f}\n"
    return chart+"\n"+"═"*44

def trend_chart(data,title):
    if not data: return ""
    mx=max(d["amount"] for d in data); ch=7; chart=f"\n📈 {title}\n{'═'*46}\n"
    for i in range(ch,0,-1):
        th=(mx/ch)*i; line=""
        for p in data:
            if p["amount"]>=th: line+=" █ "
            elif p["amount"]>=th*0.6: line+=" ▄ "
            else: line+="   "
        chart+=f"│{line}│\n"
    chart+="└"+"───"*len(data)+"┘\n   "+"  ".join(d['month'][:3] for d in data)+"\n"
    return chart

def unicode_table(headers,rows):
    widths=[len(h) for h in headers]
    for row in rows:
        for i,cell in enumerate(row): widths[i]=max(widths[i],len(str(cell)))
    sep_top="┌"+"┬".join("─"*(w+2) for w in widths)+"┐\n"
    sep_mid="├"+"┼".join("─"*(w+2) for w in widths)+"┤\n"
    sep_bot="└"+"┴".join("─"*(w+2) for w in widths)+"┘\n"
    tbl=sep_top
    tbl+="│ "+" │ ".join(h.ljust(w) for h,w in zip(headers,widths))+" │\n"
    tbl+=sep_mid
    for row in rows:
        tbl+="│ "+" │ ".join(str(c).ljust(w) for c,w in zip(row,widths))+" │\n"
    tbl+=sep_bot
    return tbl

async def predict_expenses(db,uid):
    now,cd=datetime.now(),{}
    for i in range(2,-1,-1):
        d=now-timedelta(days=30*i); start=d.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        end=(d.replace(year=d.year+1,month=1,day=1)-timedelta(seconds=1) if d.month==12 else (d.replace(month=d.month+1,day=1)-timedelta(seconds=1)))
        c=await db.execute("SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?",(uid,start.isoformat(),end.isoformat()))
        for r in await c.fetchall(): cd.setdefault(r["category"],[]).append(r["amount"])
    preds=[]
    for cat,amts in cd.items():
        if len(amts)>=2:
            avg=sum(amts)/len(amts); trend="📈" if amts[-1]>avg else ("📉" if amts[-1]<avg else "➡️")
            preds.append({"category":cat,"predicted":avg,"trend":trend})
    return sorted(preds,key=lambda p:p["predicted"],reverse=True)

async def savings_recs(db,uid,income,expense,by_cat):
    recs=[]
    if income>0:
        rate=((income-expense)/income)*100
        if rate<10: recs.append(f"⚠️ Tu tasa de ahorro es baja ({rate:.1f}%). Intenta ahorrar al menos el 20% de tu ingreso.")
        elif rate<20: recs.append(f"👍 Tasa de ahorro: {rate:.1f}%. Puedes intentar mejorar a un 20-30%.")
        else: recs.append(f"🌟 ¡Excelente! Tu tasa de ahorro es del {rate:.1f}%. Manten el ritmo.")
    if expense>0:
        for cat,amt in by_cat.items():
            pct=(amt/expense)*100
            if pct>30: recs.append(f"💡 {cat} representa el {pct:.1f}% de tus gastos. Considera reducirlo.")
        if by_cat.get("Otros",0)>expense*0.15: recs.append(f"📌 Tienes muchos gastos en 'Otros' (€{by_cat['Otros']:.2f}). Intenta categorizarlos mejor.")
    c=await db.execute("SELECT SUM(amount) as total FROM recurring_expenses WHERE user_id=? AND frequency='MENSUAL'",(uid,)); row=await c.fetchone()
    rt=row["total"] or 0
    if rt>expense*0.5: recs.append(f"🔄 Tus gastos recurrentes (€{rt:.2f}) son muy altos. Revisa suscripciones innecesarias.")
    pot=expense*0.1
    if pot>0: recs.append(f"🎯 Si reduces gastos un 10%, podrias ahorrar €{pot:.2f} mas cada mes.")
    return recs or ["✅ Vas muy bien. Manten tus buenos habitos."]

async def _monthly_category_spend(db, uid, dt):
    start, end = _month_window(dt)
    c = await db.execute(
        "SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?",
        (uid, start.isoformat(), end.isoformat())
    )
    totals = {}
    for r in await c.fetchall():
        totals[r["category"]] = totals.get(r["category"], 0.0) + r["amount"]
    return totals

async def _build_financial_snapshot(db, uid):
    now = datetime.now()
    month_start, month_end = _month_window(now)
    c = await db.execute(
        "SELECT type,amount,category,description FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'",
        (uid, month_start.isoformat(), month_end.isoformat())
    )
    txs = await c.fetchall()
    income = expense = 0.0
    by_cat = {}
    tags = {}
    for tx in txs:
        if tx["type"] == "INGRESO":
            income += tx["amount"]
        elif tx["type"] == "GASTO":
            expense += tx["amount"]
            by_cat[tx["category"]] = by_cat.get(tx["category"], 0.0) + tx["amount"]
            for tag in _extract_tags(tx.get("description")):
                tags[tag] = tags.get(tag, 0) + 1
    accts = await get_accounts(db, uid)
    cash = sum(a["balance"] or 0 for a in accts)
    days_elapsed = max(now.day, 1)
    days_total = calendar.monthrange(now.year, now.month)[1]
    remaining_days = max(days_total - days_elapsed, 0)
    daily_net = (income - expense) / days_elapsed
    projected_balance = cash + (daily_net * remaining_days)
    return {
        "now": now,
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "cash": cash,
        "by_cat": by_cat,
        "tags": tags,
        "projected_balance": projected_balance,
        "remaining_days": remaining_days,
        "days_elapsed": days_elapsed,
        "days_total": days_total,
    }

async def _build_anomalies(db, uid):
    now = datetime.now()
    current = await _monthly_category_spend(db, uid, now)
    previous = []
    for offset in range(1, 4):
        previous.append(await _monthly_category_spend(db, uid, _month_shift(now, -offset)))
    anomalies = []
    for category, cur in current.items():
        prev_values = [m.get(category, 0.0) for m in previous]
        avg_prev = sum(prev_values) / len(prev_values) if prev_values else 0.0
        if avg_prev <= 0:
            continue
        if cur >= max(avg_prev * 1.5, avg_prev + 20):
            anomalies.append((category, cur, avg_prev))
    anomalies.sort(key=lambda item: item[1] - item[2], reverse=True)
    return anomalies

def _format_panel_text(snapshot, anomalies):
    income = snapshot["income"]
    expense = snapshot["expense"]
    balance = snapshot["balance"]
    cash = snapshot["cash"]
    projected = snapshot["projected_balance"]
    by_cat = snapshot["by_cat"]
    tags = snapshot["tags"]
    month = MONTHS_ES[snapshot["now"].month]
    year = snapshot["now"].year
    msg = (
        f"📊 <b>Panel financiero — {h(month)} {h(year)}</b>\n\n"
        f"📈 Ingresos: €{h(f'{income:.2f}')}\n"
        f"📉 Gastos: €{h(f'{expense:.2f}')}\n"
        f"💵 Balance del mes: €{h(f'{balance:.2f}')}\n"
        f"🏦 Efectivo total: €{h(f'{cash:.2f}')}\n"
        f"🔮 Proyección fin de mes: €{h(f'{projected:.2f}')}\n\n"
    )
    if by_cat:
        msg += "<b>Top categorías:</b>\n"
        for cat, amt in sorted(by_cat.items(), key=lambda e: e[1], reverse=True)[:5]:
            msg += f"• {h(cat)}: €{h(f'{amt:.2f}')}\n"
        msg += "\n"
    if tags:
        msg += "<b>Etiquetas activas:</b>\n"
        for tag, count in sorted(tags.items(), key=lambda e: e[1], reverse=True)[:5]:
            msg += f"• #{h(tag)} ({h(count)})\n"
        msg += "\n"
    if anomalies:
        msg += "<b>Anomalías detectadas:</b>\n"
        for cat, cur, avg_prev in anomalies[:5]:
            msg += f"• {h(cat)}: €{h(f'{cur:.2f}')} vs media €{h(f'{avg_prev:.2f}')}\n"
    else:
        msg += "✅ Sin anomalías claras este mes.\n"
    return msg

async def check_alerts(db,tid,uid):
    c=await db.execute("""SELECT la.*,a.name,a.balance FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=? AND la.enabled=1 AND a.balance<la.threshold""",(tid,))
    rows = await c.fetchall()
    alerts = []
    for r in rows:
        bal_str = "{:.2f}".format(r["balance"]); thr_str = "{:.2f}".format(r["threshold"])
        alerts.append(f"⚠️ <b>ALERTA</b>: {h(r['name'])} esta por debajo del limite (€{h(bal_str)} &lt; €{h(thr_str)})")
    return alerts

async def _check_budget_warning(db,uid,category,update):
    now=datetime.now(); month=f"{now.year}-{now.month:02d}"
    start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    end=(now.replace(year=now.year+1,month=1,day=1)-timedelta(seconds=1) if now.month==12 else (now.replace(month=now.month+1,day=1)-timedelta(seconds=1)))
    b=await (await db.execute("SELECT amount FROM budgets WHERE user_id=? AND category=? AND month=?",(uid,category,month))).fetchone()
    if not b: return
    c=await db.execute("SELECT SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND category=? AND date>=? AND date<=?",(uid,category,start.isoformat(),end.isoformat()))
    row=await c.fetchone(); spent=row["total"] or 0
    pct=spent/b["amount"]*100
    if pct>=90:
        bud_amt = "{:.2f}".format(b["amount"]); spent_amt = "{:.2f}".format(spent); pct_str = "{:.1f}".format(pct)
        await update.message.reply_text(f"⚠️ <b>Alerta de presupuesto</b>\n{category}: €{h(spent_amt)}/{h(bud_amt)} ({h(pct_str)}%)",parse_mode=ParseMode.HTML)

async def _expense_ask_account(db,tid,uid,sdata,q):
    accts=await get_accounts(db,uid)
    await save_session(db,tid,"waiting_expense_account",sdata)
    exp_amt2 = "{:.2f}".format(sdata.get('expenseAmount',0))
    await q.edit_message_text(f"Gasto: €{h(exp_amt2)} en {h(sdata.get('expenseCategory',''))}\n\nSelecciona la cuenta:",
                              reply_markup=_acct_kb(accts,"exp_acc",None))

async def get_roundup(db,uid):
    c=await db.execute("SELECT * FROM roundup_config WHERE user_id=?",(uid,)); return await c.fetchone()

def _kb(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=d)] for t,d in buttons])

def _acct_kb(accounts,prefix,extra=None):
    btns=[(f"{a['name']} (€{a['balance']:.2f})",f"{prefix}_{a['id']}") for a in accounts]
    if extra:
        btns.extend(extra)
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=d)] for t,d in btns])

def multi_kb(items, prefix, cols=2, extra=None):
    rows=[[InlineKeyboardButton(label,callback_data=f"{prefix}_{key}")] for label,key in items]
    if extra:
        rows.extend([[InlineKeyboardButton(label,callback_data=cd)] for label,cd in extra])
    return InlineKeyboardMarkup(rows)

def _confirm_kb(confirm_cb, current_text):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Si, confirmar", callback_data=confirm_cb)],
        [InlineKeyboardButton("❌ No, cancelar", callback_data="cancel_action")]
    ])

# ── COMMAND HANDLERS ──────────────────────────────────────────────────

async def cmd_start(update:Update,ctx):
    db=await get_db(); tid=update.effective_user.id
    await get_or_create_user(db,tid); await clear_session(db,tid)
    await update.message.reply_text("""
📊 <b>Bot de Finanzas Personales</b>

¡Hola! Soy tu asistente de finanzas. Puedo ayudarte a:
✅ Gestionar tus cuentas (nomina, ahorros, cripto, inversiones)
✅ Registrar gastos e ingresos
✅ Configurar gastos recurrentes
✅ Ver resumenes y estadisticas

<b>Comandos disponibles:</b>
/cuentas - Ver tus cuentas
/nuevacuenta - Crear nueva cuenta
/gasto - Registrar un gasto
/ingreso - Registrar un ingreso
/recurrente - Gestionar gastos recurrentes
/resumen - Resumen del mes
/stats - Estadisticas por categoria
/help - Ver ayuda completa
""", parse_mode=ParseMode.HTML)

async def cmd_help(update,ctx):
    await update.message.reply_text("""
<b>📚 Guia Completa de Comandos</b>

<b>💼 GESTION DE CUENTAS</b>
/cuentas - Ver todas tus cuentas y saldos
/nuevacuenta - Crear una nueva cuenta
/borrarcuenta - Eliminar una cuenta y sus movimientos

<b>💸 TRANSACCIONES</b>
/gasto - Registrar un gasto
/ingreso - Registrar un ingreso
/traspaso - Transferir dinero entre cuentas
/deshacer - Deshacer uno de los ultimos 10 movimientos

<b>🪙 REDONDEO AUTOMATICO</b>
/redondeo - Ver y configurar el redondeo de gastos
/redondeotoggle - Activar o desactivar el redondeo
/redondeocuenta - Cambiar la cuenta destino del redondeo

<b>📅 GASTOS RECURRENTES</b>
/recurrente - Ver y gestionar gastos recurrentes
/agregarrecurrente - Agregar nuevo gasto recurrente
/borrarrecurrente - Eliminar un gasto recurrente
/ingresorecurrente - Ver ingresos recurrentes
/agregaringresorecurrente - Agregar nuevo ingreso recurrente

<b>📊 REPORTES Y ANALISIS</b>
/resumen - Resumen mensual con graficos y recomendaciones
/stats - Estadisticas ultimos 6 meses
/tendencia - Analisis de tendencias (12 meses)
/panel - Panel financiero con forecast y anomalías
/forecast - Proyección de fin de mes
/anomalias - Anomalias de gasto detectadas
/tags - Etiquetas detectadas desde notas
/sugerircategoria - Sugiere una categoría a partir de texto

<b>🔔 ALERTAS</b>
/alertas - Gestionar alertas de saldo bajo
/agregaralerta - Crear alerta de saldo bajo
/borraralerta - Eliminar una alerta

<b>📥 EXPORTAR DATOS</b>
/exportar - Descargar todas tus transacciones en CSV

<b>🏠 NAVEGACION</b>
/menu - Panel principal
/help - Esta guia
/cancel - Cancelar operacion actual

<b>📋 TIPOS DE CUENTA</b>
• NOMINA - Cuenta corriente de nomina
• AHORROS - Cuenta de ahorros
• INVERSION - Fondos de inversion
• CRIPTO - Billetera de criptomonedas

<b>🏷️ CATEGORIAS DE GASTOS</b>
• Comida / Transporte / Suscripciones
• Coche / Entretenimiento / Vivienda
• Utilidades / Otros

<b>🗑 BORRADO</b>
/borrarcuenta - Eliminar una cuenta
/borrarrecurrente - Eliminar un gasto recurrente
/deshacer - Deshacer un movimiento reciente
/reset - ⚠️ Borrar TODOS los datos

<b>💡 FUNCIONALIDADES AVANZADAS</b>
✅ Graficos ASCII de gastos
✅ Predicciones basadas en historico
✅ Recomendaciones personalizadas de ahorro
✅ Analisis de tendencias
✅ Alertas automaticas de saldo bajo
✅ Redondeo automatico de gastos
✅ Exportacion de datos a CSV
""", parse_mode=ParseMode.HTML)

async def cmd_menu(update,ctx):
    db=await get_db(); await clear_session(db,update.effective_user.id)
    MENU_ITEMS = [
        ("💸 Registrar Gasto","menu_gasto"),("💰 Registrar Ingreso","menu_ingreso"),
        ("💱 Transferencia","menu_traspaso"),("↩️ Deshacer","menu_deshacer"),
        ("📊 Resumen Mensual","menu_resumen"),("📈 Estadisticas","menu_stats"),
        ("📅 Recurrentes","menu_recurrente"),("🔔 Alertas","menu_alertas"),
        ("💼 Ver Cuentas","menu_cuentas"),("🪙 Redondeo","menu_redondeo"),
        ("📥 Exportar CSV","menu_exportar"),("❓ Ayuda","menu_help"),
    ]
    await update.message.reply_text("📊 <b>Panel Principal</b>\n\n¿Que quieres hacer?",
                                     reply_markup=multi_kb(MENU_ITEMS,"menu",cols=2), parse_mode=ParseMode.HTML)

async def handle_menu_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id
    await clear_session(db,tid)
    d=q.data.replace("menu_","")
    cmd_map={
        "gasto":cmd_gasto,"ingreso":cmd_ingreso,"traspaso":cmd_traspaso,"deshacer":cmd_deshacer,
        "resumen":cmd_resumen,"stats":cmd_stats,"recurrente":cmd_recurrente,"alertas":cmd_alertas,
        "cuentas":cmd_cuentas,"redondeo":cmd_redondeo,"exportar":cmd_exportar,"help":cmd_help,
    }
    handler=cmd_map.get(d)
    if handler:
        await q.edit_message_text("⏳ Cargando...")
        await handler(update,ctx)
    else:
        await q.edit_message_text("Opcion no disponible.")

async def cmd_cancel(update,ctx):
    db=await get_db(); await clear_session(db,update.effective_user.id)
    await update.message.reply_text("✅ Operacion cancelada.", parse_mode=ParseMode.HTML)

async def cmd_cuentas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    accts=await get_accounts(db,uid)
    if not accts: return await update.message.reply_text("No tienes cuentas. Usa /nuevacuenta para crear una.", parse_mode=ParseMode.HTML)
    rows=[]; total=0.0
    for a in accts:
        rows.append([a['name'],a['type'],f"€{a['balance']:.2f}"]); total+=a["balance"]
    tbl=unicode_table(["Cuenta","Tipo","Saldo"],rows)
    await update.message.reply_text(f"💰 <b>Tus cuentas:</b>\n<pre>{h(tbl)}</pre>\n<b>Saldo total: €{h(f'{total:.2f}')}</b>", parse_mode=ParseMode.HTML)

async def cmd_nueva_cuenta(update,ctx):
    db=await get_db(); await save_session(db,update.effective_user.id,"waiting_account_name")
    await update.message.reply_text("¿Cual es el nombre de la cuenta?\n(Ejemplos: Nomina, Ahorros, Cripto)\n\n/cancel para cancelar")

async def cmd_borrar_cuenta(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    accts=await get_accounts(db,uid)
    if not accts: return await update.message.reply_text("No tienes cuentas para eliminar.")
    await update.message.reply_text("🗑 <b>Eliminar cuenta</b>\n\nSelecciona la cuenta a eliminar.\n⚠️ Se eliminaran tambien sus transacciones y recurrentes asociados.",
                                     reply_markup=_acct_kb(accts,"del_account"), parse_mode=ParseMode.HTML)

async def cmd_gasto(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    if not await get_accounts(db,uid): return await update.message.reply_text("Debes crear una cuenta primero con /nuevacuenta")
    await save_session(db,update.effective_user.id,"waiting_expense_amount")
    await update.message.reply_text("¿Cuanto gastaste?\n(Formato: cantidad)\n\nEjemplos: 45.50, 100\n\n/cancel para cancelar")

async def cmd_ingreso(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    if not await get_accounts(db,uid): return await update.message.reply_text("Debes crear una cuenta primero con /nuevacuenta")
    await save_session(db,update.effective_user.id,"waiting_income_amount")
    await update.message.reply_text("¿Cuanto ingreso?\n(Formato: cantidad)\n\nEjemplos: 100, 2500.50\n\n/cancel para cancelar")

async def cmd_traspaso(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if len(accts)<2: return await update.message.reply_text("Necesitas al menos 2 cuentas para transferir. Crea otra con /nuevacuenta", parse_mode=ParseMode.HTML)
    await save_session(db,tid,"waiting_transfer_from")
    await update.message.reply_text("💱 <b>Transferencia</b>\n\nSelecciona la cuenta de ORIGEN:", reply_markup=_acct_kb(accts,"xfer_from",None), parse_mode=ParseMode.HTML)

async def cmd_deshacer(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM transactions WHERE user_id=? AND type IN ('GASTO','INGRESO','TRANSFERENCIA') ORDER BY id DESC LIMIT 10",(uid,))
    txs=await c.fetchall()
    if not txs: return await update.message.reply_text("No hay movimientos recientes para deshacer.")
    btns=[]
    for tx in txs:
        typetag="💸" if tx["type"]=="GASTO" else ("💰" if tx["type"]=="INGRESO" else "💱")
        datepart=tx["date"][:10] if tx["date"] else "—"
        label=f"{typetag} {datepart} | {tx['category']} | €{tx['amount']:.2f}"
        btns.append((label,f"undo_{tx['id']}"))
    btns.append(("Cancelar","cancel_action"))
    await update.message.reply_text("↩️ <b>Deshacer movimiento</b>\n\nSelecciona el movimiento a deshacer (ultimos 10):", reply_markup=_kb(btns), parse_mode=ParseMode.HTML)

async def cmd_redondeo(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    rup=await get_roundup(db,uid)
    msg="🪙 <b>Redondeo Automatico</b>\n\n"
    if rup and rup["enabled"]:
        c=await db.execute("SELECT name FROM accounts WHERE id=?",(rup["account_id"],)); acc=await c.fetchone()
        accname=h(acc["name"]) if acc else "—"
        msg+=f"Estado: ✅ <b>ACTIVADO</b>\n"
        msg+=f"Cuenta destino: <b>{accname}</b>\n\n"
        msg+="Cada gasto se redondea al euro superior y la diferencia se transfiere a la cuenta destino.\n\n"
    else:
        msg+="Estado: ❌ <b>DESACTIVADO</b>\n\n"
        msg+="El redondeo redondea cada gasto al euro superior y ahorra la diferencia automaticamente.\n\n"
    msg+="/redondeotoggle - Activar o desactivar\n/redondeocuenta - Cambiar cuenta destino"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_redondeo_toggle(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    rup=await get_roundup(db,uid)
    if rup and rup["enabled"]:
        await db.execute("UPDATE roundup_config SET enabled=0 WHERE user_id=?",(uid,)); await db.commit()
        await update.message.reply_text("🪙 Redondeo <b>DESACTIVADO</b>", parse_mode=ParseMode.HTML)
    else:
        accts=await get_accounts(db,uid)
        if not accts: return await update.message.reply_text("Necesitas al menos una cuenta. Crea una con /nuevacuenta", parse_mode=ParseMode.HTML)
        if not rup: await db.execute("INSERT INTO roundup_config(user_id,enabled,account_id) VALUES(?,1,?)",(uid,accts[0]["id"]))
        else: await db.execute("UPDATE roundup_config SET enabled=1 WHERE user_id=?",(uid,))
        await db.commit()
        await update.message.reply_text("🪙 Redondeo <b>ACTIVADO</b>\n\nCada gasto se redondeara al euro superior y la diferencia se ahorrara automaticamente.", parse_mode=ParseMode.HTML)

async def cmd_redondeo_cuenta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts: return await update.message.reply_text("Necesitas al menos una cuenta.")
    await save_session(db,tid,"waiting_roundup_account")
    await update.message.reply_text("🪙 <b>Cuenta destino del redondeo</b>\n\nSelecciona a que cuenta ira el dinero redondeado:", reply_markup=_acct_kb(accts,"roundup_acc",None), parse_mode=ParseMode.HTML)

async def cmd_recurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=? ORDER BY next_date",(uid,))
    recs=await c.fetchall()
    if not recs: return await update.message.reply_text("No tienes gastos recurrentes configurados.\n\n/agregarrecurrente - Agregar nuevo gasto", parse_mode=ParseMode.HTML)
    headers=["Nombre","Monto","Frecuencia","Proximo","ID"]
    rows=[]; total=0.0
    for r in recs:
        nd=r["next_date"][:10] if r["next_date"] else "—"
        rows.append([r['name'],f"€{r['amount']:.2f}",r['frequency'],nd,str(r['id'])])
        if r["frequency"]=="MENSUAL": total+=r["amount"]
    tbl=unicode_table(headers,rows)
    msg=f"📅 <b>Gastos recurrentes:</b>\n<pre>{h(tbl)}</pre>\n<b>Total estimado mensual: €{h(f'{total:.2f}')}</b>\n\n"
    msg+="¿Que deseas hacer?\n/agregarrecurrente - Agregar nuevo gasto\n/borrarrecurrente - Eliminar gasto\n/cancel - Cancelar"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    await save_session(db,update.effective_user.id,"menu_recurrente")

async def cmd_agregar_recurrente(update,ctx):
    db=await get_db(); await save_session(db,update.effective_user.id,"waiting_recurring_name")
    await update.message.reply_text("¿Cual es el nombre del gasto recurrente?\n(Ejemplo: Netflix, Seguro del coche)\n\n/cancel para cancelar")

async def cmd_borrar_recurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=?",(uid,)); recs=await c.fetchall()
    if not recs: return await update.message.reply_text("No tienes gastos recurrentes para eliminar.")
    btns=[(f"{r['name']} — €{r['amount']:.2f} ({r['frequency']})",f"del_recurring_{r['id']}") for r in recs]
    btns.append(("Cancelar","cancel_action"))
    await update.message.reply_text("Selecciona el gasto recurrente a eliminar:", reply_markup=_kb(btns))

async def cmd_resumen(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    now=datetime.now(); start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    end=(now.replace(year=now.year+1,month=1,day=1)-timedelta(seconds=1) if now.month==12 else (now.replace(month=now.month+1,day=1)-timedelta(seconds=1)))
    c=await db.execute("SELECT * FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA' ORDER BY date DESC",(uid,start.isoformat(),end.isoformat()))
    txs=await c.fetchall()
    if not txs: return await update.message.reply_text(f"<b>Resumen de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\nNo hay transacciones este mes.", parse_mode=ParseMode.HTML)
    by_cat,ti,te={},0.0,0.0
    for tx in txs:
        if tx["type"]=="INGRESO": ti+=tx["amount"]
        elif tx["type"]=="GASTO": te+=tx["amount"]; by_cat[tx["category"]]=by_cat.get(tx["category"],0)+tx["amount"]
    bal,rate=ti-te,(ti-te)/ti*100 if ti>0 else 0.0
    msg=f"📊 <b>Resumen de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\n📈 Ingresos: €{h(f'{ti:.2f}')}\n📉 Gastos: €{h(f'{te:.2f}')}\n💵 Balance: €{h(f'{bal:.2f}')}\n📊 Tasa de ahorro: {h(f'{rate:.1f}')}%\n\n<b>Gastos por categoria:</b>\n"
    for cat,amt in sorted(by_cat.items(),key=lambda e:e[1],reverse=True):
        pct=(amt/te*100); msg+=f"  {h(cat)}: €{h(f'{amt:.2f}')} ({h(f'{pct:.1f}')}%)\n"
    kb_rows=[]
    if by_cat: kb_rows.append([InlineKeyboardButton("📊 Grafico Categorias",callback_data="resumen_cat_c")])
    kb_rows.append([InlineKeyboardButton("📈 Tendencias 4m",callback_data="resumen_trend_t")])
    kb_rows.append([InlineKeyboardButton("🔮 Predicciones",callback_data="resumen_pred_p")])
    kb_rows.append([InlineKeyboardButton("💡 Recomendaciones",callback_data="resumen_rec_r")])
    kb_rows.append([InlineKeyboardButton("🔔 Alertas",callback_data="resumen_alerts_a")])
    await save_session(db,tid,"resumen_data",{"by_cat":by_cat,"ti":ti,"te":te,"uid":uid,"now_month":MONTHS_ES[now.month],"now_year":str(now.year)})
    await update.message.reply_text(msg,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb_rows))

async def handle_resumen_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id
    s=await get_session(db,tid)
    if not s or s["state"]!="resumen_data": return await q.edit_message_text("Sesion expirada.",parse_mode=ParseMode.HTML)
    sdata=json.loads(s["data"])
    d=q.data

    if d.startswith("resumen_cat_"):
        bc=bar_chart(sdata["by_cat"],"Gastos por categoria")
        await q.edit_message_text(f"<pre>{h(bc)}</pre>",parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))

    elif d.startswith("resumen_trend_"):
        monthly=await get_monthly_tx(db,sdata["uid"],4)
        td=[{"month":m,"amount":v["expense"]} for m,v in monthly.items()]
        tc=trend_chart(td,"Tendencia de gastos ultimos 4 meses")
        await q.edit_message_text(f"<pre>{h(tc)}</pre>",parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))

    elif d.startswith("resumen_pred_"):
        preds=await predict_expenses(db,sdata["uid"])
        if preds:
            pm="📈 <b>Predicciones para proximos meses</b>\n\n"
            for p in preds[:5]:
                pred_val="{:.2f}".format(p["predicted"])
                pm+=f"{h(p['trend'])} {h(p['category'])}: ~€{h(pred_val)}\n"
        else: pm="No hay suficientes datos para predecir."
        await q.edit_message_text(pm,parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))

    elif d.startswith("resumen_rec_"):
        recs=await savings_recs(db,sdata["uid"],sdata["ti"],sdata["te"],sdata["by_cat"])
        rm="💡 <b>Recomendaciones de Ahorro</b>\n\n"+"\n\n".join(recs)
        await q.edit_message_text(rm,parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))

    elif d.startswith("resumen_alerts_"):
        alerts=await check_alerts(db,tid,sdata["uid"])
        if alerts:
            am="\n".join(alerts)
            await q.edit_message_text(am,parse_mode=ParseMode.HTML,
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))
        else:
            await q.edit_message_text("✅ No hay alertas activas.",parse_mode=ParseMode.HTML,
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Volver",callback_data="resumen_back_main")]]))

    elif d=="resumen_back_main":
        by_cat=sdata["by_cat"]; ti=sdata["ti"]; te=sdata["te"]
        bal=ti-te; rate=(ti-te)/ti*100 if ti>0 else 0.0
        msg=f"📊 <b>Resumen de {h(sdata['now_month'])} {h(sdata['now_year'])}</b>\n\n📈 Ingresos: €{h(f'{ti:.2f}')}\n📉 Gastos: €{h(f'{te:.2f}')}\n💵 Balance: €{h(f'{bal:.2f}')}\n📊 Tasa de ahorro: {h(f'{rate:.1f}')}%\n\n<b>Gastos por categoria:</b>\n"
        for cat,amt in sorted(by_cat.items(),key=lambda e:e[1],reverse=True):
            pct=(amt/te*100); msg+=f"  {h(cat)}: €{h(f'{amt:.2f}')} ({h(f'{pct:.1f}')}%)\n"
        kb_rows=[]
        if by_cat: kb_rows.append([InlineKeyboardButton("📊 Grafico Categorias",callback_data="resumen_cat_c")])
        kb_rows.append([InlineKeyboardButton("📈 Tendencias 4m",callback_data="resumen_trend_t")])
        kb_rows.append([InlineKeyboardButton("🔮 Predicciones",callback_data="resumen_pred_p")])
        kb_rows.append([InlineKeyboardButton("💡 Recomendaciones",callback_data="resumen_rec_r")])
        kb_rows.append([InlineKeyboardButton("🔔 Alertas",callback_data="resumen_alerts_a")])
        await q.edit_message_text(msg,parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup(kb_rows))

async def cmd_stats(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    monthly=await get_monthly_tx(db,uid,6)
    headers=["Mes","Ingresos","Gastos","Balance","% Ahorro"]
    rows=[]
    for m,d in monthly.items():
        bal=d["income"]-d["expense"]; rate=(bal/d["income"]*100) if d["income"]>0 else 0.0
        rows.append([m,f"€{d['income']:.2f}",f"€{d['expense']:.2f}",f"€{bal:.2f}",f"{rate:.1f}%"])
    tbl=unicode_table(headers,rows)
    await update.message.reply_text(f"📈 <b>Estadisticas ultimos 6 meses</b>\n<pre>{h(tbl)}</pre>", parse_mode=ParseMode.HTML)

async def cmd_presupuesto(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    now=datetime.now(); month=f"{now.year}-{now.month:02d}"
    c=await db.execute("SELECT category,amount FROM budgets WHERE user_id=? AND month=?",(uid,month))
    budgets=await c.fetchall()
    start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    end=(now.replace(year=now.year+1,month=1,day=1)-timedelta(seconds=1) if now.month==12 else (now.replace(month=now.month+1,day=1)-timedelta(seconds=1)))
    c2=await db.execute("SELECT category,SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=? GROUP BY category",(uid,start.isoformat(),end.isoformat()))
    spent={r["category"]:r["total"] for r in await c2.fetchall()}
    if not budgets: return await update.message.reply_text("No tienes presupuestos configurados.\n\nUsa /presupuestoset para crear uno.",parse_mode=ParseMode.HTML)
    msg=f"📊 <b>Presupuestos de {h(MONTHS_ES[now.month])} {h(str(now.year))}</b>\n\n"
    for b in budgets:
        s=spent.get(b["category"],0); pct=s/b["amount"]*100; bar_w=10
        bl=min(int(pct/100*bar_w),bar_w); bar="█"*bl+"░"*(bar_w-bl)
        icon="🔴" if pct>90 else ("🟡" if pct>70 else "🟢")
        bud_amt = "{:.2f}".format(b["amount"]); spent_amt = "{:.2f}".format(s); pct_str = "{:.1f}".format(pct)
        msg+=f"{icon} {h(b['category'])}: €{h(spent_amt)}/{h(bud_amt)} {bar} {h(pct_str)}%\n"
    msg+="\n/presupuestoset - Crear o modificar presupuesto"
    await update.message.reply_text(msg,parse_mode=ParseMode.HTML)

async def cmd_presupuestoset(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    await save_session(db,tid,"waiting_budget_category")
    await update.message.reply_text("Selecciona la categoria para el presupuesto:",reply_markup=multi_kb(CATEGORY_KBD_ITEMS,"budcat",cols=2,extra=None))

async def handle_budget_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    s=await get_session(db,tid)
    if not s: return await q.edit_message_text("Sesion expirada.",parse_mode=ParseMode.HTML)
    sdata=json.loads(s["data"]) if s["data"] else {}; d=q.data; state=s["state"]

    if state=="waiting_budget_category" and d.startswith("budcat_"):
        key = _cb_suffix_text(d, "budcat_")
        if key not in CATEGORY_MAP: return await q.edit_message_text("Opcion invalida.")
        sdata["budgetCategory"]=key
        await save_session(db,tid,"waiting_budget_amount",sdata)
        await q.edit_message_text(f"Categoria: {h(CATEGORY_MAP[key])}\n\n¿Cual es el limite mensual?\n(Formato: cantidad)\n\n/cancel para cancelar")

async def cmd_buscar(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    text=update.message.text.strip()
    parts=text.split(" ",1)
    keyword=parts[1] if len(parts)>1 else ""
    if not keyword: return await update.message.reply_text("Uso: /buscar &lt;palabra clave&gt;\n\nBusca en las descripciones de tus transacciones.",parse_mode=ParseMode.HTML)
    c=await db.execute("SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? AND t.description LIKE ? ORDER BY t.date DESC LIMIT 10",(uid,f"%{keyword}%"))
    txs=await c.fetchall()
    if not txs: return await update.message.reply_text(f"No se encontraron transacciones para: {h(keyword)}",parse_mode=ParseMode.HTML)
    msg=f"🔍 <b>Resultados para: {h(keyword)}</b>\n\n"
    for tx in txs:
        dt=tx["date"][:10] if tx["date"] else "—"; desc=tx["description"] or "—"
        tx_amt = "{:.2f}".format(tx["amount"])
        msg+=f"{'💸' if tx['type']=='GASTO' else ('💰' if tx['type']=='INGRESO' else '💱')} {dt} | {tx['category']} | €{h(tx_amt)} | {tx['aname']}\n  {desc}\n\n"
    await update.message.reply_text(msg,parse_mode=ParseMode.HTML)

async def cmd_metas(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM savings_goals WHERE user_id=? ORDER BY created_at",(uid,))
    goals=await c.fetchall()
    if not goals: return await update.message.reply_text("No tienes metas de ahorro.\n\n/nuevameta - Crear nueva meta",parse_mode=ParseMode.HTML)
    msg="🎯 <b>Metas de Ahorro</b>\n\n"
    for g in goals:
        g_curr = "{:.2f}".format(g["current_amount"]); g_targ = "{:.2f}".format(g["target_amount"])
        pct=g["current_amount"]/g["target_amount"]*100; bar_w=10
        bl=min(int(pct/100*bar_w),bar_w); bar="█"*bl+"░"*(bar_w-bl)
        deadline=f" - Vence: {g['deadline'][:10]}" if g["deadline"] else ""
        msg+=f"🎯 {h(g['name'])}: €{h(g_curr)}/{h(g_targ)} {bar} {h(f'{pct:.1f}')}%{deadline}\n\n"
    msg+="/nuevameta - Crear meta\n/aportarmeta - Aportar a meta"
    await update.message.reply_text(msg,parse_mode=ParseMode.HTML)

async def cmd_nuevameta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    await save_session(db,tid,"waiting_goal_name")
    await update.message.reply_text("¿Nombre de la meta?\n(Ejemplo: Viaje a Japon, Fondo de emergencia)\n\n/cancel para cancelar")

async def cmd_aportarmeta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    c=await db.execute("SELECT * FROM savings_goals WHERE user_id=?",(uid,)); goals=await c.fetchall()
    if not goals: return await update.message.reply_text("No tienes metas. Crea una con /nuevameta")
    btns=[(f"{g['name']} (€{g['current_amount']:.2f}/{g['target_amount']:.2f})",f"aportar_goal_{g['id']}") for g in goals]
    btns.append(("Cancelar","cancel_action"))
    await update.message.reply_text("Selecciona la meta a la que quieres aportar:",reply_markup=_kb(btns))

async def cmd_ingresorecurrente(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    c=await db.execute("SELECT * FROM recurring_expenses WHERE user_id=? AND type='INGRESO' ORDER BY next_date",(uid,))
    recs=await c.fetchall()
    if not recs: return await update.message.reply_text("No tienes ingresos recurrentes configurados.\n\n/agregaringresorecurrente - Agregar ingreso recurrente",parse_mode=ParseMode.HTML)
    headers=["Nombre","Monto","Frecuencia","Proximo","ID"]
    rows=[]; total=0.0
    for r in recs:
        nd=r["next_date"][:10] if r["next_date"] else "—"
        rows.append([r['name'],f"€{r['amount']:.2f}",r['frequency'],nd,str(r['id'])])
        if r["frequency"]=="MENSUAL": total+=r["amount"]
    tbl=unicode_table(headers,rows)
    msg=f"💰 <b>Ingresos recurrentes:</b>\n<pre>{h(tbl)}</pre>\n<b>Total estimado mensual: €{h(f'{total:.2f}')}</b>\n\n"
    msg+="/agregaringresorecurrente - Agregar ingreso recurrente\n/cancel - Cancelar"
    await update.message.reply_text(msg,parse_mode=ParseMode.HTML)

async def cmd_agregaringresorecurrente(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts = await get_accounts(db, uid)
    if not accts:
        return await update.message.reply_text("Crea una cuenta primero con /nuevacuenta")
    await save_session(db,tid,"waiting_recurring_income_name")
    await update.message.reply_text("¿Nombre del ingreso recurrente?\n(Ejemplo: Nomina, Renta, Dividendo)\n\n/cancel para cancelar")

async def cmd_tendencia(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    monthly=await get_monthly_tx(db,uid,12)
    ed=[{"month":m,"amount":v["expense"]} for m,v in monthly.items()]
    id_data=[{"month":m,"amount":v["income"]} for m,v in monthly.items()]
    await update.message.reply_text(f"<pre>{h(trend_chart(ed,'Tendencia de gastos (12 meses)'))}</pre>", parse_mode=ParseMode.HTML)
    await update.message.reply_text(f"<pre>{h(trend_chart(id_data,'Tendencia de ingresos (12 meses)'))}</pre>", parse_mode=ParseMode.HTML)
    items=list(monthly.items())
    if len(items)>=2:
        _,pv=items[-2]; _,lv=items[-1]
        ediff=lv["expense"]-pv["expense"]; idiff=lv["income"]-pv["income"]
        ep=abs(ediff/pv["expense"]*100) if pv["expense"]>0 else 0; ip=abs(idiff/pv["income"]*100) if pv["income"]>0 else 0
        await update.message.reply_text(f"📊 <b>Analisis de Tendencia</b>\n\nGastos: {'📈' if ediff>0 else '📉'} {h(f'{ep:.1f}')}%\nIngresos: {'📈' if idiff>0 else '📉'} {h(f'{ip:.1f}')}%", parse_mode=ParseMode.HTML)

async def cmd_panel(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    snapshot = await _build_financial_snapshot(db, uid)
    anomalies = await _build_anomalies(db, uid)
    await update.message.reply_text(_format_panel_text(snapshot, anomalies), parse_mode=ParseMode.HTML)

async def cmd_anomalias(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    anomalies = await _build_anomalies(db, uid)
    if not anomalies:
        return await update.message.reply_text("✅ No se detectaron anomalías de gasto este mes.")
    msg = "⚠️ <b>Anomalías de gasto</b>\n\n"
    for cat, cur, avg_prev in anomalies:
        msg += f"• {h(cat)}: €{h(f'{cur:.2f}')} vs media de 3 meses €{h(f'{avg_prev:.2f}')}\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_forecast(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    snapshot = await _build_financial_snapshot(db, uid)
    cash = snapshot["cash"]
    projected = snapshot["projected_balance"]
    msg = (
        f"🔮 <b>Forecast de fin de mes</b>\n\n"
        f"Saldo actual total: €{h(f'{cash:.2f}')}\n"
        f"Proyección al cierre: €{h(f'{projected:.2f}')}\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_tags(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    snapshot = await _build_financial_snapshot(db, uid)
    if not snapshot["tags"]:
        return await update.message.reply_text("No hay etiquetas (#tag) en tus notas todavía.")
    msg = "🏷️ <b>Etiquetas detectadas</b>\n\n"
    for tag, count in sorted(snapshot["tags"].items(), key=lambda e: e[1], reverse=True):
        msg += f"• #{h(tag)}: {h(count)}\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_sugerircategoria(update,ctx):
    text = update.message.text.replace("/sugerircategoria", "", 1).strip()
    if not text:
        return await update.message.reply_text("Uso: /sugerircategoria <texto>\n\nEjemplo: /sugerircategoria supermercado mercadona")
    category = _smart_category_suggestion(text)
    if not category:
        return await update.message.reply_text("No pude inferir una categoría clara. Prueba con más contexto.")
    await update.message.reply_text(f"💡 Sugerencia: <b>{h(category)}</b>", parse_mode=ParseMode.HTML)

async def cmd_exportar(update,ctx):
    db=await get_db(); uid=await get_or_create_user(db,update.effective_user.id)
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
    c=await db.execute("SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? ORDER BY t.date DESC",(uid,)); txs=await c.fetchall()
    if not txs: return await update.message.reply_text("No hay transacciones para exportar.")
    await update.message.reply_text("📥 Generando archivo CSV...", parse_mode=ParseMode.HTML)
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(["Fecha","Tipo","Categoria","Monto","Cuenta","Descripcion"])
    for tx in txs: w.writerow([tx["date"][:10],tx["type"],tx["category"],f"{tx['amount']:.2f}",tx["aname"],tx["description"] or ""])
    out.seek(0); bio=io.BytesIO(out.getvalue().encode("utf-8"))
    bio.name=f"finanzas_{datetime.now().strftime('%d-%m-%Y')}.csv"
    await update.message.reply_document(bio)

async def cmd_alertas(update,ctx):
    db=await get_db(); tid=update.effective_user.id; await save_session(db,tid,"menu_alertas")
    c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
    alerts=await c.fetchall()
    msg="🔔 <b>Gestion de Alertas de Saldo Bajo</b>\n\n"
    if alerts:
        msg+="<b>Alertas configuradas:</b>\n"
        for a in alerts:
            th_val = "{:.2f}".format(a["threshold"])
            msg+=f"{'✅' if a['enabled'] else '❌'} {h(a['name'])}: €{h(th_val)}\n"
    else: msg+="No tienes alertas configuradas.\n"
    msg+="\nOpciones:\n/agregaralerta - Agregar nueva alerta\n/borraralerta - Eliminar alerta\n/cancel - Cancelar"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_agregar_alerta(update,ctx):
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    accts=await get_accounts(db,uid)
    if not accts: return await update.message.reply_text("No tienes cuentas. Crea una primero con /nuevacuenta", parse_mode=ParseMode.HTML)
    await save_session(db,tid,"waiting_alert_account",{"accounts":[{"id":a["id"],"name":a["name"]} for a in accts]})
    await update.message.reply_text("Selecciona la cuenta:", reply_markup=_acct_kb(accts,"alert_acc",None))

async def cmd_borrar_alerta(update,ctx):
    db=await get_db(); tid=update.effective_user.id
    c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
    alerts=await c.fetchall()
    if not alerts: return await update.message.reply_text("No tienes alertas para eliminar.")
    btns=[(f"{a['name']} — €{a['threshold']:.2f}",f"del_alert_{a['id']}") for a in alerts]
    btns.append(("Cancelar","cancel_action"))
    await update.message.reply_text("Selecciona la alerta a eliminar:", reply_markup=_kb(btns))

async def cmd_reset(update,ctx):
    await update.message.reply_text(
        "⚠️ <b>ATENCION</b>\n\nEsto borrara TODOS tus datos:\n• Cuentas\n• Transacciones\n• Gastos recurrentes\n• Alertas\n• Redondeo\n\nEsta accion NO se puede deshacer.\n\n¿Confirmas?",
        reply_markup=_kb([("✅ Si, borrar TODO","reset_confirm"),("❌ Cancelar","cancel_action")]), parse_mode=ParseMode.HTML)

# ── CALLBACK HANDLERS ────────────────────────────────────────────────

async def handle_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    if await _check_session_expiry(db,tid):
        return await q.edit_message_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    d=q.data
    if d=="cancel_action": await clear_session(db,tid); return await q.edit_message_text("✅ Operacion cancelada.")

    if d.startswith("aportar_goal_"):
        gid = _cb_suffix_int(d, "aportar_goal_")
        if gid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        goal=await (await db.execute("SELECT name FROM savings_goals WHERE id=? AND user_id=?",(gid,uid))).fetchone()
        if not goal: return await q.edit_message_text("Meta no encontrada.")
        await save_session(db,tid,"waiting_aportar_amount",{"goalId":gid})
        return await q.edit_message_text(f"Meta: {h(goal['name'])}\n\n¿Cuanto quieres aportar?\n(Formato: cantidad)\n\n/cancel para cancelar")

    if d.startswith("del_account_confirm_"):
        aid=_cb_suffix_int(d,"del_account_confirm_")
        if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        acc=await (await db.execute("SELECT name FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
        if not acc: return await q.edit_message_text("Cuenta no encontrada o ya eliminada.")
        c1=await (await db.execute("SELECT COUNT(*) as cnt FROM transactions WHERE account_id=?",(aid,))).fetchone()
        c2=await (await db.execute("SELECT COUNT(*) as cnt FROM recurring_expenses WHERE account_id=?",(aid,))).fetchone()
        cnt,rcnt=c1["cnt"],c2["cnt"]
        await _tx_wrap(db, [
            ("DELETE FROM transactions WHERE account_id=?",(aid,)),
            ("DELETE FROM recurring_expenses WHERE account_id=?",(aid,)),
            ("DELETE FROM low_balance_alerts WHERE account_id=?",(aid,)),
            ("DELETE FROM accounts WHERE id=? AND user_id=?",(aid,uid)),
        ])
        await q.edit_message_text(f"✅ Cuenta <b>{h(acc['name'])}</b> eliminada ({cnt} transacciones, {rcnt} recurrentes).", parse_mode=ParseMode.HTML)

    elif d.startswith("del_account_"):
        aid=_cb_suffix_int(d,"del_account_")
        if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        acc=await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
        if not acc: return await q.edit_message_text("Cuenta no encontrada.")
        c1=await (await db.execute("SELECT COUNT(*) as cnt FROM transactions WHERE account_id=?",(aid,))).fetchone()
        c2=await (await db.execute("SELECT COUNT(*) as cnt FROM recurring_expenses WHERE account_id=?",(aid,))).fetchone()
        cnt,rcnt=c1["cnt"],c2["cnt"]
        await q.edit_message_text(
            f"🗑 <b>¿Confirmas eliminar {h(acc['name'])}?</b>\n\nSe borraran:\n• {cnt} transacciones\n• {rcnt} gastos recurrentes\n• Alertas asociadas\n\nEsta accion NO se puede deshacer.",
            reply_markup=_confirm_kb(f"del_account_confirm_{aid}", d), parse_mode=ParseMode.HTML)

    elif d.startswith("xfer_from_"):
        fid = _cb_suffix_int(d, "xfer_from_")
        if fid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        accts=await get_accounts(db,uid)
        to=[a for a in accts if a["id"]!=fid]
        if not to: return await q.edit_message_text("No hay otra cuenta de destino disponible.")
        await save_session(db,tid,"waiting_transfer_to",{"from_id":fid})
        src=next(a for a in accts if a["id"]==fid)
        bal_val = "{:.2f}".format(src["balance"])
        await q.edit_message_text(f"Origen: <b>{h(src['name'])}</b> (€{h(bal_val)})\n\nSelecciona la cuenta de DESTINO:", reply_markup=_acct_kb(to,"xfer_to",None), parse_mode=ParseMode.HTML)

    elif d.startswith("xfer_to_"):
        toid = _cb_suffix_int(d, "xfer_to_")
        if toid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        s=await get_session(db,tid)
        sdata=json.loads(s["data"]) if s else {}; fid=sdata.get("from_id")
        await save_session(db,tid,"waiting_transfer_amount",{"from_id":fid,"to_id":toid})
        sa=await (await db.execute("SELECT name FROM accounts WHERE id=?",(fid,))).fetchone()
        da=await (await db.execute("SELECT name FROM accounts WHERE id=?",(toid,))).fetchone()
        await q.edit_message_text(f"Transferencia de <b>{h(sa['name'])}</b> a <b>{h(da['name'])}</b>\n\n¿Cuanto quieres transferir?\n(Formato: 100.50)\n\n/cancel para cancelar", parse_mode=ParseMode.HTML)

    elif d.startswith("del_recurring_confirm_"):
        rid=_cb_suffix_int(d,"del_recurring_confirm_")
        if rid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        r=await (await db.execute("SELECT name FROM recurring_expenses WHERE id=? AND user_id=?",(rid,uid))).fetchone()
        if not r: return await q.edit_message_text("Gasto recurrente no encontrado o ya eliminado.")
        await db.execute("DELETE FROM recurring_expenses WHERE id=? AND user_id=?",(rid,uid)); await db.commit()
        await q.edit_message_text(f"✅ Gasto recurrente <b>{h(r['name'])}</b> eliminado.", parse_mode=ParseMode.HTML)

    elif d.startswith("del_recurring_"):
        rid=_cb_suffix_int(d,"del_recurring_")
        if rid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        r=await (await db.execute("SELECT name FROM recurring_expenses WHERE id=? AND user_id=?",(rid,uid))).fetchone()
        if not r: return await q.edit_message_text("Gasto recurrente no encontrado.")
        await q.edit_message_text(
            f"🗑 <b>¿Confirmas eliminar {h(r['name'])}?</b>\n\nEsta accion NO se puede deshacer.",
            reply_markup=_confirm_kb(f"del_recurring_confirm_{rid}", d), parse_mode=ParseMode.HTML)

    elif d.startswith("alert_acc_"):
        aid = _cb_suffix_int(d, "alert_acc_")
        if aid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        await save_session(db,tid,"waiting_alert_threshold",{"account_id":aid})
        await q.edit_message_text("¿A partir de que cantidad quieres que se active la alerta?\n(Formato: cantidad en €)\n\nEjemplo: 500 (alerta cuando el saldo sea menor a €500)")

    elif d.startswith("del_alert_confirm_"):
        aid=_cb_suffix_int(d,"del_alert_confirm_")
        if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        await db.execute("DELETE FROM low_balance_alerts WHERE id=?",(aid,)); await db.commit()
        await q.edit_message_text("✅ Alerta eliminada.")

    elif d.startswith("del_alert_"):
        aid=_cb_suffix_int(d,"del_alert_")
        if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        a=await (await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.id=?",(aid,))).fetchone()
        if not a: return await q.edit_message_text("Alerta no encontrada.")
        await q.edit_message_text(
            f"🗑 <b>¿Confirmas eliminar la alerta de {h(a['name'])}?</b>",
            reply_markup=_confirm_kb(f"del_alert_confirm_{aid}", d), parse_mode=ParseMode.HTML)

    elif d.startswith("roundup_acc_"):
        accid = _cb_suffix_int(d, "roundup_acc_")
        if accid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        rup=await get_roundup(db,uid)
        if rup: await db.execute("UPDATE roundup_config SET account_id=? WHERE user_id=?",(accid,uid))
        else: await db.execute("INSERT INTO roundup_config(user_id,enabled,account_id) VALUES(?,0,?)",(uid,accid))
        await db.commit(); await clear_session(db,tid)
        sa=await (await db.execute("SELECT name FROM accounts WHERE id=?",(accid,))).fetchone()
        await q.edit_message_text(f"🪙 Cuenta destino del redondeo: <b>{h(sa['name'])}</b>", parse_mode=ParseMode.HTML)

    elif d=="reset_confirm":
        await _tx_wrap(db, [
            ("DELETE FROM transactions WHERE user_id=?",(uid,)),
            ("DELETE FROM recurring_expenses WHERE user_id=?",(uid,)),
            ("DELETE FROM low_balance_alerts WHERE telegram_id=?",(tid,)),
            ("DELETE FROM accounts WHERE user_id=?",(uid,)),
            ("DELETE FROM roundup_config WHERE user_id=?",(uid,)),
        ])
        await q.edit_message_text("🗑 <b>Todos tus datos han sido eliminados.</b>\n\nUsa /start para comenzar de nuevo.", parse_mode=ParseMode.HTML)

    # ── Undo transaction ──
    elif d.startswith("undo_"):
        txid = _cb_suffix_int(d, "undo_")
        if txid is None:
            return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
        tx=await (await db.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",(txid,uid))).fetchone()
        if not tx: return await q.edit_message_text("Movimiento no encontrado o ya deshecho.")
        ttype=tx["type"]; amt=tx["amount"]
        if ttype=="GASTO":
            await _tx_wrap(db, [
                ("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,tx["account_id"])),
                ("DELETE FROM transactions WHERE id=?",(txid,)),
            ])
        elif ttype=="INGRESO":
            acc=await (await db.execute("SELECT balance FROM accounts WHERE id=?",(tx["account_id"],))).fetchone()
            if acc["balance"]<amt:
                await q.edit_message_text("❌ No se puede deshacer: la cuenta ya no tiene saldo suficiente.")
                return
            await _tx_wrap(db, [
                ("UPDATE accounts SET balance=balance-? WHERE id=?",(amt,tx["account_id"])),
                ("DELETE FROM transactions WHERE id=?",(txid,)),
            ])
        elif ttype=="TRANSFERENCIA":
            lid=tx["linked_account_id"]
            ops = []
            if lid:
                acc=await (await db.execute("SELECT balance FROM accounts WHERE id=?",(lid,))).fetchone()
                if acc["balance"]<amt:
                    await q.edit_message_text("❌ No se puede deshacer: la cuenta destino no tiene saldo suficiente para revertir.")
                    return
                ops.append(("UPDATE accounts SET balance=balance-? WHERE id=?",(amt,lid)))
            ops.append(("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,tx["account_id"])))
            ops.append(("DELETE FROM transactions WHERE id=?",(txid,)))
            await _tx_wrap(db, ops)
        await q.edit_message_text(f"✅ Movimiento deshecho: €{h(f'{amt:.2f}')} ({ttype})", parse_mode=ParseMode.HTML)

# ── FLOW CALLBACK HANDLERS (dispatch) ───────────────────────────────

async def _hfc_acct_type(db,tid,uid,sdata,d,q,update,ctx):
    key = _cb_suffix_text(d, "type_")
    if key not in ACCOUNT_TYPE_MAP: return await q.edit_message_text("Opcion invalida.")
    sdata["accountType"]=ACCOUNT_TYPE_MAP[key]
    await save_session(db,tid,"waiting_account_balance",sdata)
    await q.edit_message_text("¿Cual es el saldo inicial?\n(Formato: cantidad)\n\nEjemplo: 2500\n\n/cancel para cancelar")

async def _hfc_expense_cat(db,tid,uid,sdata,d,q,update,ctx):
    key = _cb_suffix_text(d, "cat_")
    if key not in CATEGORY_MAP: return await q.edit_message_text("Opcion invalida.")
    sdata["expenseCategory"]=CATEGORY_MAP[key]
    await save_session(db,tid,"waiting_expense_date",sdata)
    exp_amt2="{:.2f}".format(sdata.get('expenseAmount',0))
    await q.edit_message_text(f"Gasto: €{h(exp_amt2)} en {h(CATEGORY_MAP[key])}\n\nSelecciona la fecha:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Hoy",callback_data="expdate_hoy")],
            [InlineKeyboardButton("📅 Ayer",callback_data="expdate_ayer")],
            [InlineKeyboardButton("✏️ Otra fecha",callback_data="expdate_custom")],
            [InlineKeyboardButton("Cancelar",callback_data="cancel_action")],
        ]))

async def _hfc_expense_date(db,tid,uid,sdata,d,q,update,ctx):
    sub = _cb_suffix_text(d, "expdate_")
    if sub=="hoy": sdata["expenseDate"]=datetime.now().isoformat()
    elif sub=="ayer": sdata["expenseDate"]=(datetime.now()-timedelta(days=1)).isoformat()
    elif sub=="custom":
        await save_session(db,tid,"waiting_expense_date_custom",sdata)
        return await q.edit_message_text("Introduce la fecha (DD/MM/AAAA):\n\n/cancel para cancelar")
    await _expense_ask_account(db,tid,uid,sdata,q)

async def _hfc_expense_acc(db,tid,uid,sdata,d,q,update,ctx):
    aid = _cb_suffix_int(d, "exp_acc_")
    if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
    amt=sdata.get("expenseAmount",0); cat=sdata.get("expenseCategory","")
    if amt <= 0:
        return await q.edit_message_text("❌ Cantidad invalida. Inicia de nuevo con /gasto.")
    acc=await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
    if not acc: return await q.edit_message_text("Cuenta no encontrada.")
    if acc["balance"]<amt: return await q.edit_message_text("❌ Saldo insuficiente en esta cuenta.")
    sdata["expenseAccountId"] = aid
    await save_session(db,tid,"waiting_expense_note",sdata)
    await q.edit_message_text(
        f"Gasto: €{h(f'{amt:.2f}')}\n📌 {h(cat)}\n💼 {h(acc['name'])}\n\n¿Quieres añadir una nota o etiqueta?\nPuedes escribir algo como \"supermercado #hogar\" o usar /saltar.",
        parse_mode=ParseMode.HTML
    )

async def _hfc_income_acc(db,tid,uid,sdata,d,q,update,ctx):
    aid = _cb_suffix_int(d, "inc_acc_")
    if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
    amt=sdata.get("incomeAmount",0); conc=sdata.get("incomeConcept","")
    if amt <= 0:
        return await q.edit_message_text("❌ Cantidad invalida. Inicia de nuevo con /ingreso.")
    acc=await (await db.execute("SELECT name FROM accounts WHERE id=?",(aid,))).fetchone()
    sdata["incomeAccountId"] = aid
    await save_session(db,tid,"waiting_income_note",sdata)
    await q.edit_message_text(
        f"Ingreso: €{h(f'{amt:.2f}')}\n📝 {h(conc)}\n💼 {h(acc['name'])}\n\n¿Quieres añadir una nota o etiqueta?\nPuedes escribir algo como \"nómina #salario\" o usar /saltar.",
        parse_mode=ParseMode.HTML
    )

async def _finalize_expense_with_note(db, tid, uid, sdata, note, update):
    aid = sdata["expenseAccountId"]
    amt = sdata["expenseAmount"]
    cat = sdata["expenseCategory"]
    exp_date = sdata.get("expenseDate", datetime.now().isoformat())
    acc = await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
    if not acc:
        await update.message.reply_text("Cuenta no encontrada.")
        return
    if acc["balance"] < amt:
        await update.message.reply_text("❌ Saldo insuficiente en esta cuenta.")
        return
    ops = [(
        "INSERT INTO transactions(user_id,account_id,amount,type,category,date,description) VALUES(?,?,?,'GASTO',?,?,?)",
        (uid, aid, amt, cat, exp_date, note or "")
    ), ("UPDATE accounts SET balance=balance-? WHERE id=?", (amt, aid))]
    rup = await get_roundup(db, uid)
    if rup and rup["enabled"] and rup["account_id"] and rup["account_id"] != aid:
        rounded = math.ceil(amt)
        diff = rounded - amt
        if diff > 0:
            ops.append(("UPDATE accounts SET balance=balance-? WHERE id=?", (diff, aid)))
            ops.append(("UPDATE accounts SET balance=balance+? WHERE id=?", (diff, rup["account_id"])))
            ops.append((
                "INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Redondeo',?,?)",
                (uid, aid, diff, f"Redondeo de €{amt:.2f}", rup["account_id"])
            ))
            await _tx_wrap(db, ops)
            da = await (await db.execute("SELECT name FROM accounts WHERE id=?",(rup["account_id"],))).fetchone()
            await clear_session(db, tid)
            label = f"\n🏷️ {h(note)}" if note else ""
            await update.message.reply_text(f"✅ Gasto registrado\n💸 €{h(f'{amt:.2f}')}\n📌 {h(cat)}\n💼 {h(acc['name'])}{label}\n\n🪙 Redondeo: +€{h(f'{diff:.2f}')} → {h(da['name'])}", parse_mode=ParseMode.HTML)
            await _check_budget_warning(db, uid, cat, update)
            return
    await _tx_wrap(db, ops)
    await clear_session(db, tid)
    label = f"\n🏷️ {h(note)}" if note else ""
    await update.message.reply_text(f"✅ Gasto registrado\n💸 €{h(f'{amt:.2f}')}\n📌 {h(cat)}\n💼 {h(acc['name'])}{label}", parse_mode=ParseMode.HTML)
    await _check_budget_warning(db, uid, cat, update)

async def _finalize_income_with_note(db, tid, uid, sdata, note, update):
    aid = sdata["incomeAccountId"]
    amt = sdata["incomeAmount"]
    conc = sdata["incomeConcept"]
    acc = await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
    if not acc:
        await update.message.reply_text("Cuenta no encontrada.")
        return
    await _tx_wrap(db,[
        ("INSERT INTO transactions(user_id,account_id,amount,type,category,description) VALUES(?,?,?,'INGRESO',?,?)",(uid,aid,amt,conc,note or "")),
        ("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,aid))
    ])
    await clear_session(db, tid)
    label = f"\n🏷️ {h(note)}" if note else ""
    await update.message.reply_text(f"✅ Ingreso registrado\n💰 €{h(f'{amt:.2f}')}\n📝 {h(conc)}\n💼 {h(acc['name'])}{label}", parse_mode=ParseMode.HTML)

async def _hfc_rec_freq(db,tid,uid,sdata,d,q,update,ctx):
    key = _cb_suffix_text(d, "freq_")
    if key not in FREQ_MAP: return await q.edit_message_text("Opcion invalida.")
    sdata["recurringFrequency"]=FREQ_MAP[key]; await save_session(db,tid,"waiting_recurring_category",sdata)
    rec_amt="{:.2f}".format(sdata.get('recurringAmount',0))
    await q.edit_message_text(f"Gasto recurrente: {h(sdata.get('recurringName',''))}\nMonto: €{h(rec_amt)}\nFrecuencia: {h(FREQ_MAP[key])}\n\nSelecciona la categoria:",
        reply_markup=multi_kb(CATEGORY_KBD_ITEMS,"rrcat",cols=2,extra=None))

async def _hfc_rec_cat(db,tid,uid,sdata,d,q,update,ctx):
    key = _cb_suffix_text(d, "rrcat_")
    if key not in CATEGORY_MAP: return await q.edit_message_text("Opcion invalida.")
    sdata["recurringCategory"]=CATEGORY_MAP[key]; accts=await get_accounts(db,uid)
    await save_session(db,tid,"waiting_recurring_account",sdata)
    rec_amt2="{:.2f}".format(sdata.get('recurringAmount',0))
    await q.edit_message_text(f"Gasto recurrente: {h(sdata.get('recurringName',''))}\nMonto: €{h(rec_amt2)}\nFrecuencia: {h(sdata.get('recurringFrequency',''))}\nCategoria: {h(CATEGORY_MAP[key])}\n\nSelecciona la cuenta:",
        reply_markup=_acct_kb(accts,"rec_acc",None))

async def _hfc_rec_acc(db,tid,uid,sdata,d,q,update,ctx):
    aid = _cb_suffix_int(d, "rec_acc_")
    if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
    if sdata.get("recurringAmount", 0) <= 0:
        return await q.edit_message_text("❌ Cantidad invalida. Inicia de nuevo con /agregarrecurrente.")
    await db.execute("INSERT INTO recurring_expenses(user_id,name,amount,frequency,next_date,category,account_id) VALUES(?,?,?,?,?,?,?)",
                     (uid,sdata["recurringName"],sdata["recurringAmount"],sdata["recurringFrequency"],datetime.now().isoformat(),sdata["recurringCategory"],aid))
    await db.commit(); await clear_session(db,tid)
    ramt_val="{:.2f}".format(sdata["recurringAmount"])
    await q.edit_message_text(f"✅ Gasto recurrente añadido\n📅 {h(sdata['recurringName'])}\n💵 €{h(ramt_val)} ({h(sdata['recurringFrequency'])})",parse_mode=ParseMode.HTML)

async def _hfc_rec_income_freq(db,tid,uid,sdata,d,q,update,ctx):
    key = _cb_suffix_text(d, "freqinc_")
    if key not in FREQ_MAP: return await q.edit_message_text("Opcion invalida.")
    sdata["recurringIncomeFrequency"] = FREQ_MAP[key]
    accts = await get_accounts(db, uid)
    await save_session(db, tid, "waiting_recurring_income_account", sdata)
    inc_amt = "{:.2f}".format(sdata.get("recurringIncomeAmount", 0))
    await q.edit_message_text(
        f"Ingreso recurrente: {h(sdata.get('recurringIncomeName',''))}\nMonto: €{h(inc_amt)}\nFrecuencia: {h(sdata['recurringIncomeFrequency'])}\n\nSelecciona la cuenta:",
        reply_markup=_acct_kb(accts, "inc_rec_acc", None)
    )

async def _hfc_rec_income_acc(db,tid,uid,sdata,d,q,update,ctx):
    aid = _cb_suffix_int(d, "inc_rec_acc_")
    if aid is None: return await q.edit_message_text("❌ Opcion invalida. Usa /start para reiniciar.")
    amt = sdata.get("recurringIncomeAmount", 0)
    if amt <= 0:
        return await q.edit_message_text("❌ Cantidad invalida. Inicia de nuevo con /agregaringresorecurrente.")
    await db.execute(
        "INSERT INTO recurring_expenses(user_id,name,amount,frequency,next_date,category,account_id,type) VALUES(?,?,?,?,?,?,?,?)",
        (uid, sdata["recurringIncomeName"], amt, sdata["recurringIncomeFrequency"], datetime.now().isoformat(), "Ingreso recurrente", aid, "INGRESO")
    )
    await db.commit(); await clear_session(db,tid)
    await q.edit_message_text(
        f"✅ Ingreso recurrente añadido\n📅 {h(sdata['recurringIncomeName'])}\n💵 €{h(f'{amt:.2f}')} ({h(sdata['recurringIncomeFrequency'])})",
        parse_mode=ParseMode.HTML
    )

_FLOW_CALLBACK_MAP = {
    "waiting_account_type": ("type_", _hfc_acct_type),
    "waiting_expense_category": ("cat_", _hfc_expense_cat),
    "waiting_expense_date": ("expdate_", _hfc_expense_date),
    "waiting_expense_account": ("exp_acc_", _hfc_expense_acc),
    "waiting_income_account": ("inc_acc_", _hfc_income_acc),
    "waiting_recurring_frequency": ("freq_", _hfc_rec_freq),
    "waiting_recurring_category": ("rrcat_", _hfc_rec_cat),
    "waiting_recurring_account": ("rec_acc_", _hfc_rec_acc),
    "waiting_recurring_income_frequency": ("freqinc_", _hfc_rec_income_freq),
    "waiting_recurring_income_account": ("inc_rec_acc_", _hfc_rec_income_acc),
}

async def handle_flow_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    if await _check_session_expiry(db,tid):
        return await q.edit_message_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    s=await get_session(db,tid)
    if not s: return await q.edit_message_text("Sesion expirada. Usa /start.", parse_mode=ParseMode.HTML)
    sdata=json.loads(s["data"]) if s["data"] else {}; d=q.data; state=s["state"]
    entry=_FLOW_CALLBACK_MAP.get(state)
    if entry:
        prefix, handler = entry
        if d.startswith(prefix):
            return await handler(db,tid,uid,sdata,d,q,update,ctx)


# ── TEXT HANDLER ─────────────────────────────────────────────────────

async def _ht_acct_name(db,tid,uid,text,sdata,update,ctx):
    sdata["accountName"]=text; await save_session(db,tid,"waiting_account_type",sdata)
    await update.message.reply_text("Selecciona el tipo de cuenta:", reply_markup=multi_kb(TYPE_KBD_ITEMS,"type",cols=4,extra=None))

async def _ht_acct_balance(db,tid,uid,text,sdata,update,ctx):
    bal=parse_amount(text)
    if bal is None: return await update.message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    try:
        await db.execute("INSERT INTO accounts(user_id,name,type,balance) VALUES(?,?,?,?)",(uid,sdata["accountName"],sdata["accountType"],bal))
        await db.commit(); await clear_session(db,tid)
        await update.message.reply_text(f"✅ Cuenta <b>{h(sdata['accountName'])}</b> creada correctamente con saldo €{h(f'{bal:.2f}')}", parse_mode=ParseMode.HTML)
    except DBIntegrityError:
        await clear_session(db,tid); await update.message.reply_text("Error al crear la cuenta. Probablemente ya existe una con ese nombre.", parse_mode=ParseMode.HTML)

async def _ht_expense_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    if amt <= 0: return await update.message.reply_text("La cantidad debe ser positiva.", parse_mode=ParseMode.HTML)
    sdata["expenseAmount"]=amt
    await save_session(db,tid,"waiting_expense_category",sdata)
    await update.message.reply_text(f"Gasto: €{h(f'{amt:.2f}')}\n\nSelecciona la categoria:", reply_markup=multi_kb(CATEGORY_KBD_ITEMS,"cat",cols=2,extra=None))

async def _ht_expense_date_custom(db,tid,uid,text,sdata,update,ctx):
    try: dt=datetime.strptime(text,"%d/%m/%Y"); sdata["expenseDate"]=dt.isoformat()
    except ValueError: return await update.message.reply_text("Formato invalido. Usa DD/MM/AAAA.\n\n/cancel para cancelar")
    accts=await get_accounts(db,uid); await save_session(db,tid,"waiting_expense_account",sdata)
    exp_amt_c="{:.2f}".format(sdata.get('expenseAmount',0))
    await update.message.reply_text(f"Gasto: €{h(exp_amt_c)} en {h(sdata.get('expenseCategory',''))}\n\nSelecciona la cuenta:",reply_markup=_acct_kb(accts,"exp_acc",None))

async def _ht_income_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida. Intenta de nuevo.", parse_mode=ParseMode.HTML)
    if amt <= 0: return await update.message.reply_text("La cantidad debe ser positiva.", parse_mode=ParseMode.HTML)
    sdata["incomeAmount"]=amt; await save_session(db,tid,"waiting_income_concept",sdata)
    await update.message.reply_text(f"Ingreso: €{h(f'{amt:.2f}')}\n\n¿Cual es el concepto?\n(Ejemplo: Freelance, Regalo, Bonificacion)\n\n/cancel para cancelar")

async def _ht_income_concept(db,tid,uid,text,sdata,update,ctx):
    sdata["incomeConcept"]=text; accts=await get_accounts(db,uid)
    await save_session(db,tid,"waiting_income_account",sdata)
    inc_amt="{:.2f}".format(sdata.get('incomeAmount',0))
    await update.message.reply_text(f"Ingreso: €{h(inc_amt)}\nConcepto: {h(text)}\n\nSelecciona la cuenta donde ingresa el dinero:",reply_markup=_acct_kb(accts,"inc_acc",None))

async def _ht_expense_note(db,tid,uid,text,sdata,update,ctx):
    if text == "/saltar":
        note = ""
    else:
        note = text
    await _finalize_expense_with_note(db, tid, uid, sdata, note, update)

async def _ht_income_note(db,tid,uid,text,sdata,update,ctx):
    if text == "/saltar":
        note = ""
    else:
        note = text
    await _finalize_income_with_note(db, tid, uid, sdata, note, update)

async def _ht_transfer_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida.")
    if amt<=0: return await update.message.reply_text("La cantidad debe ser positiva.")
    fid=sdata["from_id"]; tid_dst=sdata["to_id"]
    src=await (await db.execute("SELECT * FROM accounts WHERE id=?",(fid,))).fetchone()
    dst=await (await db.execute("SELECT * FROM accounts WHERE id=?",(tid_dst,))).fetchone()
    if src["balance"]<amt:
        bal_val="{:.2f}".format(src["balance"])
        return await update.message.reply_text(f"❌ Saldo insuficiente en <b>{h(src['name'])}</b> (€{h(bal_val)})",parse_mode=ParseMode.HTML)
    await _tx_wrap(db,[
        ("UPDATE accounts SET balance=balance-? WHERE id=?",(amt,fid)),
        ("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,tid_dst)),
        ("INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Transferencia',?,?)",(uid,fid,amt,f"Transferencia a {dst['name']}",tid_dst)),
    ])
    await clear_session(db,tid)
    await update.message.reply_text(f"✅ Transferencia realizada\n💱 €{h(f'{amt:.2f}')}\n📤 {h(src['name'])} → 📥 {h(dst['name'])}",parse_mode=ParseMode.HTML)

async def _ht_recurring_name(db,tid,uid,text,sdata,update,ctx):
    sdata["recurringName"]=text; await save_session(db,tid,"waiting_recurring_amount",sdata)
    await update.message.reply_text(f"Nombre: {h(text)}\n\n¿Cual es la cantidad?\n(Formato: cantidad)\n\n/cancel para cancelar")

async def _ht_recurring_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida.")
    if amt <= 0: return await update.message.reply_text("La cantidad debe ser positiva.")
    sdata["recurringAmount"]=amt; await save_session(db,tid,"waiting_recurring_frequency",sdata)
    await update.message.reply_text(f"Gasto recurrente: {h(sdata.get('recurringName',''))}\nMonto: €{h(f'{amt:.2f}')}\n\nSelecciona la frecuencia:",reply_markup=multi_kb(FREQ_KBD_ITEMS,"freq",cols=2,extra=None))

async def _ht_recurring_income_name(db,tid,uid,text,sdata,update,ctx):
    sdata["recurringIncomeName"] = text
    await save_session(db,tid,"waiting_recurring_income_amount",sdata)
    await update.message.reply_text(f"Nombre: {h(text)}\n\n¿Cual es la cantidad?\n(Formato: cantidad)\n\n/cancel para cancelar")

async def _ht_recurring_income_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida.")
    if amt <= 0: return await update.message.reply_text("La cantidad debe ser positiva.")
    sdata["recurringIncomeAmount"] = amt
    await save_session(db,tid,"waiting_recurring_income_frequency",sdata)
    await update.message.reply_text(
        f"Ingreso recurrente: {h(sdata.get('recurringIncomeName',''))}\nMonto: €{h(f'{amt:.2f}')}\n\nSelecciona la frecuencia:",
        reply_markup=multi_kb(FREQ_KBD_ITEMS,"freqinc",cols=2,extra=None)
    )

async def _ht_alert_threshold(db,tid,uid,text,sdata,update,ctx):
    th=parse_amount(text)
    if th is None: return await update.message.reply_text("Cantidad invalida.")
    if th <= 0: return await update.message.reply_text("La cantidad debe ser positiva.")
    await db.execute("INSERT OR REPLACE INTO low_balance_alerts(telegram_id,account_id,threshold,enabled) VALUES(?,?,?,1)",(tid,sdata["account_id"],th))
    await db.commit(); await clear_session(db,tid)
    await update.message.reply_text(f"✅ Alerta configurada\n🔔 Se te notificara cuando el saldo sea menor a €{h(f'{th:.2f}')}", parse_mode=ParseMode.HTML)

async def _ht_budget_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None: return await update.message.reply_text("Cantidad invalida.")
    if amt<=0: return await update.message.reply_text("La cantidad debe ser positiva.")
    cat_key=sdata["budgetCategory"]; cat_name=CATEGORY_MAP[cat_key]
    now=datetime.now(); month=f"{now.year}-{now.month:02d}"
    await db.execute("INSERT OR REPLACE INTO budgets(user_id,category,amount,month) VALUES(?,?,?,?)",(uid,cat_name,amt,month))
    await db.commit(); await clear_session(db,tid)
    await update.message.reply_text(f"✅ Presupuesto configurado\n📊 {h(cat_name)}: €{h(f'{amt:.2f}')} para {h(MONTHS_ES[now.month])} {h(str(now.year))}", parse_mode=ParseMode.HTML)

async def _ht_goal_name(db,tid,uid,text,sdata,update,ctx):
    sdata["goalName"]=text; await save_session(db,tid,"waiting_goal_target",sdata)
    await update.message.reply_text(f"Meta: {h(text)}\n\n¿Cual es el monto objetivo?\n(Formato: cantidad)\n\n/cancel para cancelar")

async def _ht_goal_target(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None or amt<=0: return await update.message.reply_text("Cantidad invalida. Debe ser positiva.")
    sdata["goalTarget"]=amt; await save_session(db,tid,"waiting_goal_deadline",sdata)
    await update.message.reply_text(f"Meta: {h(sdata.get('goalName',''))} — Objetivo: €{h(f'{amt:.2f}')}\n\n¿Fecha limite? (DD/MM/AAAA o /saltar si no hay)",parse_mode=ParseMode.HTML)

async def _ht_goal_deadline(db,tid,uid,text,sdata,update,ctx):
    dl=None
    if text!="/saltar":
        try: dl=datetime.strptime(text,"%d/%m/%Y").isoformat()
        except ValueError: return await update.message.reply_text("Formato invalido. Usa DD/MM/AAAA o /saltar")
    await db.execute("INSERT INTO savings_goals(user_id,name,target_amount,deadline) VALUES(?,?,?,?)",(uid,sdata["goalName"],sdata["goalTarget"],dl))
    await db.commit(); await clear_session(db,tid)
    gt_amt="{:.2f}".format(sdata["goalTarget"])
    await update.message.reply_text(f"✅ Meta creada\n🎯 {h(sdata['goalName'])}: €{h(gt_amt)}",parse_mode=ParseMode.HTML)

async def _ht_aportar_amount(db,tid,uid,text,sdata,update,ctx):
    amt=parse_amount(text)
    if amt is None or amt<=0: return await update.message.reply_text("Cantidad invalida.")
    gid=sdata["goalId"]
    await db.execute("UPDATE savings_goals SET current_amount=current_amount+? WHERE id=? AND user_id=?",(amt,gid,uid))
    await db.commit(); await clear_session(db,tid)
    await update.message.reply_text(f"✅ Aporte registrado\n🎯 +€{h(f'{amt:.2f}')} a tu meta",parse_mode=ParseMode.HTML)

async def _ht_menu_recurrente(db,tid,uid,text,sdata,update,ctx):
    if text=="/agregarrecurrente":
        await save_session(db,tid,"waiting_recurring_name")
        await update.message.reply_text("¿Cual es el nombre del gasto recurrente?\n(Ejemplo: Netflix, Seguro del coche)\n\n/cancel para cancelar")

async def _ht_menu_alertas(db,tid,uid,text,sdata,update,ctx):
    if text=="/agregaralerta":
        accts=await get_accounts(db,uid)
        if not accts: return await update.message.reply_text("Crea una cuenta primero con /nuevacuenta")
        await update.message.reply_text("Selecciona la cuenta:", reply_markup=_acct_kb(accts,"alert_acc",None))
    elif text=="/borraralerta":
        c=await db.execute("SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?",(tid,))
        alerts=await c.fetchall()
        if not alerts: return await update.message.reply_text("No tienes alertas para eliminar.")
        btns=[(f"{a['name']} — €{a['threshold']:.2f}",f"del_alert_{a['id']}") for a in alerts]
        btns.append(("Cancelar","cancel_action"))
        await update.message.reply_text("Selecciona la alerta a eliminar:", reply_markup=_kb(btns))

async def _ht_menu_ingresorec(db,tid,uid,text,sdata,update,ctx):
    await clear_session(db,tid)
    await update.message.reply_text("Usa /agregaringresorecurrente para agregar un ingreso recurrente.",parse_mode=ParseMode.HTML)

_TEXT_HANDLERS = {
    "waiting_account_name": _ht_acct_name,
    "waiting_account_balance": _ht_acct_balance,
    "waiting_expense_amount": _ht_expense_amount,
    "waiting_expense_note": _ht_expense_note,
    "waiting_expense_date_custom": _ht_expense_date_custom,
    "waiting_income_amount": _ht_income_amount,
    "waiting_income_concept": _ht_income_concept,
    "waiting_income_note": _ht_income_note,
    "waiting_transfer_amount": _ht_transfer_amount,
    "waiting_recurring_name": _ht_recurring_name,
    "waiting_recurring_amount": _ht_recurring_amount,
    "waiting_recurring_income_name": _ht_recurring_income_name,
    "waiting_recurring_income_amount": _ht_recurring_income_amount,
    "waiting_alert_threshold": _ht_alert_threshold,
    "waiting_budget_amount": _ht_budget_amount,
    "waiting_goal_name": _ht_goal_name,
    "waiting_goal_target": _ht_goal_target,
    "waiting_goal_deadline": _ht_goal_deadline,
    "waiting_aportar_amount": _ht_aportar_amount,
    "menu_recurrente": _ht_menu_recurrente,
    "menu_alertas": _ht_menu_alertas,
    "menu_ingresorec": _ht_menu_ingresorec,
}

async def handle_text(update,ctx):
    db=await get_db(); tid=update.effective_user.id; text=update.message.text.strip()
    s=await get_session(db,tid)
    if await _check_session_expiry(db,tid):
        return await update.message.reply_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    if not s: return await update.message.reply_text("Usa /start para comenzar o /help para ver los comandos disponibles.")
    uid=await get_or_create_user(db,tid); state=s["state"]
    sdata=json.loads(s["data"]) if s["data"] else {}
    handler=_TEXT_HANDLERS.get(state)
    if handler: return await handler(db,tid,uid,text,sdata,update,ctx)
    await update.message.reply_text("Usa /start para comenzar o /help para ver los comandos disponibles.")

# ── FLASK APP ────────────────────────────────────────────────────────
app = Flask(__name__)
# WSGI entrypoint expected by PythonAnywhere (`from bot_pythonanywhere import application`)
application = app
ptb_app = None
_event_loop = None
_ptb_app_lock = asyncio.Lock()

def get_event_loop():
    global _event_loop
    if _event_loop is None:
        _event_loop = asyncio.new_event_loop(); asyncio.set_event_loop(_event_loop)
    return _event_loop

async def _create_ptb_app():
    global ptb_app
    if ptb_app is not None:
        return ptb_app

    async with _ptb_app_lock:
        if ptb_app is not None:
            return ptb_app
        application = Application.builder().token(TOKEN).build()
        application.bot_data["db"] = await init_db()

        application.add_handler(CommandHandler("start",cmd_start))
        application.add_handler(CommandHandler("help",cmd_help))
        application.add_handler(CommandHandler("menu",cmd_menu))
        application.add_handler(CommandHandler("cancel",cmd_cancel))
        application.add_handler(CommandHandler("cuentas",cmd_cuentas))
        application.add_handler(CommandHandler("nuevacuenta",cmd_nueva_cuenta))
        application.add_handler(CommandHandler("borrarcuenta",cmd_borrar_cuenta))
        application.add_handler(CommandHandler("gasto",cmd_gasto))
        application.add_handler(CommandHandler("ingreso",cmd_ingreso))
        application.add_handler(CommandHandler("traspaso",cmd_traspaso))
        application.add_handler(CommandHandler("deshacer",cmd_deshacer))
        application.add_handler(CommandHandler("redondeo",cmd_redondeo))
        application.add_handler(CommandHandler("redondeotoggle",cmd_redondeo_toggle))
        application.add_handler(CommandHandler("redondeocuenta",cmd_redondeo_cuenta))
        application.add_handler(CommandHandler("recurrente",cmd_recurrente))
        application.add_handler(CommandHandler("agregarrecurrente",cmd_agregar_recurrente))
        application.add_handler(CommandHandler("borrarrecurrente",cmd_borrar_recurrente))
        application.add_handler(CommandHandler("resumen",cmd_resumen))
        application.add_handler(CommandHandler("stats",cmd_stats))
        application.add_handler(CommandHandler("tendencia",cmd_tendencia))
        application.add_handler(CommandHandler("panel",cmd_panel))
        application.add_handler(CommandHandler("forecast",cmd_forecast))
        application.add_handler(CommandHandler("anomalias",cmd_anomalias))
        application.add_handler(CommandHandler("tags",cmd_tags))
        application.add_handler(CommandHandler("sugerircategoria",cmd_sugerircategoria))
        application.add_handler(CommandHandler("exportar",cmd_exportar))
        application.add_handler(CommandHandler("alertas",cmd_alertas))
        application.add_handler(CommandHandler("agregaralerta",cmd_agregar_alerta))
        application.add_handler(CommandHandler("borraralerta",cmd_borrar_alerta))
        application.add_handler(CommandHandler("reset",cmd_reset))
        application.add_handler(CommandHandler("presupuesto",cmd_presupuesto))
        application.add_handler(CommandHandler("presupuestoset",cmd_presupuestoset))
        application.add_handler(CommandHandler("buscar",cmd_buscar))
        application.add_handler(CommandHandler("metas",cmd_metas))
        application.add_handler(CommandHandler("nuevameta",cmd_nuevameta))
        application.add_handler(CommandHandler("aportarmeta",cmd_aportarmeta))
        application.add_handler(CommandHandler("agregaringresorecurrente",cmd_agregaringresorecurrente))
        application.add_handler(CommandHandler("ingresorecurrente",cmd_ingresorecurrente))
        application.add_handler(CallbackQueryHandler(handle_menu_callback,pattern="^menu_.*"))
        application.add_handler(CallbackQueryHandler(handle_resumen_callback,pattern="^resumen_.*"))
        application.add_handler(CallbackQueryHandler(handle_budget_callback,pattern="^budcat_.*"))
        application.add_handler(CallbackQueryHandler(handle_callback,pattern="^(cancel_action|aportar_goal_|del_account_|del_account_confirm_|xfer_from_|xfer_to_|del_recurring_|del_recurring_confirm_|alert_acc_|del_alert_|del_alert_confirm_|roundup_acc_|reset_confirm|undo_).*"))
        application.add_handler(CallbackQueryHandler(handle_flow_callback,pattern="^(type_|cat_|expdate_|exp_acc_|inc_acc_|freq_|rrcat_|rec_acc_|freqinc_|inc_rec_acc_).*"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_text))
        application.add_error_handler(_ptb_error_handler)

        await application.initialize(); await application.start()

        if application.job_queue is not None:
            async def check_recurring_reminders(ctx):
                db=application.bot_data["db"]
                now=datetime.now(); target=now+timedelta(days=1)
                c=await db.execute("SELECT r.*,u.telegram_id FROM recurring_expenses r JOIN users u ON r.user_id=u.id WHERE r.next_date<=?",(target.isoformat(),))
                for rec in await c.fetchall():
                    try:
                        await ctx.bot.send_message(chat_id=rec["telegram_id"],
                            text=f"📅 <b>Recordatorio de pago</b>\n\n{rec['name']}: €{'{:.2f}'.format(rec['amount'])} ({rec['frequency']})",
                            parse_mode=ParseMode.HTML)
                    except Exception:
                        pass

            async def maybe_send_weekly_panel(ctx):
                db=application.bot_data["db"]
                now=datetime.now()
                if now.weekday() != 0:
                    return
                meta = await get_system_state(db)
                meta_data = json.loads(meta["data"]) if meta and meta["data"] else {}
                today = now.date().isoformat()
                if meta_data.get("weekly_panel_last_sent") == today:
                    return
                users = await db._select_rows("users", columns="telegram_id")
                for user in users:
                    try:
                        uid = user.get("telegram_id")
                        if uid is None:
                            continue
                        snapshot = await _build_financial_snapshot(db, user["telegram_id"])
                        anomalies = await _build_anomalies(db, user["telegram_id"])
                        await ctx.bot.send_message(chat_id=user["telegram_id"], text=_format_panel_text(snapshot, anomalies), parse_mode=ParseMode.HTML)
                    except Exception:
                        logger.exception("No se pudo enviar el panel semanal")
                meta_data["weekly_panel_last_sent"] = today
                await save_system_state(db, "bot_meta", meta_data)

            application.job_queue.run_repeating(check_recurring_reminders, interval=3600, first=10)
            application.job_queue.run_repeating(maybe_send_weekly_panel, interval=3600, first=60)
        else:
            logger.warning("JobQueue not available. Recurring reminders disabled.")

        ptb_app = application
        return application

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    loop = get_event_loop()
    if TELEGRAM_WEBHOOK_SECRET:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if received_secret != TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Rejected webhook with invalid Telegram secret token")
            return "forbidden", 403
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.exception("Failed to parse webhook JSON")
        return "bad request", 400

    async def process():
        app_ptb = await _create_ptb_app()
        update = Update.de_json(data, app_ptb.bot)
        await app_ptb.process_update(update)
        return "OK"

    try:
        loop.run_until_complete(process())
    except Exception as e:
        logger.exception("Error processing webhook update")
        return "error", 500
    return "OK", 200

@app.route("/")
def index(): return "Finance Bot OK", 200
