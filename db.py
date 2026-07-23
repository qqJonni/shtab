import re
import psycopg2
import psycopg2.extras
from flask import g
import config


def _to_pg(sql):
    """Translate SQLite ? placeholders → psycopg2 %s."""
    return sql.replace('?', '%s')


def _is_insert(sql):
    return sql.strip().upper().startswith('INSERT')


class _PgCursor:
    """
    Thin wrapper around psycopg2 RealDictCursor.
    Adds .lastrowid so existing routes don't break before Step 4.
    For INSERTs the caller's RETURNING id row is consumed here.
    """
    def __init__(self, cur, insert_id=None):
        self._cur = cur
        self.lastrowid = insert_id

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    def close(self):
        self._cur.close()

    # allow positional [0] on the cursor itself (used in app.py seed queries)
    def __getitem__(self, item):
        return self._cur.__getitem__(item)


class _PgConnection:
    """
    Wraps a psycopg2 connection to match the legacy sqlite3 usage pattern:
      db = get_db()
      cur = db.execute(sql, args)
      db.commit()
    Translates ? → %s and handles RETURNING id for INSERTs.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, args=()):
        pg_sql = _to_pg(sql.strip())
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        insert_id = None
        if _is_insert(pg_sql) and 'RETURNING' not in pg_sql.upper():
            pg_sql = pg_sql.rstrip('; ') + ' RETURNING id'
            cur.execute(pg_sql, args or None)
            row = cur.fetchone()
            if row and 'id' in row:
                insert_id = row['id']
        else:
            cur.execute(pg_sql, args or None)

        return _PgCursor(cur, insert_id)

    def executescript(self, script):
        """
        Compatibility shim for SQLite executescript used in migrations.
        Splits on semicolons and executes each statement.
        Will be replaced entirely in Step 3.
        """
        cur = self._conn.cursor()
        statements = [s.strip() for s in script.split(';') if s.strip()]
        for stmt in statements:
            cur.execute(stmt)
        cur.close()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    if 'db' not in g:
        conn = psycopg2.connect(config.DATABASE_URL)
        g.db = _PgConnection(conn)
    return g.db


def close_connection(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_db(sql, args=(), one=False):
    pg_sql = _to_pg(sql)
    conn = get_db()._conn
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(pg_sql, args or None)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute_db(sql, args=()):
    """
    Executes a statement and commits.
    For INSERT returns the new row id (via RETURNING id).
    For UPDATE/DELETE returns None.
    """
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def get_setting(key, default=None):
    row = query_db('SELECT value FROM settings WHERE key = ?', (key,), one=True)
    return row['value'] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value',
        (key, str(value)),
    )
    db.commit()


def _send_email(to_addr, subject, body, link=''):
    """Отправка письма через SMTP. Никогда не бросает исключений.
    Возвращает True при успехе. Если SMTP не настроен — тихо False."""
    import config
    if not config.email_enabled() or not to_addr:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        text = body or ''
        if link:
            base = os.environ.get('APP_BASE_URL', 'https://shtab-crm.ru').rstrip('/')
            text += f'\n\n{base}{link}'
        msg = MIMEText(text, 'plain', 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')
        msg['From'] = config.EMAIL_FROM
        msg['To'] = to_addr
        # порт 465 → неявный SSL; иначе SMTP + STARTTLS
        if int(config.EMAIL_PORT) == 465:
            server = smtplib.SMTP_SSL(config.EMAIL_HOST, config.EMAIL_PORT, timeout=20)
        else:
            server = smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=20)
            if config.EMAIL_USE_TLS:
                server.starttls()
        if config.EMAIL_USER:
            server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_FROM, [to_addr], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False


# Событийные типы, подпадающие под роутинг тенанта (вкл/выкл).
# Системные (digest, user) шлются всегда.
_ROUTABLE_TYPES = {'approval', 'defect', 'supply'}


def notify(user_id, type, title, body='', link=''):
    """Создаёт уведомление получателю с учётом настроек тенанта и пользователя.

    - Тип-роутинг тенанта: если событийный тип выключен у тенанта получателя
      (developer-роли) — уведомление не создаётся вообще.
    - Каналы = пересечение(каналы тенанта, каналы пользователя):
      in_app (запись в БД), push (web-push), email (SMTP).
    - Дефолты = все каналы включены, все типы включены (текущее поведение)."""
    from helpers import (recipient_channels_and_types, )
    channels, allowed = recipient_channels_and_types(user_id)
    if type in _ROUTABLE_TYPES and type not in allowed:
        return  # тип выключен у тенанта получателя

    db = get_db()
    if 'in_app' in channels:
        db.execute(
            'INSERT INTO notifications (user_id, type, title, body, link) VALUES (?, ?, ?, ?, ?)',
            (user_id, type, title, body, link),
        )
        db.commit()
    if 'push' in channels:
        _send_web_push_to_user(user_id, title, body, link)
    if 'email' in channels:
        row = query_db('SELECT email FROM users WHERE id = ?', (user_id,), one=True)
        if row and row['email']:
            _send_email(row['email'], title, body, link)


def _send_web_push_to_user(user_id, title, body, link):
    """Send Web Push to all registered subscriptions for user. Never raises."""
    try:
        import os, json
        from pywebpush import webpush, WebPushException
        private_key = os.environ.get('VAPID_PRIVATE_KEY', '')
        email = os.environ.get('VAPID_EMAIL', 'admin@shtab-crm.ru')
        if not private_key:
            return
        db = get_db()
        subs = db.execute(
            'SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?',
            (user_id,)
        ).fetchall()
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        'endpoint': sub['endpoint'],
                        'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
                    },
                    data=json.dumps({'title': title, 'body': body, 'link': link}),
                    vapid_private_key=private_key,
                    vapid_claims={'sub': f'mailto:{email}'},
                    timeout=5,
                )
            except WebPushException as e:
                if e.response is not None and e.response.status_code in (404, 410):
                    db.execute('DELETE FROM push_subscriptions WHERE id = ?', (sub['id'],))
                    db.commit()
    except Exception:
        pass
