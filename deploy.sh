#!/bin/bash
# Деплой ШТАБ на прод (shtab-crm.ru)
# Использование: ./deploy.sh
# Требует настроенный ssh-доступ root@shtab-crm.ru (или sshpass в SSHPASS)
set -e

SERVER="root@shtab-crm.ru"
DIR="/var/www/shtab"

SSH="ssh $SERVER"
if [ -n "${SSHPASS:-}" ]; then
  SSH="sshpass -e ssh $SERVER"
fi

echo "=== 1. Бэкап БД ==="
$SSH "PGPASSWORD=\$(grep -oP 'postgresql://shtab:\K[^@]+' $DIR/.env) \
  pg_dump -h localhost -U shtab shtab > /var/backups/shtab/shtab_before_deploy_\$(date +%Y%m%d_%H%M%S).sql && \
  ls -t /var/backups/shtab/*.sql | head -1"

echo "=== 2. Пуллим код ==="
$SSH "cd $DIR && git pull"

echo "=== 3. Зависимости ==="
$SSH "cd $DIR && source venv/bin/activate && pip install -q -r requirements.txt"

echo "=== 4. Миграции БД ==="
$SSH "cd $DIR && source venv/bin/activate && python3 -c 'from app import init_db; init_db()'"

echo "=== 5. Права на загрузки (git pull под root создаёт каталоги root'ом) ==="
$SSH "chown -R shtab:shtab $DIR/static"

echo "=== 6. Рестарт сервиса ==="
$SSH "systemctl restart shtab && sleep 3 && systemctl is-active shtab"

echo "=== 7. Проверка ==="
curl -s -o /dev/null -w "https://shtab-crm.ru → %{http_code}\n" https://shtab-crm.ru/
$SSH "cd $DIR && git log --oneline -1"

echo "Деплой успешен!"
