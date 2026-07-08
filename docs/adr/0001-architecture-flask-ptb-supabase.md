# ADR-0001: Arquitectura Flask + python-telegram-bot + Supabase

## Estado

Aceptado.

## Contexto

El bot requiere:

- Endpoint HTTP para webhook de Telegram (compatible con WSGI en PythonAnywhere).
- Lógica conversacional asíncrona para comandos/callbacks.
- Persistencia gestionada y accesible desde entorno cloud.

## Decisión

Se adopta:

1. **Flask** como capa HTTP/WSGI para recibir webhooks.
2. **python-telegram-bot** como motor de handlers y flujos de conversación.
3. **Supabase (Postgres + Data API)** como backend de datos.

## Consecuencias positivas

- Despliegue simple en PythonAnywhere.
- Menor carga operativa de base de datos.
- Evolución rápida de funcionalidades del bot.

## Riesgos y mitigaciones

- **Riesgo**: inconsistencias por operaciones multi-step no atómicas.
  - **Mitigación**: migrar operaciones críticas a RPC transaccional.
- **Riesgo**: bloqueo por RLS.
  - **Mitigación**: políticas explícitas y validación previa.
- **Riesgo**: tráfico no legítimo al webhook.
  - **Mitigación**: validación de secret token del webhook.

## Alternativas consideradas

- Bot polling puro sin Flask: descartado por necesidad de webhook en entorno WSGI.
- Base de datos SQLite local: descartada por portabilidad y concurrencia limitada.

