from datetime import datetime, timedelta

from finance_shared import h
from finance_state import get_accounts, save_session
from finance_ui import _acct_kb


async def check_alerts(db, tid, uid):
    c = await db.execute(
        """SELECT la.*,a.name,a.balance FROM low_balance_alerts la JOIN accounts a ON la.account_id=a.id
           WHERE la.telegram_id=? AND la.enabled=1 AND a.balance<la.threshold""",
        (tid,),
    )
    rows = await c.fetchall()
    alerts = []
    for r in rows:
        bal_str = "{:.2f}".format(r["balance"])
        thr_str = "{:.2f}".format(r["threshold"])
        alerts.append(f"⚠️ <b>ALERTA</b>: {h(r['name'])} esta por debajo del limite (€{h(bal_str)} &lt; €{h(thr_str)})")
    return alerts


async def _check_budget_warning(db, uid, category, update):
    now = datetime.now()
    month = f"{now.year}-{now.month:02d}"
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        now.replace(year=now.year + 1, month=1, day=1) - timedelta(seconds=1)
        if now.month == 12
        else now.replace(month=now.month + 1, day=1) - timedelta(seconds=1)
    )
    b = await (await db.execute("SELECT amount FROM budgets WHERE user_id=? AND category=? AND month=?", (uid, category, month))).fetchone()
    if not b:
        return
    c = await db.execute(
        "SELECT SUM(amount) as total FROM transactions WHERE user_id=? AND type='GASTO' AND category=? AND date>=? AND date<=?",
        (uid, category, start.isoformat(), end.isoformat()),
    )
    row = await c.fetchone()
    spent = row["total"] or 0
    pct = spent / b["amount"] * 100
    if pct >= 90:
        bud_amt = "{:.2f}".format(b["amount"])
        spent_amt = "{:.2f}".format(spent)
        pct_str = "{:.1f}".format(pct)
        await update.effective_message.reply_text(
            f"⚠️ <b>Alerta de presupuesto</b>\n{category}: €{h(spent_amt)}/{h(bud_amt)} ({h(pct_str)}%)",
            parse_mode="HTML",
        )


async def _expense_ask_account(db, tid, uid, sdata, q):
    accts = await get_accounts(db, uid)
    await save_session(db, tid, "waiting_expense_account", sdata)
    exp_amt2 = "{:.2f}".format(sdata.get("expenseAmount", 0))
    await q.edit_message_text(
        f"Gasto: €{h(exp_amt2)} en {h(sdata.get('expenseCategory',''))}\n\nSelecciona la cuenta:",
        reply_markup=_acct_kb(accts, "exp_acc", None),
    )
