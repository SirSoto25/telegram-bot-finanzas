# ──────────────────────────────────────────────────
# Oracle Cloud Always Free — Deployment Guide
# VM: Ampere A1 (4 OCPU, 24GB RAM, ARM64)
# Distro: Ubuntu 24.04 LTS
# ──────────────────────────────────────────────────

# 1. CONECTAR A LA VM
ssh ubuntu@<IP_PUBLICA>

# 2. INSTALAR DEPENDENCIAS
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx

# 3. CREAR DIRECTORIO Y CLONAR REPO
mkdir -p /home/ubuntu/telegram-bot-finanzas
cd /home/ubuntu/telegram-bot-finanzas
git clone https://github.com/SirSoto25/telegram-bot-finanzas.git .

# 4. INSTALAR PAQUETES PYTHON
pip install --break-system-packages -r requirements.txt

# 5. CONFIGURAR VARIABLES DE ENTORNO
sudo mkdir -p /etc/finance-bot
sudo tee /etc/finance-bot/env <<EOF
TELEGRAM_BOT_TOKEN=<TU_TOKEN>
SUPABASE_URL=<TU_URL>
SUPABASE_KEY=<TU_KEY>
EOF
sudo chmod 600 /etc/finance-bot/env

# 6. ACTIVAR SERVICIOS
sudo cp /home/ubuntu/telegram-bot-finanzas/deploy/finance-bot.service /etc/systemd/system/
sudo cp /home/ubuntu/telegram-bot-finanzas/deploy/nginx.conf /etc/nginx/sites-available/finance-bot
sudo ln -sf /etc/nginx/sites-available/finance-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Reemplazar <TOKEN> en nginx con el token real
sudo sed -i 's/<TOKEN>/<TU_TOKEN_REAL>/' /etc/nginx/sites-available/finance-bot

# 7. CONFIGURAR FIREWALL
sudo apt install -y ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# 8. INICIAR SERVICIOS
sudo systemctl daemon-reload
sudo systemctl enable finance-bot
sudo systemctl start finance-bot
sudo systemctl restart nginx

# 9. HTTPS CON LET'S ENCRYPT
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <TU_DOMINIO>

# 10. CONFIGURAR WEBHOOK DE TELEGRAM
curl "https://api.telegram.org/bot<TU_TOKEN>/setWebhook?url=https://<TU_DOMINIO>/<TU_TOKEN>"

# 11. VERIFICAR
sudo systemctl status finance-bot
sudo journalctl -u finance-bot -f
