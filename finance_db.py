import asyncio
import logging
import os
import re
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


_OP_MAP = {"=": "eq", "!=": "neq", "<>": "neq", ">=": "gte", "<=": "lte", ">": "gt", "<": "lt", "like": "like", "in": "in"}


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

    async def _run(self, fn, max_retries=3):
        last_err = None
        for attempt in range(max_retries):
            try:
                return await asyncio.to_thread(fn)
            except Exception as err:
                last_err = err
                if _is_rls_denied(err):
                    raise RuntimeError(
                        "Supabase rechazo la escritura por RLS (42501). "
                        "Usa SUPABASE_KEY con la service_role key para este backend, "
                        "o crea politicas RLS que permitan INSERT/UPDATE/DELETE."
                    ) from err
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        raise last_err

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

    def _parse_columns(self, sql_part):
        if not sql_part or sql_part.strip() == "*":
            return "*"
        return [c.strip() for c in sql_part.split(",")]

    def _map_op(self, sql_op):
        return _OP_MAP.get(sql_op.lower(), "eq")

    def _parse_where_clause(self, where_text):
        conds = []
        parts = re.split(r"\s+AND\s+", where_text, flags=re.I)
        for part in parts:
            part = part.strip()
            m = re.match(r"(\w+)\.(\w+)\s*(=|!=|>=|<=|>|<|LIKE|IN)\s*(\?|'[^']*'|\([^)]+\))", part, re.I)
            if m:
                conds.append((m.group(2), m.group(3).lower(), m.group(4), part))
                continue
            m = re.match(r"(\w+)\s*(=|!=|>=|<=|>|<|LIKE|IN)\s*(\?|'[^']*'|\([^)]+\))", part, re.I)
            if m:
                conds.append((m.group(1), m.group(2).lower(), m.group(3), part))
                continue
        return conds

    def _parse_select(self, q, p):
        result = {"op": "SELECT", "params": p, "filters": [], "order_by": None, "desc": False, "limit": None, "group_by": None, "raw_q": q}

        m = re.match(r"SELECT\s+(.+?)\s+FROM\s+(\w+)", q, re.I)
        if m:
            result["columns"] = self._parse_columns(m.group(1))
            result["table"] = m.group(2)

        join_m = re.search(r"JOIN\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", q, re.I)
        if join_m:
            result["join_table"] = join_m.group(1)
            result["join_alias"] = join_m.group(2) or join_m.group(1)
            result["join_left"] = (join_m.group(3), join_m.group(4))
            result["join_right"] = (join_m.group(5), join_m.group(6))

        where_match = re.search(r"\bWHERE\s+(.+?)\s*(?:\b(?:ORDER|GROUP|LIMIT)\b|$)", q, re.I)
        if where_match:
            conds = self._parse_where_clause(where_match.group(1))
            p_idx = 0
            for col, op, val, _raw in conds:
                mapped_op = self._map_op(op)
                if val == "?":
                    result["filters"].append((mapped_op, col, p[p_idx] if p_idx < len(p) else None))
                    p_idx += 1
                elif val.startswith("'") and val.endswith("'"):
                    result["filters"].append(("neq" if op in ("!=", "<>") else "eq", col, val.strip("'")))
                elif val.startswith("("):
                    cleaned = [v.strip().strip("'") for v in val[1:-1].split(",")]
                    result["filters"].append((mapped_op, col, cleaned))
                else:
                    result["filters"].append((mapped_op, col, val))
                    p_idx += 1

        order_match = re.search(r"\bORDER\s+BY\s+(\w+)\.?(\w+)?(?:\s+(DESC|ASC))?", q, re.I)
        if order_match:
            result["order_by"] = order_match.group(2) or order_match.group(1)
            result["desc"] = (order_match.group(3) or "").upper() == "DESC"

        limit_match = re.search(r"\bLIMIT\s+(\d+)", q, re.I)
        if limit_match:
            result["limit"] = int(limit_match.group(1))

        group_match = re.search(r"\bGROUP\s+BY\s+(\w+)", q, re.I)
        if group_match:
            result["group_by"] = group_match.group(1)

        agg_match = re.search(r"(\w+)\(([^)]+)\)\s+(?:as\s+)?(\w+)", q, re.I)
        if agg_match:
            result["aggregate"] = (agg_match.group(1).upper(), agg_match.group(2).strip(), agg_match.group(3))

        return result

    def _parse_insert(self, q, p):
        result = {"op": "INSERT", "table": None, "columns": [], "values": [], "params": p, "upsert_on_conflict": None}
        if "OR REPLACE" in q.upper():
            result["op"] = "UPSERT"

        m = re.match(r"INSERT(?:\s+OR\s+REPLACE)?\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)", q, re.I)
        if m:
            result["table"] = m.group(1)
            result["columns"] = [c.strip() for c in m.group(2).split(",")]
            values_raw = [v.strip() for v in m.group(3).split(",")]
            p_idx = 0
            for val in values_raw:
                if val == "?":
                    result["values"].append(p[p_idx])
                    p_idx += 1
                elif val.startswith("'") and val.endswith("'"):
                    result["values"].append(val[1:-1])
                elif val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
                    result["values"].append(int(val) if val.isdigit() or (val[0] == "-" and val[1:].isdigit()) else val)
                else:
                    result["values"].append(val)

        return result

    def _parse_update(self, q, p):
        result = {"op": "UPDATE", "table": None, "sets": {}, "filters": [], "params": p}
        m = re.match(r"UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)", q, re.I)
        if m:
            result["table"] = m.group(1)
            set_clause = m.group(2)
            where_clause = m.group(3)

            set_parts = set_clause.split(",")
            p_idx = 0
            for part in set_parts:
                part = part.strip()
                eq_match = re.match(r"(\w+)\s*=\s*(.+)", part)
                if eq_match:
                    col = eq_match.group(1)
                    expr = eq_match.group(2).strip()
                    if "?" in expr:
                        result["sets"][col] = (expr, p[p_idx])
                        p_idx += 1
                    else:
                        result["sets"][col] = (expr, None)

            conds = self._parse_where_clause(where_clause)
            for _col, _op, val, _raw in conds:
                if val == "?":
                    result["filters"].append((self._map_op(_op), _col, p[p_idx]))
                    p_idx += 1
                elif val.startswith("'") and val.endswith("'"):
                    result["filters"].append((self._map_op(_op), _col, val.strip("'")))
                elif val not in ("?",):
                    result["filters"].append((self._map_op(_op), _col, val))
                    p_idx += 1

        return result

    def _parse_delete(self, q, p):
        result = {"op": "DELETE", "table": None, "filters": [], "params": p}
        m = re.match(r"DELETE\s+FROM\s+(\w+)\s+WHERE\s+(.+)", q, re.I)
        if m:
            result["table"] = m.group(1)
            conds = self._parse_where_clause(m.group(2))
            p_idx = 0
            for _col, _op, val, _raw in conds:
                if val == "?":
                    result["filters"].append((self._map_op(_op), _col, p[p_idx]))
                    p_idx += 1
        return result

    def _parse_sql(self, q, p):
        q_upper = q.upper()
        if q_upper.startswith("SELECT"):
            return self._parse_select(q, p)
        elif q_upper.startswith("INSERT"):
            return self._parse_insert(q, p)
        elif q_upper.startswith("UPDATE"):
            return self._parse_update(q, p)
        elif q_upper.startswith("DELETE"):
            return self._parse_delete(q, p)
        raise ValueError(f"Unable to parse SQL: {q}")

    async def _exec_parsed(self, parsed):
        op, table = parsed["op"], parsed.get("table")
        p = parsed.get("params", ())

        filters = parsed.get("filters", [])
        cols = parsed.get("columns", "*")

        if op == "SELECT":
            return await self._handle_select(parsed)
        elif op == "INSERT":
            return await self._handle_insert(parsed)
        elif op == "UPSERT":
            return await self._handle_upsert(parsed)
        elif op == "UPDATE":
            return await self._handle_update(parsed)
        elif op == "DELETE":
            return await self._handle_delete(parsed)
        raise RuntimeError(f"SQL no soportado: {parsed.get('raw_q', parsed)}")

    async def _handle_select(self, parsed):
        table = parsed["table"]
        cols = parsed["columns"]
        filters = parsed.get("filters", [])
        order_by = parsed.get("order_by")
        desc = parsed.get("desc", False)
        limit = parsed.get("limit")
        group_by = parsed.get("group_by")
        aggregate = parsed.get("aggregate")
        join = parsed.get("join_table")

        if join:
            acct_col = parsed["join_right"][1] if parsed["join_right"][0] == join else parsed["join_left"][1]
            txs = await self._select_rows(table, columns="*", filters=filters, order_by=order_by, desc=desc, limit=limit)
            if not txs:
                return SupabaseCursor([])
            arows = await self._select_rows(join, columns="id,name", filters=[("in", "id", list({t[acct_col] for t in txs}))])
            amap = {a["id"]: a["name"] for a in arows}
            result_rows = []
            for t in txs:
                d = dict(t)
                d["aname"] = amap.get(t.get(acct_col), "-")
                result_rows.append(d)
            return SupabaseCursor(result_rows)

        if group_by:
            rows = await self._select_rows(table, columns="*", filters=filters, order_by=order_by, desc=desc, limit=limit)
            grouped = {}
            for r in rows:
                g = r.get(group_by, "")
                grouped[g] = grouped.get(g, 0) + (r.get("amount") or 0)
            agg_name = aggregate[2] if aggregate else "total"
            return SupabaseCursor([{group_by: c, agg_name: t} for c, t in grouped.items()])

        if aggregate:
            func, agg_col, alias = aggregate
            rows = await self._select_rows(table, columns=agg_col, filters=filters, order_by=order_by, desc=desc, limit=limit)
            if func == "SUM":
                total = sum(r[agg_col] for r in rows) if rows else None
                return SupabaseCursor([{alias: total}])
            elif func == "COUNT":
                return SupabaseCursor([{alias: len(rows)}])

        select_cols = "*"
        if isinstance(cols, list) and cols != ["*"]:
            select_cols = ",".join(cols)

        rows = await self._select_rows(table, columns=select_cols, filters=filters, order_by=order_by, desc=desc, limit=limit)
        return SupabaseCursor(rows)

    async def _handle_insert(self, parsed):
        table = parsed["table"]
        columns = parsed["columns"]
        values = parsed.get("values", [])
        payload = dict(zip(columns, values))

        if table == "accounts":
            dup = await self._select_rows(table, columns="id", filters=[("eq", "user_id", payload.get("user_id")), ("eq", "name", payload.get("name"))], limit=1)
            if dup:
                raise DBIntegrityError("Cuenta duplicada para el usuario")

        await self._insert_row(table, payload)
        return SupabaseCursor()

    async def _handle_upsert(self, parsed):
        table = parsed["table"]
        columns = parsed["columns"]
        values = parsed.get("values", [])
        payload = dict(zip(columns, values))

        if table == "session_states":
            on_conflict = "telegram_id"
        elif table == "low_balance_alerts":
            on_conflict = "telegram_id,account_id"
        elif table == "roundup_config":
            on_conflict = "user_id"
        elif table == "budgets":
            on_conflict = "user_id,category,month"
        else:
            on_conflict = "id"

        await self._upsert_row(table, payload, on_conflict=on_conflict)
        return SupabaseCursor()

    async def _handle_update(self, parsed):
        table = parsed["table"]
        sets = parsed.get("sets", {})
        filters = parsed.get("filters", [])

        if table == "accounts":
            for col, (expr, val) in sets.items():
                if col == "balance" and "+" in expr:
                    for _op, fcol, fval in filters:
                        if fcol == "id":
                            await self._apply_account_balance_delta(fval, val)
                            return SupabaseCursor()
                elif col == "balance" and "-" in expr:
                    for _op, fcol, fval in filters:
                        if fcol == "id":
                            await self._apply_account_balance_delta(fval, -val)
                            return SupabaseCursor()

        if table == "savings_goals":
            for col, (expr, val) in sets.items():
                if col == "current_amount" and "+" in expr:
                    uid_val = None
                    gid_val = None
                    for _op, fcol, fval in filters:
                        if fcol == "id":
                            gid_val = fval
                        if fcol == "user_id":
                            uid_val = fval
                    if uid_val is not None and gid_val is not None:
                        await self._apply_goal_amount_delta(gid_val, uid_val, val)
                        return SupabaseCursor()

        payload = {}
        for col, (expr, val) in sets.items():
            if "?" in expr and not expr.startswith("?"):
                payload[col] = val if val is not None else expr
            elif val is not None:
                payload[col] = val
            else:
                payload[col] = expr

        await self._update_rows(table, payload, filters)
        return SupabaseCursor()

    async def _handle_delete(self, parsed):
        table = parsed["table"]
        filters = parsed.get("filters", [])
        await self._delete_rows(table, filters)
        return SupabaseCursor()

    async def execute(self, sql, params=()):
        q = _norm_sql(sql)
        p = tuple(params or ())

        if q in ("BEGIN", "ROLLBACK"):
            return SupabaseCursor()
        if q.startswith("ALTER TABLE "):
            return SupabaseCursor()

        try:
            parsed = self._parse_sql(q, p)
        except Exception:
            raise RuntimeError(f"SQL no soportado por backend Supabase: {q}")
        return await self._exec_parsed(parsed)

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
