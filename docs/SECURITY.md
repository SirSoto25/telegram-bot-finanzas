# Security & Hardening

## Secretos

- No subir tokens ni keys al repositorio.
- Definir `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `SUPABASE_URL`, `SUPABASE_KEY` como variables de entorno.
- Rotar claves tras cualquier sospecha de exposición.

## Webhook Telegram

- El endpoint valida `X-Telegram-Bot-Api-Secret-Token`.
- Requests con token inválido deben responder `403`.

## Supabase

- Activar RLS en tablas expuestas por API.
- Definir políticas explícitas por tabla.
- Evitar exponer `service_role` en clientes públicos.

## Validación de entrada

- Importes monetarios estrictamente positivos.
- Callback data parseada con validación robusta.
- Rechazar estados inválidos con mensaje controlado.

## Logging

- Registrar excepciones no controladas con contexto.
- No loggear secretos ni payloads sensibles completos.

## Checklist antes de producción

1. Variables de entorno presentes y correctas.
2. Webhook secret activo.
3. RLS validado en Supabase.
4. Pruebas de rutas críticas (`/gasto`, `/ingreso`, `/traspaso`).
5. Plan de rollback documentado.

