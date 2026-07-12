import os
import psycopg2
import psycopg2.extras
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
    app.config['TEMPLATES_AUTO_RELOAD'] = True

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

    @app.context_processor
    def inject_static_version():
        # cache-busting для crm.css: версия = mtime файла
        css_path = os.path.join(app.static_folder, 'css', 'crm.css')
        try:
            v = int(os.path.getmtime(css_path))
        except OSError:
            v = 0
        return {'static_version': v}

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    return app


def register_routes(app):
    from routes import auth, notifications, dashboards, objects, defects, packages, supply, export, guest, admin, report_page, plans, journal, pwa, smeta, digest, id_module, schedule
    auth.register(app)
    schedule.register(app)
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
    pwa.register(app)
    smeta.register(app)
    digest.register(app)
    id_module.register(app)


def init_db():
    conn = psycopg2.connect(config.DATABASE_URL)
    cur = conn.cursor()

    # Все даты/timestamps хранятся как TEXT, чтобы не ломать строковые сравнения.
    # DEFAULT to_char(now(),...) воспроизводит тот же формат, что SQLite писал в TEXT.
    cur.execute('''
        CREATE TABLE IF NOT EXISTS organizations (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('developer', 'contractor')),
            inn TEXT,
            kpp TEXT,
            address TEXT,
            ogrn TEXT,
            okpo TEXT,
            phone TEXT,
            email TEXT,
            rep_position TEXT,
            rep_name TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT,
            email TEXT,
            avatar TEXT,
            is_approved INTEGER DEFAULT 0,
            organization_id INTEGER REFERENCES organizations(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id SERIAL PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            value TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            type TEXT,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS objects (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT,
            type TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            construction_name TEXT,
            construction_address TEXT,
            cadastral_number TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS construction_stages (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            order_num INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            contractor_id INTEGER REFERENCES organizations(id),
            contractor_status TEXT NOT NULL DEFAULT 'search' CHECK(contractor_status IN ('search', 'assigned')),
            plan_start_date TEXT,
            plan_end_date TEXT,
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned', 'in_progress', 'done', 'suspended')),
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            contract_number TEXT,
            contract_date TEXT,
            contract_amount REAL
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS stage_documents (
            id SERIAL PRIMARY KEY,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            doc_type TEXT NOT NULL DEFAULT 'other' CHECK(doc_type IN ('contract', 'tech_spec', 'price_doc', 'work_schedule', 'other')),
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS substages (
            id SERIAL PRIMARY KEY,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT,
            volume REAL,
            unit TEXT,
            unit_price REAL,
            total_price REAL,
            plan_end_date TEXT,
            status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN ('not_started', 'in_progress', 'done', 'closed', 'approved')),
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            completed_at TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS substage_photos (
            id SERIAL PRIMARY KEY,
            substage_id INTEGER NOT NULL REFERENCES substages(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS doc_packages (
            id SERIAL PRIMARY KEY,
            substage_id INTEGER REFERENCES substages(id),
            stage_id INTEGER REFERENCES construction_stages(id),
            contractor_id INTEGER REFERENCES organizations(id),
            created_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'in_review', 'returned', 'approved', 'completed')),
            return_to_role TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            submitted_at TEXT,
            completed_at TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS package_items (
            id SERIAL PRIMARY KEY,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            substage_id INTEGER NOT NULL REFERENCES substages(id) ON DELETE CASCADE,
            qty NUMERIC,
            unit_price NUMERIC,
            amount NUMERIC,
            UNIQUE(package_id, substage_id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS package_documents (
            id SERIAL PRIMARY KEY,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            doc_type TEXT NOT NULL DEFAULT 'free_form' CHECK(doc_type IN ('ks2', 'ks3', 'invoice', 'raw_material_report', 'free_form')),
            title TEXT NOT NULL,
            filename TEXT,
            is_generated INTEGER NOT NULL DEFAULT 0,
            data_json TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS approval_steps (
            id SERIAL PRIMARY KEY,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting', 'pending', 'approved', 'returned')),
            approver_id INTEGER REFERENCES users(id),
            comment TEXT,
            acted_at TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS defect_types (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            order_num INTEGER NOT NULL DEFAULT 0
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS defects (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id),
            stage_id INTEGER REFERENCES construction_stages(id),
            substage_id INTEGER REFERENCES substages(id),
            title TEXT NOT NULL,
            description TEXT,
            type_id INTEGER REFERENCES defect_types(id),
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high', 'critical')),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'resolved', 'verified', 'rejected', 'closed')),
            reporter_id INTEGER NOT NULL REFERENCES users(id),
            responsible_id INTEGER REFERENCES users(id),
            contractor_id INTEGER REFERENCES organizations(id),
            due_date TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            resolved_at TEXT,
            verified_at TEXT,
            reopen_count INTEGER NOT NULL DEFAULT 0,
            plan_id INTEGER,
            pin_x REAL,
            pin_y REAL
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS defect_photos (
            id SERIAL PRIMARY KEY,
            defect_id INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            photo_type TEXT NOT NULL DEFAULT 'general' CHECK(photo_type IN ('before', 'after', 'general')),
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS defect_history (
            id SERIAL PRIMARY KEY,
            defect_id INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            comment TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS defect_audio (
            id SERIAL PRIMARY KEY,
            defect_id INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS journal_entries (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            author_id INTEGER NOT NULL REFERENCES users(id),
            entry_date TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            weather TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            work_type TEXT,
            contractor_id INTEGER REFERENCES organizations(id)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS journal_photos (
            id SERIAL PRIMARY KEY,
            entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS object_plans (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            level_label TEXT,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT 'image',
            sort_order INTEGER NOT NULL DEFAULT 0,
            uploaded_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS material_requests (
            id SERIAL PRIMARY KEY,
            stage_id INTEGER NOT NULL REFERENCES construction_stages(id),
            substage_id INTEGER REFERENCES substages(id),
            contractor_id INTEGER REFERENCES organizations(id),
            requested_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted', 'returned', 'approved', 'processing', 'completed')),
            route_role TEXT NOT NULL DEFAULT 'pto' CHECK(route_role IN ('pto', 'supply')),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            completed_at TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS material_request_items (
            id SERIAL PRIMARY KEY,
            request_id INTEGER NOT NULL REFERENCES material_requests(id) ON DELETE CASCADE,
            material_name TEXT NOT NULL,
            unit TEXT,
            quantity REAL,
            price REAL,
            comment TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS material_request_history (
            id SERIAL PRIMARY KEY,
            request_id INTEGER NOT NULL REFERENCES material_requests(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            comment TEXT,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS guest_tokens (
            id SERIAL PRIMARY KEY,
            object_id INTEGER,
            token TEXT UNIQUE NOT NULL,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            endpoint TEXT NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(user_id, endpoint)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS smeta_imports (
            id           SERIAL PRIMARY KEY,
            stage_id     INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            filename     TEXT,
            source_type  TEXT CHECK(source_type IN ('xlsx','csv','pdf')),
            status       TEXT DEFAULT 'parsed' CHECK(status IN ('parsed','confirmed','failed')),
            rows_json    TEXT,
            uploaded_by  INTEGER REFERENCES users(id),
            created_at   TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            confirmed_at TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS id_item_types (
            id        SERIAL PRIMARY KEY,
            name      TEXT NOT NULL UNIQUE,
            order_num INTEGER NOT NULL DEFAULT 0
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS id_checklist_items (
            id         SERIAL PRIMARY KEY,
            stage_id   INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            type_id    INTEGER REFERENCES id_item_types(id),
            title      TEXT NOT NULL,
            is_required INTEGER NOT NULL DEFAULT 1,
            order_num  INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS id_documents (
            id          SERIAL PRIMARY KEY,
            item_id     INTEGER NOT NULL REFERENCES id_checklist_items(id) ON DELETE CASCADE,
            filename    TEXT NOT NULL,
            original_name TEXT,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS id_packages (
            id            SERIAL PRIMARY KEY,
            stage_id      INTEGER NOT NULL REFERENCES construction_stages(id) ON DELETE CASCADE,
            contractor_id INTEGER REFERENCES organizations(id),
            created_by    INTEGER NOT NULL REFERENCES users(id),
            status        TEXT NOT NULL DEFAULT 'draft'
                          CHECK(status IN ('draft','in_review','returned','accepted')),
            return_to_role TEXT,
            created_at    TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            submitted_at  TEXT,
            accepted_at   TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS id_approval_steps (
            id          SERIAL PRIMARY KEY,
            package_id  INTEGER NOT NULL REFERENCES id_packages(id) ON DELETE CASCADE,
            step_order  INTEGER NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('inspector','pto','manager')),
            status      TEXT NOT NULL DEFAULT 'waiting'
                        CHECK(status IN ('waiting','pending','approved','returned')),
            approver_id INTEGER REFERENCES users(id),
            comment     TEXT,
            acted_at    TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS object_team (
            id         SERIAL PRIMARY KEY,
            object_id  INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            role       TEXT NOT NULL,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            UNIQUE(object_id, role)
        )
    ''')

    conn.commit()
    cur.close()

    run_migrations(conn)
    _seed(conn)
    _seed_id_item_types(conn)
    conn.close()


def _seed(conn):
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users')
    if cur.fetchone()[0] > 0:
        cur.close()
        return

    from werkzeug.security import generate_password_hash

    cur.execute(
        "INSERT INTO organizations (name, type, inn) VALUES (%s, 'developer', '5900000001')",
        ('ГК Федерация',))
    cur.execute(
        "INSERT INTO organizations (name, type, inn) VALUES (%s, 'contractor', '5900000002')",
        ('СтройМонтаж',))

    cur.execute("SELECT id FROM organizations WHERE type='developer'")
    dev_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM organizations WHERE type='contractor'")
    con_id = cur.fetchone()[0]

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
        cur.execute(
            'INSERT INTO users (username, password_hash, role, full_name, is_approved, organization_id) '
            'VALUES (%s, %s, %s, %s, 1, %s)',
            (username, generate_password_hash(password), role, full_name, org_id),
        )

    cur.execute("SELECT id FROM users WHERE username='manager'")
    manager_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM users WHERE username='contractor'")
    contractor_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM users WHERE username='inspector'")
    inspector_id = cur.fetchone()[0]

    defect_types = [
        'Отделка', 'Электрика', 'Сантехника', 'Окна/двери',
        'Стены/потолки', 'Полы', 'Кровля', 'Фасад', 'Прочее',
    ]
    for i, dt in enumerate(defect_types, 1):
        cur.execute('INSERT INTO defect_types (name, order_num) VALUES (%s, %s)', (dt, i))

    # Настройка НДС по умолчанию
    cur.execute(
        "INSERT INTO settings (key, value) VALUES ('vat_rate', '20') ON CONFLICT (key) DO NOTHING")

    test_notifications = [
        (manager_id,    'system',   'Добро пожаловать в ШТАБ', 'Система готова к работе. Создайте первый объект.', '/dashboard'),
        (manager_id,    'approval', 'Новый пакет на согласование', 'Подрядчик «СтройМонтаж» отправил пакет документов.', '/dashboard'),
        (contractor_id, 'defect',   'Новое замечание', 'Технадзор выявил замечание: трещина в перекрытии 3-го этажа.', '/dashboard'),
        (inspector_id,  'system',   'Добро пожаловать в ШТАБ', 'Вы назначены технадзором. Проверяйте ход работ.', '/dashboard'),
    ]
    for uid, ntype, title, body, link in test_notifications:
        cur.execute(
            'INSERT INTO notifications (user_id, type, title, body, link) VALUES (%s, %s, %s, %s, %s)',
            (uid, ntype, title, body, link),
        )

    conn.commit()
    cur.close()


def _seed_id_item_types(conn):
    """Идемпотентный сид справочника типов ИД."""
    types = [
        'Реестр исполнительной документации',
        'Акт освидетельствования скрытых работ (АОСР)',
        'Акт освидетельствования ответственных конструкций',
        'Акт освидетельствования участков сетей инженерно-технического обеспечения',
        'Исполнительная геодезическая схема',
        'Исполнительный чертёж',
        'Акт приёмки геодезической разбивочной основы',
        'Протокол лабораторных испытаний',
        'Паспорт качества на материалы/изделия',
        'Сертификат соответствия на материалы',
        'Общий журнал работ',
        'Специальный журнал работ',
        'Журнал входного контроля материалов',
        'Схема операционного контроля качества',
        'Акт испытания (трубопроводов/систем/конструкций)',
        'Ведомость смонтированного оборудования',
        'Документы о качестве (накладные)',
        'Прочее',
    ]
    cur = conn.cursor()
    for i, name in enumerate(types, 1):
        cur.execute(
            'INSERT INTO id_item_types (name, order_num) VALUES (%s, %s) '
            'ON CONFLICT (name) DO NOTHING',
            (name, i),
        )
    conn.commit()
    cur.close()


def run_migrations(conn):
    """Идемпотентные миграции через information_schema (не PRAGMA)."""
    cur = conn.cursor()

    def _col_exists(table, col):
        cur.execute(
            'SELECT 1 FROM information_schema.columns '
            'WHERE table_name = %s AND column_name = %s',
            (table, col))
        return cur.fetchone() is not None

    # Все колонки уже включены в CREATE TABLE выше (init_db написан финально).
    # Этот блок остаётся для деплоя на существующую Postgres БД,
    # где таблицы могут быть созданы старой версией без этих колонок.

    for col, typedef in [
        ('address', 'TEXT'), ('ogrn', 'TEXT'), ('okpo', 'TEXT'),
        ('phone', 'TEXT'), ('rep_position', 'TEXT'), ('rep_name', 'TEXT'),
        ('email', 'TEXT'),
    ]:
        if not _col_exists('organizations', col):
            cur.execute(f'ALTER TABLE organizations ADD COLUMN {col} {typedef}')

    for col, typedef in [
        ('construction_name', 'TEXT'), ('construction_address', 'TEXT'),
        ('cadastral_number', 'TEXT'),
    ]:
        if not _col_exists('objects', col):
            cur.execute(f'ALTER TABLE objects ADD COLUMN {col} {typedef}')

    for col, typedef in [
        ('contract_number', 'TEXT'), ('contract_date', 'TEXT'),
        ('contract_amount', 'REAL'),
    ]:
        if not _col_exists('construction_stages', col):
            cur.execute(f'ALTER TABLE construction_stages ADD COLUMN {col} {typedef}')

    for col, typedef in [('plan_id', 'INTEGER'), ('pin_x', 'REAL'), ('pin_y', 'REAL')]:
        if not _col_exists('defects', col):
            cur.execute(f'ALTER TABLE defects ADD COLUMN {col} {typedef}')

    for col, typedef in [('work_type', 'TEXT'), ('contractor_id', 'INTEGER')]:
        if not _col_exists('journal_entries', col):
            cur.execute(f'ALTER TABLE journal_entries ADD COLUMN {col} {typedef}')

    # Онбординг: email пользователя (опциональный, при регистрации по коду)
    if not _col_exists('users', 'email'):
        cur.execute('ALTER TABLE users ADD COLUMN email TEXT')

    # Многострочные пакеты (процентовка): stage_id + package_items
    if not _col_exists('doc_packages', 'stage_id'):
        cur.execute('ALTER TABLE doc_packages ADD COLUMN stage_id INTEGER REFERENCES construction_stages(id)')
        cur.execute('ALTER TABLE doc_packages ALTER COLUMN substage_id DROP NOT NULL')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS package_items (
            id SERIAL PRIMARY KEY,
            package_id INTEGER NOT NULL REFERENCES doc_packages(id) ON DELETE CASCADE,
            substage_id INTEGER NOT NULL REFERENCES substages(id) ON DELETE CASCADE,
            qty NUMERIC,
            unit_price NUMERIC,
            amount NUMERIC,
            UNIQUE(package_id, substage_id)
        )
    ''')
    # Бэкфилл: legacy-пакеты (substage_id, без stage_id) → stage_id + одна строка item на полный объём
    cur.execute('''
        UPDATE doc_packages dp SET stage_id = ss.stage_id
        FROM substages ss
        WHERE dp.substage_id = ss.id AND dp.stage_id IS NULL
    ''')
    cur.execute('''
        INSERT INTO package_items (package_id, substage_id, qty, unit_price, amount)
        SELECT dp.id, dp.substage_id, ss.volume, ss.unit_price, ss.total_price
        FROM doc_packages dp
        JOIN substages ss ON dp.substage_id = ss.id
        WHERE NOT EXISTS (SELECT 1 FROM package_items pi WHERE pi.package_id = dp.id)
        ON CONFLICT (package_id, substage_id) DO NOTHING
    ''')

    # ГПР (график производства работ): даты план/факт
    for col in ('plan_start_date', 'actual_start_date', 'actual_end_date'):
        if not _col_exists('substages', col):
            cur.execute(f'ALTER TABLE substages ADD COLUMN {col} TEXT')
    for col in ('actual_start_date', 'actual_end_date'):
        if not _col_exists('construction_stages', col):
            cur.execute(f'ALTER TABLE construction_stages ADD COLUMN {col} TEXT')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS schedule_baselines (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            data_json TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS schedule_milestones (
            id SERIAL PRIMARY KEY,
            object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            plan_date TEXT,
            actual_date TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'done', 'missed')),
            order_num INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # Бэкфилл факт-дат из существующих статусов (только пустые):
    # завершённые подэтапы: факт-финиш = completed_at
    cur.execute('''
        UPDATE substages SET actual_end_date = LEFT(completed_at, 10)
        WHERE actual_end_date IS NULL AND completed_at IS NOT NULL
          AND status IN ('done', 'closed', 'approved')
    ''')
    # завершённые без completed_at (данные до внедрения): факт-финиш ≈ плановый
    cur.execute('''
        UPDATE substages SET actual_end_date = plan_end_date
        WHERE actual_end_date IS NULL AND plan_end_date IS NOT NULL
          AND status IN ('done', 'closed', 'approved')
    ''')
    # подэтапы в работе или завершённые без факт-старта: берём плановый старт,
    # иначе плановый финиш, иначе дату завершения
    cur.execute('''
        UPDATE substages SET actual_start_date =
            LEAST(COALESCE(plan_start_date, actual_end_date, plan_end_date),
                  to_char(now(), 'YYYY-MM-DD'))
        WHERE actual_start_date IS NULL
          AND status IN ('in_progress', 'done', 'closed', 'approved')
    ''')
    # факт этапа из подэтапов
    cur.execute('''
        UPDATE construction_stages cs SET actual_start_date = sub.min_start
        FROM (SELECT stage_id, MIN(actual_start_date) as min_start FROM substages
              WHERE actual_start_date IS NOT NULL GROUP BY stage_id) sub
        WHERE cs.id = sub.stage_id AND cs.actual_start_date IS NULL
    ''')
    cur.execute('''
        UPDATE construction_stages cs SET actual_end_date = sub.max_end
        FROM (SELECT stage_id, MAX(actual_end_date) as max_end FROM substages
              WHERE actual_end_date IS NOT NULL GROUP BY stage_id) sub
        WHERE cs.id = sub.stage_id AND cs.actual_end_date IS NULL AND cs.status = 'done'
    ''')

    # Онбординг подрядчиков: какой тенант завёл организацию
    if not _col_exists('organizations', 'created_by_org'):
        cur.execute('ALTER TABLE organizations ADD COLUMN created_by_org INTEGER REFERENCES organizations(id)')

    # Мультитенантность: тенант объекта
    if not _col_exists('objects', 'developer_id'):
        cur.execute('ALTER TABLE objects ADD COLUMN developer_id INTEGER REFERENCES organizations(id)')

    # Мультитенантность: код приглашения и статус организации
    if not _col_exists('organizations', 'join_code'):
        cur.execute('ALTER TABLE organizations ADD COLUMN join_code TEXT')
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_org_join_code ON organizations(join_code) WHERE join_code IS NOT NULL')
    if not _col_exists('organizations', 'status'):
        cur.execute("ALTER TABLE organizations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

    # Генерируем join_code для организаций, у которых его нет
    import secrets
    cur.execute('SELECT id FROM organizations WHERE join_code IS NULL')
    orgs_without_code = cur.fetchall()
    for (org_id,) in orgs_without_code:
        for _ in range(10):
            code = secrets.token_urlsafe(6)[:8].upper()
            cur.execute('SELECT 1 FROM organizations WHERE join_code = %s', (code,))
            if not cur.fetchone():
                cur.execute('UPDATE organizations SET join_code = %s WHERE id = %s', (code, org_id))
                break

    # Настройка НДС (идемпотентно)
    cur.execute(
        "INSERT INTO settings (key, value) VALUES ('vat_rate', '20') ON CONFLICT (key) DO NOTHING")

    conn.commit()
    cur.close()


for folder in (config.UPLOAD_FOLDER, config.DOCS_FOLDER, config.AVATARS_FOLDER, config.DEFECTS_FOLDER, config.PACKAGES_FOLDER, config.PLANS_FOLDER, config.JOURNAL_FOLDER, config.ID_DOCS_FOLDER):
    os.makedirs(folder, exist_ok=True)

app = create_app()

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
