try:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
except Exception:
    class _FallbackToken:
        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    class _FallbackHandler:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _FallbackFilters:
        TEXT = _FallbackToken()
        COMMAND = _FallbackToken()

    CallbackQueryHandler = CommandHandler = MessageHandler = _FallbackHandler
    filters = _FallbackFilters()


def register_handlers(application, handlers):
    application.add_handler(CommandHandler("start", handlers["cmd_start"]))
    application.add_handler(CommandHandler("help", handlers["cmd_help"]))
    application.add_handler(CommandHandler("menu", handlers["cmd_menu"]))
    application.add_handler(CommandHandler("cancel", handlers["cmd_cancel"]))
    application.add_handler(CommandHandler("cuentas", handlers["cmd_cuentas"]))
    application.add_handler(CommandHandler("nuevacuenta", handlers["cmd_nueva_cuenta"]))
    application.add_handler(CommandHandler("borrarcuenta", handlers["cmd_borrar_cuenta"]))
    application.add_handler(CommandHandler("g", handlers["cmd_gasto_rapido"]))
    application.add_handler(CommandHandler("gasto", handlers["cmd_gasto"]))
    application.add_handler(CommandHandler("ingreso", handlers["cmd_ingreso"]))
    application.add_handler(CommandHandler("traspaso", handlers["cmd_traspaso"]))
    application.add_handler(CommandHandler("deshacer", handlers["cmd_deshacer"]))
    application.add_handler(CommandHandler("redondeo", handlers["cmd_redondeo"]))
    application.add_handler(CommandHandler("redondeotoggle", handlers["cmd_redondeo_toggle"]))
    application.add_handler(CommandHandler("redondeocuenta", handlers["cmd_redondeo_cuenta"]))
    application.add_handler(CommandHandler("recurrente", handlers["cmd_recurrente"]))
    application.add_handler(CommandHandler("agregarrecurrente", handlers["cmd_agregar_recurrente"]))
    application.add_handler(CommandHandler("borrarrecurrente", handlers["cmd_borrar_recurrente"]))
    application.add_handler(CommandHandler("resumen", handlers["cmd_resumen"]))
    application.add_handler(CommandHandler("stats", handlers["cmd_stats"]))
    application.add_handler(CommandHandler("tendencia", handlers["cmd_tendencia"]))
    application.add_handler(CommandHandler("panel", handlers["cmd_panel"]))
    application.add_handler(CommandHandler("forecast", handlers["cmd_forecast"]))
    application.add_handler(CommandHandler("anomalias", handlers["cmd_anomalias"]))
    application.add_handler(CommandHandler("tags", handlers["cmd_tags"]))
    application.add_handler(CommandHandler("sugerircategoria", handlers["cmd_sugerircategoria"]))
    application.add_handler(CommandHandler("exportar", handlers["cmd_exportar"]))
    application.add_handler(CommandHandler("alertas", handlers["cmd_alertas"]))
    application.add_handler(CommandHandler("agregaralerta", handlers["cmd_agregar_alerta"]))
    application.add_handler(CommandHandler("borraralerta", handlers["cmd_borrar_alerta"]))
    application.add_handler(CommandHandler("reset", handlers["cmd_reset"]))
    application.add_handler(CommandHandler("patrimonio", handlers["cmd_patrimonio"]))
    application.add_handler(CommandHandler("presupuesto", handlers["cmd_presupuesto"]))
    application.add_handler(CommandHandler("presupuestoset", handlers["cmd_presupuestoset"]))
    application.add_handler(CommandHandler("buscar", handlers["cmd_buscar"]))
    application.add_handler(CommandHandler("metas", handlers["cmd_metas"]))
    application.add_handler(CommandHandler("nuevameta", handlers["cmd_nuevameta"]))
    application.add_handler(CommandHandler("aportarmeta", handlers["cmd_aportarmeta"]))
    application.add_handler(CommandHandler("agregaringresorecurrente", handlers["cmd_agregaringresorecurrente"]))
    application.add_handler(CommandHandler("ingresorecurrente", handlers["cmd_ingresorecurrente"]))
    application.add_handler(CallbackQueryHandler(handlers["handle_menu_callback"], pattern="^menu_.*"))
    application.add_handler(CallbackQueryHandler(handlers["handle_resumen_callback"], pattern="^resumen_.*"))
    application.add_handler(CallbackQueryHandler(handlers["handle_budget_callback"], pattern="^budcat_.*"))
    application.add_handler(
        CallbackQueryHandler(
            handlers["handle_callback"],
            pattern="^(cancel_action|quick_acc_|aportar_goal_|del_account_|del_account_confirm_|xfer_from_|xfer_to_|del_recurring_|del_recurring_confirm_|alert_acc_|del_alert_|del_alert_confirm_|roundup_acc_|reset_confirm|undo_).*",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handlers["handle_flow_callback"],
            pattern="^(type_|cat_|expdate_|exp_acc_|inc_acc_|freq_|rrcat_|rec_acc_|freqinc_|inc_rec_acc_).*",
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["handle_text"]))
