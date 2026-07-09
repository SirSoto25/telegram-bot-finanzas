from datetime import datetime, timedelta
import calendar

from finance_shared import MONTHS_ES, end_of_month, h, _extract_tags, _month_shift, _month_window

CATEGORY_EMOJI = {"Comida":"🍕","Transporte":"🚌","Suscripciones":"📺","Coche":"🚗","Entretenimiento":"🎮","Vivienda":"🏠","Utilidades":"💡","Otros":"🏷️"}

async def get_monthly_tx(db,uid,months=6):
    data,now={},datetime.now()
    for i in range(months-1,-1,-1):
        d=now-timedelta(days=30*i); start=d.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        end=end_of_month(d)
        key=f"{MONTHS_ES[start.month]} {start.year}"
        c=await db.execute("SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=? AND type!='TRANSFERENCIA'",(uid,start.isoformat(),end.isoformat()))
        rows=await c.fetchall()
        data[key]={"income":sum(r["amount"] for r in rows if r["type"]=="INGRESO"),"expense":sum(r["amount"] for r in rows if r["type"]=="GASTO")}
    return data

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
        end=end_of_month(d)
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
    accts = await db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY created_at",(uid,))
    accts = await accts.fetchall()
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


async def get_net_worth_history(db, uid, months=12):
    """Calculate net worth at the end of each month for the last N months."""
    from finance_shared import end_of_month
    now = datetime.now()
    result = []
    for i in range(months - 1, -1, -1):
        d = now - timedelta(days=30 * i)
        month_end = end_of_month(d)
        month_label = f"{MONTHS_ES[d.month]} {d.year}"
        c = await db.execute(
            "SELECT type,amount FROM transactions WHERE user_id=? AND date<=?",
            (uid, month_end.isoformat()),
        )
        rows = await c.fetchall()
        income = sum(r["amount"] for r in rows if r["type"] == "INGRESO")
        expense = sum(r["amount"] for r in rows if r["type"] == "GASTO")
        transfers_out = sum(r["amount"] for r in rows if r["type"] == "TRANSFERENCIA")
        net = income - expense - transfers_out
        result.append((month_label, net, income, expense))
    return result


async def get_yoy_comparison(db, uid, month_offset=0):
    """Compare current month vs same month last year."""
    now = datetime.now()
    target = now - timedelta(days=30 * month_offset)
    current_start = target.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_end = end_of_month(target)
    prev_year = target.replace(year=target.year - 1)
    prev_start = prev_year.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_end = end_of_month(prev_year)

    c1 = await db.execute(
        "SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?",
        (uid, current_start.isoformat(), current_end.isoformat()),
    )
    c2 = await db.execute(
        "SELECT category,amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?",
        (uid, prev_start.isoformat(), prev_end.isoformat()),
    )
    cur_rows = await c1.fetchall()
    prev_rows = await c2.fetchall()

    cur = {r["category"]: r["amount"] for r in cur_rows}
    prev = {r["category"]: r["amount"] for r in prev_rows}
    all_cats = sorted(set(list(cur.keys()) + list(prev.keys())))
    rows = []
    for cat in all_cats:
        cur_val = cur.get(cat, 0)
        prev_val = prev.get(cat, 0)
        if prev_val > 0:
            pct = (cur_val - prev_val) / prev_val * 100
        else:
            pct = 100 if cur_val > 0 else 0
        arrow = "⬆️" if pct > 10 else ("⬇️" if pct < -10 else "➡️")
        rows.append((cat, f"€{cur_val:.2f}", f"€{prev_val:.2f}", f"{arrow} {pct:+.0f}%"))
    cur_total = sum(cur.values())
    prev_total = sum(prev.values())
    total_pct = ((cur_total - prev_total) / prev_total * 100) if prev_total > 0 else 0
    return {
        "current_month": f"{MONTHS_ES[target.month]} {target.year}",
        "prev_month": f"{MONTHS_ES[prev_year.month]} {prev_year.year}",
        "rows": rows,
        "cur_total": cur_total,
        "prev_total": prev_total,
        "total_pct": total_pct,
    }


async def get_burn_rate(db, uid):
    """Estimate days until balance reaches zero at current spending rate."""
    from finance_shared import end_of_month
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_elapsed = now.day
    c = await db.execute(
        "SELECT amount FROM transactions WHERE user_id=? AND type='GASTO' AND date>=? AND date<=?",
        (uid, month_start.isoformat(), now.isoformat()),
    )
    rows = await c.fetchall()
    total_spent = sum(r["amount"] for r in rows)
    daily_avg = total_spent / max(days_elapsed, 1)

    c2 = await db.execute("SELECT balance FROM accounts WHERE user_id=?", (uid,))
    accounts = await c2.fetchall()
    total_balance = sum(a["balance"] for a in accounts)

    days_left = int(total_balance / daily_avg) if daily_avg > 0 else None
    month_projection = daily_avg * days_in_month
    return {
        "daily_avg": daily_avg,
        "total_balance": total_balance,
        "days_left": days_left,
        "month_projection": month_projection,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
    }


async def get_savings_rate(db, uid, months=6):
    """Calculate monthly savings rate as percentage of income."""
    from finance_shared import end_of_month
    now = datetime.now()
    result = []
    for i in range(months - 1, -1, -1):
        d = now - timedelta(days=30 * i)
        start = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = end_of_month(d)
        c = await db.execute(
            "SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=?",
            (uid, start.isoformat(), end.isoformat()),
        )
        rows = await c.fetchall()
        income = sum(r["amount"] for r in rows if r["type"] == "INGRESO")
        expense = sum(r["amount"] for r in rows if r["type"] == "GASTO")
        transfers = sum(r["amount"] for r in rows if r["type"] == "TRANSFERENCIA")
        net = income - expense - transfers
        rate = (net / income * 100) if income > 0 else 0
        result.append((f"{MONTHS_ES[d.month]}", rate, income, expense + transfers))
    avg_rate = sum(r[1] for r in result) / len(result) if result else 0
    return result, avg_rate


def get_advice(history, burn_data, yoy_comp, savings_rate):
    """Generate personalized financial tips based on data analysis."""
    tips = []
    # Check savings rate
    if savings_rate < 0:
        tips.append("🔴 Estás gastando más de lo que ingresas. Revisa tus gastos variables.")
    elif savings_rate < 10:
        tips.append("🟡 Tu tasa de ahorro es baja (<10%). Intenta reducir gastos en la categoría más alta.")
    elif savings_rate > 30:
        tips.append("🟢 ¡Excelente tasa de ahorro! Considera invertir el excedente.")

    # Check YoY comparison
    if yoy_comp and yoy_comp["total_pct"] > 20:
        tips.append(f"⬆️ Tus gastos subieron un {yoy_comp['total_pct']:.0f}% vs el año pasado. Revisa si hay nuevos gastos fijos.")

    # Burn rate advice
    if burn_data["days_left"] is not None and burn_data["days_left"] < 30:
        tips.append(f"⚠️ Al ritmo actual, tu saldo dura solo {burn_data['days_left']} días. Reduce gastos o busca ingresos extra.")

    # Anomaly-based advice
    if history:
        last_income = sum(h[2] for h in history[-1:])
        last_expense = sum(h[3] for h in history[-1:])
        if last_income > 0 and last_expense > last_income * 0.9:
            tips.append("⚡ Tus gastos están al 90% de tus ingresos este mes. Cuidado con los imprevistos.")

    if not tips:
        tips.append("✅ Tus finanzas están equilibradas. Sigue así y mantén el hábito de registrar tus gastos.")
    return tips


_50_30_20_CATEGORIES = {
    "Comida": "necesidad", "Transporte": "necesidad", "Vivienda": "necesidad",
    "Utilidades": "necesidad", "Coche": "necesidad",
    "Suscripciones": "deseo", "Entretenimiento": "deseo", "Otros": "deseo",
}


async def get_50_30_20(db, uid):
    """Calculate spending breakdown against the 50/30/20 rule."""
    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    c = await db.execute(
        "SELECT type,amount,category FROM transactions WHERE user_id=? AND date>=? AND date<=?",
        (uid, start.isoformat(), now.isoformat()),
    )
    rows = await c.fetchall()
    income = sum(r["amount"] for r in rows if r["type"] == "INGRESO")
    necesidades = sum(r["amount"] for r in rows if r["type"] == "GASTO" and _50_30_20_CATEGORIES.get(r["category"]) == "necesidad")
    deseos = sum(r["amount"] for r in rows if r["type"] == "GASTO" and _50_30_20_CATEGORIES.get(r["category"]) == "deseo")
    transfers = sum(r["amount"] for r in rows if r["type"] == "TRANSFERENCIA")
    ahorro = income - necesidades - deseos - transfers
    return {
        "income": income,
        "necesidades": necesidades,
        "deseos": deseos,
        "ahorro": ahorro,
        "ideal_n": income * 0.5,
        "ideal_d": income * 0.3,
        "ideal_a": income * 0.2,
    }


async def get_goal_projections(db, uid):
    """Calculate time to reach each savings goal based on average monthly savings."""
    goals = await db._select_rows("savings_goals", filters=[("eq", "user_id", uid)])
    if not goals:
        return []
    now = datetime.now()
    start = now.replace(year=now.year - 1, month=now.month, day=1)
    c = await db.execute(
        "SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND type IN ('INGRESO','GASTO','TRANSFERENCIA')",
        (uid, start.isoformat()),
    )
    rows = await c.fetchall()
    income = sum(r["amount"] for r in rows if r["type"] == "INGRESO")
    expense = sum(r["amount"] for r in rows if r["type"] == "GASTO")
    transfers = sum(r["amount"] for r in rows if r["type"] == "TRANSFERENCIA")
    months = max(((now - start).days / 30), 1)
    monthly_saving = (income - expense - transfers) / months

    projections = []
    for g in goals:
        remaining = g["target_amount"] - (g.get("current_amount") or 0)
        if remaining <= 0:
            months_left = 0
            eta = "¡Conseguida!"
        elif monthly_saving > 0:
            months_left = int(remaining / monthly_saving)
            eta_date = now + timedelta(days=months_left * 30)
            eta = f"{MONTHS_ES[eta_date.month]} {eta_date.year}"
        else:
            months_left = None
            eta = "∞ (sin ahorro positivo)"
        projections.append({
            "name": g["name"],
            "target": g["target_amount"],
            "current": g.get("current_amount") or 0,
            "remaining": remaining,
            "months_left": months_left,
            "eta": eta,
            "deadline": g.get("deadline"),
        })
    return projections


async def get_phantom_expenses(db, uid):
    """Detect potential duplicate/unnecessary spending."""
    now = datetime.now()
    start = now.replace(day=1) - timedelta(days=60)
    c = await db.execute(
        "SELECT amount,description,category FROM transactions WHERE user_id=? AND type='GASTO' AND date>=?",
        (uid, start.isoformat()),
    )
    rows = await c.fetchall()
    phantoms = []
    seen = {}
    for r in rows:
        desc = (r["description"] or "").strip().lower()
        if not desc:
            continue
        key = (desc, r["category"])
        if key in seen:
            seen[key]["count"] += 1
            seen[key]["total"] += r["amount"]
        else:
            seen[key] = {"desc": desc, "cat": r["category"], "count": 1, "total": r["amount"]}
    for (desc, cat), data in seen.items():
        if data["count"] >= 2:
            phantoms.append((data["desc"], data["cat"], data["count"], data["total"]))
    phantoms.sort(key=lambda x: x[3], reverse=True)
    return phantoms[:10]


async def check_and_award_achievements(db, uid, tid=None):
    """Check for newly unlocked achievements and return them."""
    now = datetime.now()
    new_achievements = []

    defs = [
        {"key": "first_expense", "msg": "🏁 Primer gasto registrado"},
        {"key": "first_account", "msg": "🏦 Primera cuenta creada"},
        {"key": "first_goal", "msg": "🎯 Primera meta creada"},
        {"key": "transactions_100", "msg": "📊 100 transacciones"},
        {"key": "full_month", "msg": "📅 1 mes completo trackeado"},
        {"key": "streak_3", "msg": "🔥 3 meses de racha de ahorro"},
        {"key": "big_saver", "msg": "💎 Ahorro >20% del ingreso mensual"},
    ]

    for d in defs:
        existing = await db._select_rows("achievements", filters=[("eq", "user_id", uid), ("eq", "achievement_key", d["key"])], limit=1)
        if existing:
            continue
        unlocked = False
        if d["key"] == "first_expense":
            rows = await db._select_rows("transactions", columns="id", filters=[("eq", "user_id", uid), ("eq", "type", "GASTO")], limit=1)
            unlocked = bool(rows)
        elif d["key"] == "first_account":
            rows = await db._select_rows("accounts", columns="id", filters=[("eq", "user_id", uid)], limit=1)
            unlocked = bool(rows)
        elif d["key"] == "first_goal":
            rows = await db._select_rows("savings_goals", columns="id", filters=[("eq", "user_id", uid)], limit=1)
            unlocked = bool(rows)
        elif d["key"] == "transactions_100":
            rows = await db._select_rows("transactions", columns="id", filters=[("eq", "user_id", uid)])
            unlocked = len(rows) >= 100
        elif d["key"] == "full_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(day=28) + __import__("datetime").timedelta(days=4)
            rows = await db._select_rows("transactions", columns="id", filters=[("eq", "user_id", uid), ("gte", "date", start.isoformat())], limit=1)
            unlocked = bool(rows) and now.day >= 28
        elif d["key"] == "streak_3":
            srows = await db._select_rows("streaks", columns="current_streak", filters=[("eq", "user_id", uid)], limit=1)
            unlocked = bool(srows) and srows[0].get("current_streak", 0) >= 3

        if unlocked:
            await db._upsert_row("achievements", {"user_id": uid, "achievement_key": d["key"]}, on_conflict="user_id,achievement_key")
            new_achievements.append(d)

    return new_achievements


async def get_streak(db, uid):
    rows = await db._select_rows("streaks", filters=[("eq", "user_id", uid)], limit=1)
    return rows[0] if rows else None


async def update_streak(db, uid):
    """Update streak after checking if this month had positive savings."""
    from finance_shared import end_of_month
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = end_of_month(now)

    c = await db.execute("SELECT type,amount FROM transactions WHERE user_id=? AND date>=? AND date<=?", (uid, month_start.isoformat(), month_end.isoformat()))
    rows = await c.fetchall()
    income = sum(r["amount"] for r in rows if r["type"] == "INGRESO")
    expense = sum(r["amount"] for r in rows if r["type"] == "GASTO")

    current = await get_streak(db, uid)
    cur_val = current["current_streak"] if current else 0
    best_val = current["best_streak"] if current else 0

    if income > expense:
        cur_val += 1
        best_val = max(best_val, cur_val)
    else:
        cur_val = 0

    if current:
        await db.execute("UPDATE streaks SET current_streak=?, best_streak=?, last_updated=? WHERE user_id=?", (cur_val, best_val, now.isoformat(), uid))
    else:
        await db.execute("INSERT OR REPLACE INTO streaks(user_id,current_streak,best_streak,last_updated) VALUES(?,?,?,?)", (uid, cur_val, best_val, now.isoformat()))
    await db.commit()

    return {"current": cur_val, "best": best_val, "income": income, "expense": expense}
