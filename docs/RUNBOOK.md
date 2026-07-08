# Runbook Operativo

## 1. Reinicio de servicio (PythonAnywhere)

1. Actualiza código en `/home/sirsoto25/bot/`.
2. Verifica variables de entorno en la Web App.
3. Pulsa **Reload** en PythonAnywhere.
4. Comprueba logs de error y acceso.

## 2. Verificación rápida tras despliegue

1. `GET /` responde `Finance Bot OK`.
2. Telegram webhook devuelve `200` para requests válidas.
3. Ejecutar `/start` y una operación simple (`/cuentas`).

## 3. Incidentes comunes

## `ImportError: cannot import name 'application'`

- Causa: módulo no exporta `application`.
- Acción: confirmar `application = app` en `bot_pythonanywhere.py`.

## `TypeError: can't subtract offset-naive and offset-aware datetimes`

- Causa: mezcla de datetime naive/aware.
- Acción: normalizar `now` a `created.tzinfo` antes de comparar.

## `RLS 42501` en Supabase

- Causa: políticas RLS bloquean escritura.
- Acción: usar key adecuada de backend o corregir políticas RLS.

## 4. Backups y restauración

1. Mantener snapshots en Supabase (DB backups).
2. Mantener exportaciones CSV periódicas para validación operativa.
3. Restaurar primero base de datos, luego código, luego recarga de app.

## 5. Rollback de release

1. Checkout al commit estable anterior.
2. Desplegar archivo en servidor.
3. Reload de PythonAnywhere.
4. Verificar `/start`, `/gasto`, `/ingreso`.

