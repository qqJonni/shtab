#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8080

# Определяем локальный IP
if command -v ipconfig &>/dev/null; then
  # macOS
  LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")
else
  # Linux
  LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
fi

echo "══════════════════════════════════════"
echo "  ШТАБ — запуск для LAN-тестирования"
echo "══════════════════════════════════════"
echo ""
echo "  Этот ПК:  http://localhost:${PORT}"
echo "  По сети:  http://${LOCAL_IP}:${PORT}"
echo ""
echo "  Откройте адрес выше на телефоне"
echo "  или втором ПК в той же Wi-Fi сети."
echo ""
echo "  Логины: manager/manager, contractor/contractor,"
echo "  pto/pto, inspector/inspector, foreman/foreman,"
echo "  supply/supply, accountant/accountant, admin/admin"
echo ""
echo "  Ctrl+C — остановить сервер"
echo "══════════════════════════════════════"
echo ""

cd "$SCRIPT_DIR"
python3 app.py
