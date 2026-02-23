#!/bin/bash

# ==========================================
# MyBot Restart Bo'limini Avto-Sozlash
# Ubuntu Server uchun
# ==========================================

set -e  # Xatolik bo'lsa to'xtaydi

echo "=========================================="
echo "🚀 MyBot Restart Bo'limini Sozlash"
echo "=========================================="
echo ""

# ==========================================
# 1. Tekshirish: Root ruxsati
# ==========================================

if [ "$EUID" -ne 0 ]; then 
    echo "❌ Iltimos, sudo bilan ishga tushiring:"
    echo "   sudo bash setup_restart.sh"
    exit 1
fi

echo "✅ Root ruxsati tasdiqlandi"
echo ""

# ==========================================
# 2. Bot papkasini aniqlash
# ==========================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo "📁 Bot papkasi: $SCRIPT_DIR"

# main.py borligini tekshirish
if [ ! -f "$SCRIPT_DIR/main.py" ]; then
    echo "❌ main.py topilmadi! To'g'ri papkadan ishga tushirganingizga ishonch hosil qiling."
    exit 1
fi

echo "✅ main.py topildi"
echo ""

# ==========================================
# 3. Python yo'lini topish
# ==========================================

PYTHON_PATH=$(which python3)
if [ -z "$PYTHON_PATH" ]; then
    echo "❌ python3 topilmadi! Python 3 o'rnatilganligini tekshiring."
    exit 1
fi

echo "🐍 Python yo'li: $PYTHON_PATH"
echo "   Versiya: $(python3 --version)"
echo ""

# ==========================================
# 4. Joriy user ni aniqlash
# ==========================================

CURRENT_USER=$(logname 2>/dev/null || echo $SUDO_USER)
if [ -z "$CURRENT_USER" ]; then
    CURRENT_USER="root"
fi

echo "👤 Bot user: $CURRENT_USER"
echo ""

# ==========================================
# 5. Service nomi
# ==========================================

SERVICE_NAME="mybot.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo "⚙️  Service nomi: $SERVICE_NAME"
echo ""

# ==========================================
# 6. Service faylini yaratish
# ==========================================

echo "📝 Service faylini yaratish..."

cat > $SERVICE_PATH << EOF
[Unit]
Description=MyBot Telegram Bot Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_PATH $SCRIPT_DIR/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

if [ -f "$SERVICE_PATH" ]; then
    echo "✅ Service fayli yaratildi: $SERVICE_PATH"
else
    echo "❌ Service faylini yaratishda xatolik!"
    exit 1
fi
echo ""

# ==========================================
# 7. Systemd ni yangilash
# ==========================================

echo "🔄 Systemd ni yangilash..."
systemctl daemon-reload
echo "✅ Systemd yangilandi"
echo ""

# ==========================================
# 8. Service ni yoqish
# ==========================================

echo "▶️  Service ni yoqish..."
systemctl enable $SERVICE_NAME
echo "✅ Service avtomatik ishga tushishga sozlandi"
echo ""

# ==========================================
# 9. Visudo sozlash
# ==========================================

echo "🔐 Sudo ruxsatlarini sozlash..."

VISUDO_FILE="/etc/sudoers.d/mybot-restart"

cat > $VISUDO_FILE << EOF
# MyBot restart ruxsatlari
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl start $SERVICE_NAME
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop $SERVICE_NAME
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl status $SERVICE_NAME
$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/journalctl
EOF

# Ruxsatlarni to'g'rilash
chmod 0440 $VISUDO_FILE

# Visudo tekshirish
if visudo -c -f $VISUDO_FILE; then
    echo "✅ Sudo ruxsatlari sozlandi: $VISUDO_FILE"
else
    echo "❌ Visudo faylida xatolik! O'chirilmoqda..."
    rm -f $VISUDO_FILE
    exit 1
fi
echo ""

# ==========================================
# 10. Eski botni to'xtatish (agar ishlab tursa)
# ==========================================

echo "⏹  Eski bot jarayonlarini tekshirish..."

# main.py ishlab turgan jarayonlarni topish (systemd dan tashqari)
PIDS=$(pgrep -f "python.*main.py" | grep -v "^$$\$" || true)

if [ ! -z "$PIDS" ]; then
    echo "⚠️  Eski bot jarayonlari topildi, to'xtatilmoqda..."
    kill -9 $PIDS 2>/dev/null || true
    sleep 2
    echo "✅ Eski jarayonlar to'xtatildi"
else
    echo "✅ Eski jarayonlar yo'q"
fi
echo ""

# ==========================================
# 11. Service ni ishga tushirish
# ==========================================

echo "🚀 Botni ishga tushirish..."
systemctl start $SERVICE_NAME
sleep 3
echo ""

# ==========================================
# 12. Status tekshirish
# ==========================================

echo "=========================================="
echo "📊 SERVICE HOLATI:"
echo "=========================================="
systemctl status $SERVICE_NAME --no-pager -l || true
echo ""

# ==========================================
# 13. Natija
# ==========================================

if systemctl is-active --quiet $SERVICE_NAME; then
    echo "=========================================="
    echo "✅ MUVAFFAQIYATLI SOZLANDI!"
    echo "=========================================="
    echo ""
    echo "✨ Bot muvaffaqiyatli ishga tushirildi!"
    echo ""
    echo "📋 Foydali buyruqlar:"
    echo "   systemctl status $SERVICE_NAME    - Holatni ko'rish"
    echo "   systemctl restart $SERVICE_NAME   - Restart qilish"
    echo "   systemctl stop $SERVICE_NAME      - To'xtatish"
    echo "   journalctl -u $SERVICE_NAME -f    - Loglarni ko'rish"
    echo ""
    echo "🎯 Telegram botda sinab ko'ring:"
    echo "   /admin → 🔄 Botni restart qilish → 📊 Status"
    echo ""
else
    echo "=========================================="
    echo "⚠️  SERVICE ISHLAMAYAPTI!"
    echo "=========================================="
    echo ""
    echo "❌ Bot ishga tushmadi. Loglarni tekshiring:"
    echo "   journalctl -u $SERVICE_NAME -n 50"
    echo ""
    echo "Keng tarqalgan muammolar:"
    echo "   1. config.py sozlanmagan (bot token, API keys)"
    echo "   2. requirements.txt o'rnatilmagan (pip install -r requirements.txt)"
    echo "   3. Python versiyasi mos emas"
    echo "   4. Fayl ruxsatlari noto'g'ri"
    echo ""
    exit 1
fi

echo "=========================================="
echo "🎉 SETUP TUGADI!"
echo "=========================================="

