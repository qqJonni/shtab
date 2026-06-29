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

    @app.context_processor
    def inject_now_date():
        from datetime import date
        return {'now_date': date.today().isoformat()}

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    return app


def register_routes(app):
    from routes import auth, notifications, dashboards, objects, defects, packages, supply, export, guest, admin, report_page, plans, journal
    auth.register(app)
    notifications.register(app)
    dashboards.register(app)
    objects.register(app)
    defects.register(app)
    packages.register(app)
    supply.register(app)
    export.register(app)
    guest.register(app)
    admin.register(app)
    report_page.register(app)
    plans.register(app)
    journal.register(app)


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
            doc_type TEXT NOT NULL DEFAULT 'other' CHECK(doc_type IN ('contract', 'tech_spec', 'price_doc', 'work_schedule', 'other')),
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS substages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT,
            volume REAL,
            unit TEXT,
            unit_price REAL,
            total_price REAL,
            plan_end_date DATE,
            status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN ('not_started', 'in_progress', 'done', 'closed', 'approved')),
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS substage_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            substage_id INTEGER NOT NULL REFERENCES substages(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doc_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            substage_id INTEGER NOT NULL REFERENCES substages(id),
            contractor_id INTEGER REFERENCES organizations(id),
            created_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'in_review', 'returned', 'approved', 'completed')),
            return_to_role TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            submitted_at TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS package_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            doc_type TEXT NOT NULL DEFAULT 'free_form' CHECK(doc_type IN ('ks2', 'ks3', 'invoice', 'raw_material_report', 'free_form')),
            title TEXT NOT NULL,
            filename TEXT,
            is_generated INTEGER NOT NULL DEFAULT 0,
            data_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS approval_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting', 'pending', 'approved', 'returned')),
            approver_id INTEGER REFERENCES users(id),
            comment TEXT,
            acted_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS defect_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            order_num INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS defects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER NOT NULL REFERENCES objects(id),
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id),
            substage_id INTEGER REFERENCES substages(id),
            title TEXT NOT NULL,
            description TEXT,
            type_id INTEGER REFERENCES defect_types(id),
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'critical')),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'resolved', 'verified', 'rejected', 'closed')),
            reporter_id INTEGER NOT NULL REFERENCES users(id),
            responsible_id INTEGER REFERENCES users(id),
            contractor_id INTEGER REFERENCES organizations(id),
            due_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            verified_at TIMESTAMP,
            reopen_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS defect_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_id INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            photo_type TEXT NOT NULL DEFAULT 'general' CHECK(photo_type IN ('before', 'after', 'general')),
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS defect_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_id INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            author_id INTEGER NOT NULL REFERENCES users(id),
            entry_date DATE NOT NULL,
            text TEXT NOT NULL,
            weather TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS journal_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS object_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            level_label TEXT,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'image',
            sort_order INTEGER NOT NULL DEFAULT 0,
            uploaded_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS material_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id),
            substage_id INTEGER REFERENCES substages(id),
            contractor_id INTEGER REFERENCES organizations(id),
            requested_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted', 'returned', 'approved', 'processing', 'completed')),
            current_role TEXT NOT NULL DEFAULT 'pto' CHECK(current_role IN ('pto', 'supply')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS material_request_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES material_requests(id) ON DELETE CASCADE,
            material_name TEXT NOT NULL,
            unit TEXT,
            quantity REAL,
            price REAL,
            comment TEXT
        );

        CREATE TABLE IF NOT EXISTS material_request_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES material_requests(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    defect_types = [
        'Отделка', 'Электрика', 'Сантехника', 'Окна/двери',
        'Стены/потолки', 'Полы', 'Кровля', 'Фасад', 'Прочее',
    ]
    for i, dt in enumerate(defect_types, 1):
        db.execute('INSERT INTO defect_types (name, order_num) VALUES (?, ?)', (dt, i))

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
    def _col_exists(table, col):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        return col in cols

    # M4-S6: реквизиты организаций
    for col, typedef in [
        ('address', 'TEXT'), ('ogrn', 'TEXT'), ('okpo', 'TEXT'),
        ('phone', 'TEXT'), ('rep_position', 'TEXT'), ('rep_name', 'TEXT'),
        ('email', 'TEXT'),
    ]:
        if not _col_exists('organizations', col):
            db.execute(f'ALTER TABLE organizations ADD COLUMN {col} {typedef}')

    # M4-S6: реквизиты объектов
    for col, typedef in [
        ('construction_name', 'TEXT'), ('construction_address', 'TEXT'),
        ('cadastral_number', 'TEXT'),
    ]:
        if not _col_exists('objects', col):
            db.execute(f'ALTER TABLE objects ADD COLUMN {col} {typedef}')

    # M4-S6: договорные данные этапа
    for col, typedef in [
        ('contract_number', 'TEXT'), ('contract_date', 'DATE'),
        ('contract_amount', 'REAL'),
    ]:
        if not _col_exists('construction_stages', col):
            db.execute(f'ALTER TABLE construction_stages ADD COLUMN {col} {typedef}')

    # M4-S6: дефолтная ставка НДС
    existing = db.execute("SELECT id FROM settings WHERE key='vat_rate'").fetchone()
    if not existing:
        db.execute("INSERT INTO settings (key, value) VALUES ('vat_rate', '20')")

    # M6-T: добавить price_doc в stage_documents.doc_type CHECK
    check_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='stage_documents'").fetchone()
    if check_sql and 'price_doc' not in (check_sql[0] or ''):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS stage_documents_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage_id INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
                doc_type TEXT NOT NULL DEFAULT 'other' CHECK(doc_type IN ('contract', 'tech_spec', 'price_doc', 'work_schedule', 'other')),
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                uploaded_by INTEGER REFERENCES users(id),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO stage_documents_new SELECT * FROM stage_documents;
            DROP TABLE stage_documents;
            ALTER TABLE stage_documents_new RENAME TO stage_documents;
        ''')

    # M7A: plan_id, pin_x, pin_y в defects
    for col, typedef in [('plan_id', 'INTEGER'), ('pin_x', 'REAL'), ('pin_y', 'REAL')]:
        if not _col_exists('defects', col):
            db.execute(f'ALTER TABLE defects ADD COLUMN {col} {typedef}')

    db.commit()


for folder in (config.UPLOAD_FOLDER, config.DOCS_FOLDER, config.AVATARS_FOLDER, config.DEFECTS_FOLDER, config.PACKAGES_FOLDER, config.PLANS_FOLDER, config.JOURNAL_FOLDER):
    os.makedirs(folder, exist_ok=True)

app = create_app()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)
