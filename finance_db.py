import asyncio
import logging
import os
import sqlite3
import sys
from pathlib import Path

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

        if q in ("BEGIN", "ROLLBACK"):
            return SupabaseCursor()
        if q.startswith("ALTER TABLE "):
            return SupabaseCursor()

        if q == "SELECT id FROM users WHERE telegram_id=?":
            rows = await self._select_rows("users", columns="id", filters=[("eq", "telegram_id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT INTO users(telegram_id) VALUES(?)":
            row = await self._insert_row("users", {"telegram_id": p[0]})
            return SupabaseCursor(lastrowid=row.get("id"))

        if q == "SELECT state,data,created_at FROM session_states WHERE telegram_id=?":
            rows = await self._select_rows("session_states", columns="state,data,created_at", filters=[("eq", "telegram_id", p[0])], limit=1)
            return SupabaseCursor(rows)
        if q == "INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)":
            await self._upsert_row("session_states", {"telegram_id": p[0], "state": p[1], "data": p[2], "created_at": p[3]}, on_conflict="telegram_id")
            return SupabaseCursor()
        if q == "DELETE FROM session_states WHERE telegram_id=?":
            await self._delete_rows("session_states", [("eq", "telegram_id", p[0])])
            return SupabaseCursor()

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

        if q == "SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'":
            rows = await self._select_rows("transactions", columns="type,amount", filters=[("eq", "user_id", p[0]), ("gte", "date", p[1]), ("lte", "date", p[2]), ("neq", "type", "TRANSFERENCIA")])
            return SupabaseCursor(rows)
        if q == "SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?":
            rows = await self._select_rows("transactions", columns="category,amount", filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("gte", "date", p[1]), ("lte", "date", p[2])])
            return SupabaseCursor(rows)
        if q == "SELECT SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND category=? AND date>=? AND date<=?":
            rows = await self._select_rows("transactions", columns="amount", filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("eq", "category", p[1]), ("gte", "date", p[2]), ("lte", "date", p[3])])
            total = sum(r["amount"] for r in rows) if rows else None
            return SupabaseCursor([{"total": total}])
        if q == "SELECT category,SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=? GROUP BY category":
            rows = await self._select_rows("transactions", columns="category,amount", filters=[("eq", "user_id", p[0]), ("eq", "type", "GASTO"), ("gte", "date", p[1]), ("lte", "date", p[2])])
            grouped = {}
            for r in rows:
                grouped[r["category"]] = grouped.get(r["category"], 0) + r["amount"]
            return SupabaseCursor([{"category": c, "total": t} for c, t in grouped.items()])
        if q == "SELECT * FROM transactions WHERE user_id=? AND type IN ('GASTO','INGRESO','TRANSFERENCIA') ORDER BY id DESC LIMIT 10":
            rows = await self._select_rows("transactions", filters=[("eq", "user_id", p[0]), ("in", "type", ["GASTO", "INGRESO", "TRANSFERENCIA"])], order_by="id", desc=True, limit=10)
            return SupabaseCursor(rows)
        if q == "SELECT * FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA' ORDER BY date DESC":
            rows = await self._select_rows("transactions", filters=[("eq", "user_id", p[0]), ("gte", "date", p[1]), ("lte", "date", p[2]), ("neq", "type", "TRANSFERENCIA")], order_by="date", desc=True)
            return SupabaseCursor(rows)
        if q == "SELECT * FROM transactions WHERE id=? AND user_id=?":
            rows = await self._select_rows("transactions", filters=[("eq", "id", p[0]), ("eq", "user_id", p[1])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? AND t.description LIKE ? ORDER BY t.date DESC LIMIT 10":
            txs = await self._select_rows("transactions", filters=[("eq", "user_id", p[0]), ("like", "description", p[1])], order_by="date", desc=True, limit=10)
            if not txs:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({t["account_id"] for t in txs}))])
            amap = {a["id"]: a["name"] for a in arows}
            return SupabaseCursor([dict(t, aname=amap.get(t["account_id"], "—")) for t in txs])
        if q == "SELECT t.*,a.name as aname FROM transactions t JOIN accounts a ON t.account_id=a.id WHERE t.user_id=? ORDER BY t.date DESC":
            txs = await self._select_rows("transactions", filters=[("eq", "user_id", p[0])], order_by="date", desc=True)
            if not txs:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({t["account_id"] for t in txs}))])
            amap = {a["id"]: a["name"] for a in arows}
            return SupabaseCursor([dict(t, aname=amap.get(t["account_id"], "—")) for t in txs])
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
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "TRANSFERENCIA", "category": "Redondeo", "description": p[3], "linked_account_id": p[4]})
            return SupabaseCursor()
        if q == "INSERT INTO transactions(user_id,account_id,amount,type,category,description,linked_account_id) VALUES(?,?,?,'TRANSFERENCIA','Transferencia',?,?)":
            await self._insert_row("transactions", {"user_id": p[0], "account_id": p[1], "amount": p[2], "type": "TRANSFERENCIA", "category": "Transferencia", "description": p[3], "linked_account_id": p[4]})
            return SupabaseCursor()

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
            await self._insert_row("recurring_expenses", {"user_id": p[0], "name": p[1], "amount": p[2], "frequency": p[3], "next_date": p[4], "category": p[5], "account_id": p[6], "type": "GASTO"})
            return SupabaseCursor()
        if q == "INSERT INTO recurring_expenses(user_id,name,amount,frequency,next_date,category,account_id,type) VALUES(?,?,?,?,?,?,?,?)":
            await self._insert_row("recurring_expenses", {"user_id": p[0], "name": p[1], "amount": p[2], "frequency": p[3], "next_date": p[4], "category": p[5], "account_id": p[6], "type": p[7]})
            return SupabaseCursor()
        if q == "SELECT r.*,u.telegram_id FROM recurring_expenses r JOIN users u ON r.user_id=u.id WHERE r.next_date<=?":
            recs = await self._select_rows("recurring_expenses", filters=[("lte", "next_date", p[0])])
            if not recs:
                return SupabaseCursor([])
            users = await self._select_rows("users", columns="id,telegram_id", filters=[("in", "id", list({r["user_id"] for r in recs}))])
            umap = {u["id"]: u["telegram_id"] for u in users}
            out = [dict(r, telegram_id=umap.get(r["user_id"])) for r in recs if r["user_id"] in umap]
            return SupabaseCursor(out)

        if q == "SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.telegram_id=?":
            alerts = await self._select_rows("low_balance_alerts", filters=[("eq", "telegram_id", p[0])])
            if not alerts:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("in", "id", list({a["account_id"] for a in alerts}))])
            amap = {a["id"]: a["name"] for a in arows}
            return SupabaseCursor([dict(a, name=amap.get(a["account_id"], "—")) for a in alerts])
        if q == "SELECT la.*,a.name FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id WHERE la.id=?":
            alerts = await self._select_rows("low_balance_alerts", filters=[("eq", "id", p[0])], limit=1)
            if not alerts:
                return SupabaseCursor([])
            arows = await self._select_rows("accounts", columns="id,name", filters=[("eq", "id", alerts[0]["account_id"])], limit=1)
            name = arows[0]["name"] if arows else "—"
            return SupabaseCursor([dict(alerts[0], name=name)])
        if q == "INSERT OR REPLACE INTO low_balance_alerts(telegram_id,account_id,threshold,enabled) VALUES(?,?,?,1)":
            await self._upsert_row("low_balance_alerts", {"telegram_id": p[0], "account_id": p[1], "threshold": p[2], "enabled": True}, on_conflict="telegram_id,account_id")
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

        if q == "SELECT amount FROM budgets WHERE user_id=? AND category=? AND month=?":
            rows = await self._select_rows("budgets", columns="amount", filters=[("eq", "user_id", p[0]), ("eq", "category", p[1]), ("eq", "month", p[2])], limit=1)
            return SupabaseCursor(rows)
        if q == "SELECT category,amount FROM budgets WHERE user_id=? AND month=?":
            rows = await self._select_rows("budgets", columns="category,amount", filters=[("eq", "user_id", p[0]), ("eq", "month", p[1])])
            return SupabaseCursor(rows)
        if q == "INSERT OR REPLACE INTO budgets(user_id,category,amount,month) VALUES(?,?,?,?)":
            await self._upsert_row("budgets", {"user_id": p[0], "category": p[1], "amount": p[2], "month": p[3]}, on_conflict="user_id,category,month")
            return SupabaseCursor()

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


LEGACY_SQLITE_PATH = Path("/home/sirsoto25/bot/finance.db")
logger = logging.getLogger(__name__)


async def _tx_wrap(db, ops):
    await db.execute("BEGIN")
    try:
        for sql, params in ops:
            await db.execute(sql, params)
        await db.commit()
    except Exception:
        await db.execute("ROLLBACK")
        raise


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


_app_db = None


async def init_db(supabase_url=None, supabase_key=None):
    if not supabase_url:
        supabase_url = os.environ.get("SUPABASE_URL", "")
    if not supabase_key:
        supabase_key = os.environ.get("SUPABASE_KEY", "")
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
    if not supabase_url or not supabase_key:
        raise RuntimeError("Configura SUPABASE_URL y SUPABASE_KEY en variables de entorno.")

    db = SupabaseDB(supabase_url, supabase_key)
    try:
        await db._select_rows("users", columns="id", limit=1)
    except Exception as err:
        raise RuntimeError(
            "No se pudo acceder a la tabla 'users' en Supabase. "
            "Crea el esquema en Supabase antes de iniciar el bot."
        ) from err
    await migrate_legacy_sqlite(db)
    return db


async def get_db(supabase_url=None, supabase_key=None):
    global _app_db
    if _app_db is None:
        _app_db = await init_db(supabase_url, supabase_key)
    return _app_db
