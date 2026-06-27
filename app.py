import os
import sqlite3
from flask import Flask, render_template
from flask_login import LoginManager, UserMixin

import config
from db import close_connection, query_db


login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message = 'Войдите в систему'
login_manager.login_message_category = 'warning'


class User(UserMixin):
    def __init__(self, id, username, role, full_name, avatar, is_approved, organization_id):
        self.id = id
        self.username = username
        self.role = role
        self.full_name = full_name
        self.avatar = avatar
        self.is_approved = is_approved
        self.organization_id = organization_id

    @property
    def initials(self):
        if not self.full_name:
            return self.username[:2].upper()
        parts = self.full_name.split()
        return ''.join(p[0].upper() for p in parts[:2])

    @property
    def role_label(self):
        return config.ROLES.get(self.role, self.role)


def load_user_obj(row):
    return User(
        id=row['id'], username=row['username'], role=row['role'],
        full_name=row['full_name'], avatar=row['avatar'],
        is_approved=row['is_approved'], organization_id=row['organization_id'],
    )


@login_manager.user_loader
def load_user(user_id):
    row = query_db('SELECT * FROM users WHERE id = ?', (user_id,), one=True)
    return load_user_obj(row) if row else None


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH

    login_manager.init_app(app)
    app.teardown_appcontext(close_connection)

    app.jinja_env.globals.update(
        ROLES=config.ROLES,
        SELF_REGISTER_ROLES=config.SELF_REGISTER_ROLES,
        ORG_TYPES=config.ORG_TYPES,
        UNITS=config.UNITS,
    )

    register_routes(app)

    @app.context_processor
    def inject_unread_count():
        from flask_login import current_user
        if current_user.is_authenticated:
            row = query_db(
                'SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0',
                (current_user.id,), one=True,
            )
            return {'unread_count': row['cnt'] if row else 0}
        return {'unread_count': 0}

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    return app


def register_routes(app):
    from routes import auth, notifications, dashboards, objects
    auth.register(app)
    notifications.register(app)
    dashboards.register(app)
    objects.register(app)


def init_db():
    db = sqlite3.connect(config.DATABASE)
    db.execute('PRAGMA foreign_keys = ON')

    db.executescript('''
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('developer', 'contractor')),
            inn TEXT,
            kpp TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            avatar TEXT,
            is_approved INTEGER DEFAULT 0,
            organization_id INTEGER REFERENCES organizations(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            type TEXT,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            type TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS construction_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            order_num INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            contractor_id INTEGER REFERENCES organizations(id),
            contractor_status TEXT NOT NULL DEFAULT 'search' CHECK(contractor_status IN ('search', 'assigned')),
            plan_start_date DATE,
            plan_end_date DATE,
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'in_progress', 'done', 'suspended')),
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stage_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            doc_type TEXT NOT NULL DEFAULT 'other' CHECK(doc_type IN ('contract', 'tech_spec', 'work_schedule', 'other')),
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS guest_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER,
            token TEXT UNIQUE NOT NULL,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    run_migrations(db)
    _seed(db)
    db.close()


def _seed(db):
    count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count > 0:
        return

    from werkzeug.security import generate_password_hash

    db.execute("INSERT INTO organizations (name, type, inn) VALUES (?, 'developer', '5900000001')",
               ('ГК Федерация',))
    db.execute("INSERT INTO organizations (name, type, inn) VALUES (?, 'contractor', '5900000002')",
               ('СтройМонтаж',))
    dev_id = db.execute("SELECT id FROM organizations WHERE type='developer'").fetchone()[0]
    con_id = db.execute("SELECT id FROM organizations WHERE type='contractor'").fetchone()[0]

    seed_users = [
        ('admin',      'admin',      'Админов Админ',        'admin',      None),
        ('manager',    'manager',    'Руководителев Иван',   'manager',    dev_id),
        ('pto',        'pto',        'Птошников Пётр',       'pto',        dev_id),
        ('inspector',  'inspector',  'Надзоров Сергей',      'inspector',  dev_id),
        ('foreman',    'foreman',    'Прорабов Алексей',     'foreman',    dev_id),
        ('supply',     'supply',     'Снабженцев Дмитрий',  'supply',     dev_id),
        ('accountant', 'accountant', 'Бухгалтерова Елена',  'accountant', dev_id),
        ('contractor', 'contractor', 'Подрядчиков Михаил',  'contractor', con_id),
    ]
    for username, role, full_name, password, org_id in seed_users:
        db.execute(
            'INSERT INTO users (username, password_hash, role, full_name, is_approved, organization_id) '
            'VALUES (?, ?, ?, ?, 1, ?)',
            (username, generate_password_hash(password), role, full_name, org_id),
        )
    manager_id = db.execute("SELECT id FROM users WHERE username='manager'").fetchone()[0]
    contractor_id = db.execute("SELECT id FROM users WHERE username='contractor'").fetchone()[0]
    inspector_id = db.execute("SELECT id FROM users WHERE username='inspector'").fetchone()[0]

    test_notifications = [
        (manager_id, 'system', 'Добро пожаловать в ШТАБ', 'Система готова к работе. Создайте первый объект.', '/dashboard'),
        (manager_id, 'approval', 'Новый пакет на согласование', 'Подрядчик «СтройМонтаж» отправил пакет документов по подэтапу «Монолитные работы».', '/dashboard'),
        (contractor_id, 'defect', 'Новое замечание', 'Технадзор выявил замечание: трещина в перекрытии 3-го этажа.', '/dashboard'),
        (inspector_id, 'system', 'Добро пожаловать в ШТАБ', 'Вы назначены технадзором. Проверяйте ход работ и выдавайте замечания.', '/dashboard'),
    ]
    for uid, ntype, title, body, link in test_notifications:
        db.execute(
            'INSERT INTO notifications (user_id, type, title, body, link) VALUES (?, ?, ?, ?, ?)',
            (uid, ntype, title, body, link),
        )

    db.commit()


def run_migrations(db):
    pass


for folder in (config.UPLOAD_FOLDER, config.DOCS_FOLDER, config.AVATARS_FOLDER):
    os.makedirs(folder, exist_ok=True)

app = create_app()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)
