#!/bin/bash
# Деплой Web Push уведомлений
set -e

SERVER="root@159.194.206.104"
DIR="/var/www/shtab"

# VAPID-ключи читаются из локального .env, чтобы не хранить секреты в скрипте
VAPID_PRIVATE_KEY=$(grep '^VAPID_PRIVATE_KEY=' .env | cut -d= -f2-)
VAPID_PUBLIC_KEY=$(grep '^VAPID_PUBLIC_KEY=' .env | cut -d= -f2-)
VAPID_EMAIL=$(grep '^VAPID_EMAIL=' .env | cut -d= -f2-)

echo "=== 1. Пуллим код ==="
ssh $SERVER "cd $DIR && git pull"

echo "=== 2. Устанавливаем pywebpush ==="
ssh $SERVER "cd $DIR && source venv/bin/activate && pip install pywebpush==2.0.0"

echo "=== 3. Добавляем VAPID ключи в .env на сервере ==="
ssh $SERVER "
  grep -q VAPID_PRIVATE_KEY $DIR/.env 2>/dev/null || cat >> $DIR/.env << ENVEOF
VAPID_PRIVATE_KEY=$VAPID_PRIVATE_KEY
VAPID_PUBLIC_KEY=$VAPID_PUBLIC_KEY
VAPID_EMAIL=$VAPID_EMAIL
ENVEOF
  echo 'VAPID keys OK'
"

echo "=== 4. Миграция БД (создаём таблицу push_subscriptions) ==="
ssh $SERVER "cd $DIR && source venv/bin/activate && python3 -c 'from app import init_db; init_db()'"

echo "=== 5. Перезапускаем сервис ==="
ssh $SERVER "systemctl restart shtab && sleep 2 && systemctl is-active shtab"

echo "=== Готово! Проверяем ==="
curl -s https://shtab-crm.ru/api/push/vapid-public-key
echo ""
echo "Деплой успешен!"
