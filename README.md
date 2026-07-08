# Telegram Bot Finanzas

Bot de finanzas personales para Telegram con backend en **Flask + python-telegram-bot** y persistencia en **Supabase**.

## Características

- Gestión de cuentas (nómina, ahorros, inversión, cripto)
- Registro de gastos, ingresos y transferencias
- Gastos recurrentes e ingresos recurrentes
- Presupuestos y metas de ahorro
- Alertas de saldo bajo
- Panel semanal, forecast y detección de anomalías
- Etiquetas con `#hashtag` y sugerencias de categoría
- Exportación CSV y estadísticas básicas

## Requisitos

- Python 3.11+
- Dependencias:
  - `python-telegram-bot`
  - `flask`
  - `supabase`

## Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí | Token del bot de Telegram |
| `TELEGRAM_WEBHOOK_SECRET` | Recomendado | Secret token del webhook de Telegram |
| `SUPABASE_URL` | Sí | URL del proyecto Supabase |
| `SUPABASE_KEY` | Sí | Key del backend (preferible service role en este diseño) |

## Ejecución local

```bash
python3 -m pip install --upgrade python-telegram-bot flask supabase
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_WEBHOOK_SECRET="..."
export SUPABASE_URL="https://<project>.supabase.co"
export SUPABASE_KEY="..."
python3 -m flask --app bot_pythonanywhere run --host 0.0.0.0 --port 8000
```

## Despliegue en PythonAnywhere (WSGI)

El WSGI debe importar:

```python
from bot_pythonanywhere import application
```

Puntos importantes:

1. El archivo debe existir en la ruta configurada en el WSGI.
2. `application` debe estar exportado por el módulo.
3. Define todas las variables de entorno en la Web App.
4. Recarga la app tras cada despliegue.

## Comandos principales del bot

- `/start`, `/help`, `/menu`
- `/cuentas`, `/nuevacuenta`, `/borrarcuenta`
- `/gasto`, `/ingreso`, `/traspaso`, `/deshacer`
- `/recurrente`, `/agregarrecurrente`, `/borrarrecurrente`
- `/ingresorecurrente`, `/agregaringresorecurrente`
- `/presupuesto`, `/presupuestoset`
- `/metas`, `/nuevameta`, `/aportarmeta`
- `/alertas`, `/agregaralerta`, `/borraralerta`
- `/panel`, `/forecast`, `/anomalias`, `/tags`, `/sugerircategoria`
- `/exportar`

## Documentación adicional

- `docs/RUNBOOK.md`
- `docs/REFERENCE_COMMANDS_AND_STATES.md`
- `docs/SECURITY.md`
- `docs/adr/0001-architecture-flask-ptb-supabase.md`
