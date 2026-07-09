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
