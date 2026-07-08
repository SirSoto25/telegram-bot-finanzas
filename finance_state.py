import json
from datetime import datetime

from finance_shared import SYSTEM_BOT_TELEGRAM_ID, SESSION_TIMEOUT_MINUTES, session_is_expired


async def get_or_create_user(db, tid):
    c = await db.execute("SELECT id FROM users WHERE telegram_id=?", (tid,))
    u = await c.fetchone()
    if not u:
        c = await db.execute("INSERT INTO users(telegram_id) VALUES(?)", (tid,))
        await db.commit()
        return c.lastrowid
    return u["id"]


async def get_session(db, tid):
    c = await db.execute("SELECT state,data,created_at FROM session_states WHERE telegram_id=?", (tid,))
    return await c.fetchone()


async def save_session(db, tid, state, data=None):
    await db.execute(
        "INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)",
        (tid, state, json.dumps(data or {}), datetime.now().isoformat()),
    )
    await db.commit()


async def clear_session(db, tid):
    await db.execute("DELETE FROM session_states WHERE telegram_id=?", (tid,))
    await db.commit()


async def get_system_state(db):
    c = await db.execute("SELECT state,data,created_at FROM session_states WHERE telegram_id=?", (SYSTEM_BOT_TELEGRAM_ID,))
    return await c.fetchone()


async def save_system_state(db, state, data=None):
    await db.execute(
        "INSERT OR REPLACE INTO session_states(telegram_id,state,data,created_at) VALUES(?,?,?,?)",
        (SYSTEM_BOT_TELEGRAM_ID, state, json.dumps(data or {}), datetime.now().isoformat()),
    )
    await db.commit()


async def _check_session_expiry(db, tid):
    s = await get_session(db, tid)
    if s and s["created_at"]:
        created = datetime.fromisoformat(s["created_at"])
        if session_is_expired(created, SESSION_TIMEOUT_MINUTES):
            await clear_session(db, tid)
            return True
    return False


async def get_accounts(db, uid):
    c = await db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY created_at", (uid,))
    return await c.fetchall()


async def get_roundup(db, uid):
    c = await db.execute("SELECT * FROM roundup_config WHERE user_id=?", (uid,))
    return await c.fetchone()
