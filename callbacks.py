"""Callback query handlers for the finance bot."""
import json
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from _env import get_db
from finance_shared import (
    ACCOUNT_TYPE_MAP, CATEGORY_KBD_ITEMS, CATEGORY_MAP, FREQ_KBD_ITEMS,
    FREQ_MAP, MONTHS_ES, TYPE_KBD_ITEMS,
    _cb_suffix_int, _cb_suffix_text, _smart_category_suggestion,
    h, parse_amount,
)
from finance_db import _tx_wrap
from finance_state import (
    _check_session_expiry, clear_session, get_accounts, get_or_create_user,
    get_roundup, get_session, save_session,
)
from finance_ui import _acct_kb, _confirm_kb, _kb, multi_kb
from finance_analytics import unicode_table
from commands import _finalize_quick_expense


async def handle_menu_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id
    await clear_session(db,tid)
    d=q.data.replace("menu_","")
    cmd_map={
        "gasto":cmd_gasto,"ingreso":cmd_ingreso,"traspaso":cmd_traspaso,"deshacer":cmd_deshacer,
        "resumen":finance_reports.cmd_resumen,"stats":finance_reports.cmd_stats,"recurrente":cmd_recurrente,"alertas":cmd_alertas,
        "cuentas":cmd_cuentas,"redondeo":cmd_redondeo,"exportar":finance_reports.cmd_exportar,"help":cmd_help,
    }
    handler=cmd_map.get(d)
    if handler:
        await q.edit_message_text("⏳ Cargando...")
        await handler(update,ctx)
    else:
        await q.edit_message_text("Opcion no disponible.")

async def handle_resumen_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id
    s=await get_session(db,tid)
    if not s or s["state"]!="resumen_data": return await q.edit_message_text("Sesion expirada.",parse_mode=ParseMode.HTML)
    try:
        sdata=json.loads(s["data"]) if s["data"] else {}
    except (json.JSONDecodeError, TypeError):
        return await q.edit_message_text("⚠️ Datos de sesion corruptos.", parse_mode=ParseMode.HTML)
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

async def handle_budget_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    s=await get_session(db,tid)
    if not s: return await q.edit_message_text("Sesion expirada.",parse_mode=ParseMode.HTML)
    try:
        sdata=json.loads(s["data"]) if s["data"] else {}
    except (json.JSONDecodeError, TypeError):
        return await q.edit_message_text("⚠️ Datos de sesion corruptos.", parse_mode=ParseMode.HTML)
    d=q.data; state=s["state"]

    if state=="waiting_budget_category" and d.startswith("budcat_"):
        key = _cb_suffix_text(d, "budcat_")
        if key not in CATEGORY_MAP: return await q.edit_message_text("Opcion invalida.")
        sdata["budgetCategory"]=key
        await save_session(db,tid,"waiting_budget_amount",sdata)
        await q.edit_message_text(f"Categoria: {h(CATEGORY_MAP[key])}\n\n¿Cual es el limite mensual?\n(Formato: cantidad)\n\n/cancel para cancelar")


async def handle_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    if await _check_session_expiry(db,tid):
        return await q.edit_message_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    d=q.data
    if d=="cancel_action": await clear_session(db,tid); return await q.edit_message_text("✅ Operacion cancelada.")

    if d.startswith("quick_acc_"):
        aid=_cb_suffix_int(d,"quick_acc_")
        if aid is None: return await q.edit_message_text("❌ Opcion invalida.")
        s=await get_session(db,tid)
        sdata=json.loads(s["data"]) if s and s["data"] else {}
        await _finalize_quick_expense(db,tid,uid,sdata,aid,update)
        return

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
        try:
            sdata=json.loads(s["data"]) if s else {}
        except (json.JSONDecodeError, TypeError):
            return await q.edit_message_text("⚠️ Datos de sesion corruptos.", parse_mode=ParseMode.HTML)
        fid=sdata.get("from_id")
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

    elif d.startswith("delbill_"):
        bid=_cb_suffix_int(d,"delbill_")
        if bid is None: return await q.edit_message_text("❌ Opcion invalida.")
        await db.execute("DELETE FROM bill_reminders WHERE id=? AND user_id=?",(bid,uid))
        await db.commit()
        await q.edit_message_text("✅ Recordatorio de factura eliminado.")

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

async def _finalize_expense_with_note(db, tid, uid, sdata, note, update):
    aid = sdata["expenseAccountId"]
    amt = sdata["expenseAmount"]
    cat = sdata["expenseCategory"]
    exp_date = sdata.get("expenseDate", datetime.now().isoformat())
    acc = await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
    if not acc:
        await update.effective_message.reply_text("Cuenta no encontrada.")
        return
    if acc["balance"] < amt:
        await update.effective_message.reply_text("❌ Saldo insuficiente en esta cuenta.")
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
            await update.effective_message.reply_text(f"✅ Gasto registrado\n💸 €{h(f'{amt:.2f}')}\n📌 {h(cat)}\n💼 {h(acc['name'])}{label}\n\n🪙 Redondeo: +€{h(f'{diff:.2f}')} → {h(da['name'])}", parse_mode=ParseMode.HTML)
            await _check_budget_warning(db, uid, cat, update)
            return
    await _tx_wrap(db, ops)
    await clear_session(db, tid)
    label = f"\n🏷️ {h(note)}" if note else ""
    await update.effective_message.reply_text(f"✅ Gasto registrado\n💸 €{h(f'{amt:.2f}')}\n📌 {h(cat)}\n💼 {h(acc['name'])}{label}", parse_mode=ParseMode.HTML)
    await _check_budget_warning(db, uid, cat, update)

async def _finalize_income_with_note(db, tid, uid, sdata, note, update):
    aid = sdata["incomeAccountId"]
    amt = sdata["incomeAmount"]
    conc = sdata["incomeConcept"]
    acc = await (await db.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",(aid,uid))).fetchone()
    if not acc:
        await update.effective_message.reply_text("Cuenta no encontrada.")
        return
    await _tx_wrap(db,[
        ("INSERT INTO transactions(user_id,account_id,amount,type,category,description) VALUES(?,?,?,'INGRESO',?,?)",(uid,aid,amt,conc,note or "")),
        ("UPDATE accounts SET balance=balance+? WHERE id=?",(amt,aid))
    ])
    await clear_session(db, tid)
    label = f"\n🏷️ {h(note)}" if note else ""
    await update.effective_message.reply_text(f"✅ Ingreso registrado\n💰 €{h(f'{amt:.2f}')}\n📝 {h(conc)}\n💼 {h(acc['name'])}{label}", parse_mode=ParseMode.HTML)

async def handle_flow_callback(update,ctx):
    q=update.callback_query; await _safe_answer_callback(q)
    db=await get_db(); tid=update.effective_user.id; uid=await get_or_create_user(db,tid)
    if await _check_session_expiry(db,tid):
        return await q.edit_message_text("⏰ Sesion expirada. Usa /start para comenzar de nuevo.")
    s=await get_session(db,tid)
    if not s: return await q.edit_message_text("Sesion expirada. Usa /start.", parse_mode=ParseMode.HTML)
    try:
        sdata=json.loads(s["data"]) if s["data"] else {}
    except (json.JSONDecodeError, TypeError):
        return await q.edit_message_text("⚠️ Datos de sesion corruptos.", parse_mode=ParseMode.HTML)
    d=q.data; state=s["state"]
    entry=_FLOW_CALLBACK_MAP.get(state)
    if entry:
        prefix, handler = entry
        if d.startswith(prefix):
            return await handler(db,tid,uid,sdata,d,q,update,ctx)


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


