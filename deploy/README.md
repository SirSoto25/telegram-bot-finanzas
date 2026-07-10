# Guía de despliegue — Alternativas gratuitas (sin sleep)

El proyecto ya tiene Dockerfile, fly.toml y requirements.txt listos.

---

## Opción 1: Fly.io (recomendada)

**Gratis:** 3 VMs × 256MB RAM, 3GB storage, sin sleep, dominios `.fly.dev` con HTTPS automático.
**Límite:** 30GB tráfico/mes. Para un bot de Telegram es más que suficiente.

### Despliegue

```bash
# 1. Instalar CLI de Fly.io
curl -L https://fly.io/install.sh | sh

# 2. Autenticarse (crea cuenta en fly.io si no tienes)
fly auth signup

# 3. Lanzar la app (desde la raíz del repo)
fly launch

# Fly.io detecta automáticamente el Dockerfile y fly.toml.
# Responde "Yes" a todo.

# 4. Configurar variables de entorno
fly secrets set TELEGRAM_BOT_TOKEN=<TU_TOKEN>
fly secrets set SUPABASE_URL=<TU_URL>
fly secrets set SUPABASE_KEY=<TU_KEY>

# 5. Desplegar
fly deploy

# 6. Configurar webhook de Telegram (usa la URL que te da fly.io)
curl "https://api.telegram.org/bot<TU_TOKEN>/setWebhook?url=https://<app-name>.fly.dev/<TOKEN>"
```

**Ventajas:** 3 regiones (Madrid disponible), HTTPS automático, no se duerme nunca, `fly deploy` en segundos.

---

## Opción 2: Koyeb

**Gratis:** 1 servicio web, 0.1 vCPU, 512MB RAM, sin sleep.
**Límite:** Solo 1 servicio en free tier.

### Despliegue

1. Sube el repo a GitHub
2. En [koyeb.com](https://app.koyeb.com), crea una app:
   - Source: GitHub → tu repo
   - Builder: Dockerfile
   - Puerto: 8080
3. Añade las 3 variables de entorno en la UI
4. Deploy
5. Configura el webhook con la URL `https://<app-name>.<org>.koyeb.app/<TOKEN>`

---

## Opción 3: Hugging Face Spaces

**Gratis:** Sin límite de horas, pero se duerme si no hay tráfico. Bueno como fallback.

El Dockerfile funciona igual — solo cambia el webhook URL.

---

## Comparativa

| Característica | Fly.io | Koyeb | PythonAnywhere |
|---------------|--------|-------|----------------|
| **Sleep** | ❌ Nunca | ❌ Nunca | ❌ Nunca |
| **HTTPS** | ✅ Auto | ✅ Auto | ✅ |
| **Región España** | ✅ Madrid | ❌ (Frankfurt) | ❌ (US) |
| **Complejidad** | CLI | Web UI | Web UI |
| **Límite tráfico** | 30GB/mes | 100GB/mes | Ilimitado |
| **Proxy 503** | ❌ Sin proxy | ❌ Sin proxy | ✅ Muy frecuente |
