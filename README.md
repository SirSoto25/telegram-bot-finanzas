# Telegram Finance Bot

Bot de Telegram para gestión de finanzas personales con Supabase + Flask WSGI, diseñado para desplegar en PythonAnywhere.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![python-telegram-bot](https://img.shields.io/badge/PTB-21+-blue.svg)](https://python-telegram-bot.org)
[![Supabase](https://img.shields.io/badge/Supabase-backend-green.svg)](https://supabase.com)

## Características

- **Cuentas**: múltiples cuentas (nómina, ahorros, inversión, cripto) con saldo individual
- **Gastos/Ingresos**: registro con categorías, fechas personalizadas, notas, tags y comando rápido `/g`
- **Transferencias**: mover dinero entre cuentas propias
- **Recurrentes**: gastos e ingresos automáticos programados (semanal/mensual/trimestral/anual)
- **Redondeo**: ahorro automático redondeando cada gasto al euro superior
- **Deshacer**: revertir los últimos 10 movimientos
- **Presupuestos**: límites mensuales por categoría con alertas de progreso
- **Metas de ahorro**: objetivos con fecha límite, aportes y seguimiento
- **Alertas**: notificaciones cuando el saldo de una cuenta baja de un umbral
- **Reportes**: resumen, estadísticas 6m, tendencias 12m, panel financiero, patrimonio, comparativa interanual, burn rate, tasa de ahorro
- **Coaching**: consejos personalizados, visualización 50/30/20, proyección de metas, detector de gastos duplicados
- **Facturas**: recordatorios mensuales de facturas (luz, internet, alquiler...)
- **Gamificación**: racha de ahorro, logros (7 disponibles), resumen anual
- **Gastos compartidos**: `/split` para dividir gastos entre usuarios, `/deudas` para seguimiento
- **Exportación**: CSV y gráficos ASCII en el chat
- **Jobs**: recordatorios recurrentes, panel semanal, resumen diario, facturas del día

## Comandos

### Cuentas
| Comando | Descripción |
|---------|-------------|
| `/cuentas` | Lista cuentas con saldos y total consolidado |
| `/nuevacuenta` | Crear nueva cuenta |
| `/borrarcuenta` | Eliminar cuenta y sus movimientos |

### Gastos e ingresos
| Comando | Descripción |
|---------|-------------|
| `/g <cantidad> [desc]` | Gasto rápido con categoría automática |
| `/gasto` | Registrar un gasto (flujo guiado) |
| `/ingreso` | Registrar un ingreso |
| `/traspaso` | Transferir entre cuentas |
| `/deshacer` | Revertir último movimiento |
| `/buscar <texto>` | Buscar en descripciones |

### Reportes
| Comando | Descripción |
|---------|-------------|
| `/resumen` | Resumen del mes actual con desglose por categoría |
| `/stats` | Estadísticas últimos 6 meses |
| `/tendencia` | Gráficos de tendencia 12 meses |
| `/panel` | Panel financiero completo + anomalías |
| `/forecast` | Proyección de saldo a fin de mes |
| `/anomalias` | Detección de gastos anómalos |
| `/tags` | Etiquetas usadas en notas |
| `/exportar` | Descargar CSV de transacciones |
| `/patrimonio` | Evolución del patrimonio neto 12 meses |
| `/comparar` | Comparativa interanual por categoría |
| `/burnrate` | Días hasta agotar saldo al ritmo actual |
| `/ahorro` | Tasa de ahorro mensual 6 meses |

### Coaching financiero
| Comando | Descripción |
|---------|-------------|
| `/consejo` | Consejos personalizados según tus datos |
| `/regla` | Visualización de la regla 50/30/20 |
| `/proyeccion` | Cuándo alcanzarás cada meta de ahorro |
| `/fantasmas` | Detector de gastos duplicados o frecuentes |

### Presupuestos y metas
| Comando | Descripción |
|---------|-------------|
| `/presupuesto` | Ver presupuestos con barras de progreso |
| `/presupuestoset` | Crear/modificar presupuesto |
| `/metas` | Ver metas de ahorro |
| `/nuevameta` | Crear nueva meta |
| `/aportarmeta` | Aportar a una meta |

### Alertas, facturas y recurrentes
| Comando | Descripción |
|---------|-------------|
| `/alertas` | Ver alertas de saldo bajo |
| `/agregaralerta` | Crear alerta |
| `/borraralerta` | Eliminar alerta |
| `/recurrente` | Ver gastos recurrentes |
| `/agregarrecurrente` | Agregar gasto recurrente |
| `/borrarrecurrente` | Eliminar recurrente |
| `/ingresorecurrente` | Ver ingresos recurrentes |
| `/agregaringresorecurrente` | Agregar ingreso recurrente |
| `/factura` | Crear recordatorio de factura mensual |
| `/facturas` | Listar recordatorios de facturas |
| `/borrarfactura` | Eliminar recordatorio de factura |

### Gamificación
| Comando | Descripción |
|---------|-------------|
| `/racha` | Racha de meses consecutivos ahorrando |
| `/logros` | Logros desbloqueados (7 disponibles) |
| `/resumenanual` | Resumen anual con top categorías |

### Gastos compartidos
| Comando | Descripción |
|---------|-------------|
| `/split <cantidad> @u1 @u2` | Dividir gasto entre varias personas |
| `/deudas` | Ver quién te debe y a quién le debes |

### Configuración
| Comando | Descripción |
|---------|-------------|
| `/start` | Iniciar el bot |
| `/help [comando]` | Mostrar todos los comandos o ayuda detallada |
| `/menu` | Panel con botones interactivos |
| `/cancel` | Cancelar operación en curso |
| `/resumendiario [on\|off]` | Activar/desactivar resumen diario a las 8am |
| `/redondeo` | Ver estado del redondeo automático |
| `/redondeotoggle` | Activar/desactivar redondeo |
| `/redondeocuenta` | Cambiar cuenta destino del redondeo |
| `/sugerircategoria` | Sugerir categoría de un texto |
| `/reset` | Borrar todos los datos |

## Instalación

### Requisitos

```bash
pip install -r requirements.txt
```

### Variables de entorno

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export SUPABASE_URL="https://xxx.supabase.co"
export SUPABASE_KEY="eyJ..."        # service_role key
export TELEGRAM_WEBHOOK_SECRET=""   # opcional, secreto para webhook
```

### Desarrollo local

```bash
# Instalar dependencias
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Ejecutar tests
python -m pytest tests/ -v

# El bot usa Flask WSGI — para desarrollo local usa ngrok o similar
# con el webhook de Telegram apuntando a tu endpoint público
```

### PythonAnywhere

1. Subir el código a `/home/tuusuario/telegram-bot-finanzas/`
2. Crear una web app Flask apuntando a `bot_pythonanywhere.application`
3. Configurar variables de entorno en el panel de PythonAnywhere
4. Configurar el webhook de Telegram: `https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://tuusuario.pythonanywhere.com/<TOKEN>`

## Arquitectura

```
telegram-bot-finanzas/
├── bot_pythonanywhere.py   # Entry point Flask WSGI + PTB init + jobs (262 líneas)
├── _env.py                 # get_db() y variables de entorno compartidas
├── commands.py             # 55 handlers de comandos (/g, /gasto, /patrimonio, /split, ...)
├── callbacks.py            # Handlers de callbacks (botones inline, flujos)
├── flows.py                # State machine de flujos de texto + handle_text (289 líneas)
├── finance_db.py           # Adapter SupabaseDB con parser SQL (738 líneas)
├── finance_shared.py       # Constantes, utilidades (h(), parse_amount, parse_quick_expense)
├── finance_state.py        # Gestión de sesiones y estado de usuario
├── finance_ui.py           # Constructores de teclados inline
├── finance_analytics.py    # Análisis: anomalías, coaching, gamificación, gráficos (536 líneas)
├── finance_notifications.py # Notificaciones: alertas, avisos de presupuesto
├── finance_reports.py      # Handlers delegados de reportes
├── handlers_registry.py    # Registro de handlers en PTB Application
├── tests/                  # Tests con pytest (28 tests)
├── requirements.txt        # Dependencias
├── requirements-dev.txt    # Dependencias de desarrollo
├── README.md               # Esta documentación
├── ARCHITECTURE.md          # Arquitectura detallada
```

### Flujo de datos

```
Telegram API → Webhook → Flask WSGI → PTB Application
                                          ├── CommandHandler → commands.py
                                          ├── CallbackQueryHandler → callbacks.py
                                          └── MessageHandler → flows.py
                                                    ↓
                                              finance_db.py → Supabase
```

### State machine de flujos

Los flujos multi-paso (crear cuenta, registrar gasto, etc.) usan un state machine manual basado en sesiones:

1. El comando guarda el estado en `session_states` (ej. `waiting_expense_amount`)
2. El siguiente mensaje del usuario se despacha via `handle_text` → `_TEXT_HANDLERS[state]`
3. Cada handler valida, guarda datos parciales y avanza al siguiente estado
4. Al finalizar, se hace commit de la transacción y se limpia la sesión

### Base de datos

Tablas en Supabase: `users`, `accounts`, `transactions`, `recurring_expenses`, `session_states`, `low_balance_alerts`, `roundup_config`, `budgets`, `savings_goals`

El adapter `SupabaseDB` traduce automáticamente SQL estándar a la API de Supabase mediante un parser regex en lugar de string-matching exacto.

## Tests

```bash
python -m pytest tests/ -v
```

28 tests cubriendo:
- Utilidades compartidas (`finance_shared`)
- Constructores de teclados (`finance_ui`)
- Análisis y anomalías (`finance_analytics`)
- Handlers de comandos (`commands`): cuentas, gasto, ingreso, traspaso, recurrente, alertas, presupuesto, metas, búsqueda
- State machine de flujos (`flows`): sesiones, expiración, cancelación
- Notificaciones y sugerencias (`finance_notifications`, `finance_reports`)

## Licencia

MIT
