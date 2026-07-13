import os
import uuid
from functools import wraps
from flask import abort
from flask_login import current_user
from werkzeug.utils import secure_filename

import config

ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO = {'mp4', 'mov', 'avi', 'mkv', 'webm'}
ALLOWED_AUDIO = {'webm', 'ogg', 'mp3', 'wav', 'm4a', 'mp4'}
ALLOWED_DOC = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar'}


def accessible_object_ids(user):
    """Returns list of object IDs the user can access based on tenant rules.
    - admin → all
    - contractor → objects where their org is contractor on ≥1 stage
    - everyone else → objects where developer_id = their org (or all if no org yet)
    """
    from db import query_db
    if user.role == 'admin':
        rows = query_db('SELECT id FROM objects')
        return [r['id'] for r in rows]
    if user.role == 'contractor':
        if not user.organization_id:
            return []
        rows = query_db(
            'SELECT DISTINCT object_id FROM construction_stages WHERE contractor_id = ?',
            (user.organization_id,))
        return [r['object_id'] for r in rows]
    # Developer-side roles: manager, pto, inspector, foreman, supply, accountant
    if user.organization_id:
        rows = query_db('SELECT id FROM objects WHERE developer_id = ?', (user.organization_id,))
        return [r['id'] for r in rows]
    # No org yet (shouldn't happen for approved users) → no access
    return []


def assert_object_access(user, object_id):
    """Aborts 403 if user cannot access the given object."""
    from flask import abort
    from db import query_db
    if user.role == 'admin':
        return
    if user.role == 'contractor':
        if not user.organization_id:
            abort(403)
        row = query_db(
            'SELECT 1 FROM construction_stages WHERE object_id = ? AND contractor_id = ?',
            (object_id, user.organization_id), one=True)
    elif user.organization_id:
        row = query_db(
            'SELECT 1 FROM objects WHERE id = ? AND developer_id = ?',
            (object_id, user.organization_id), one=True)
    else:
        row = None
    if not row:
        abort(403)


TEAM_ROLES = [
    ('inspector',  'Технадзор'),
    ('pto',        'Инженер ПТО'),
    ('foreman',    'Прораб'),
    ('manager',    'Руководитель'),
    ('accountant', 'Бухгалтер'),
    ('supply',     'Снабженец'),
]


# ═══ Настройки тенанта ═══════════════════════════════════════════════════
# Дефолты = текущее поведение системы. Ненастроенный тенант работает как раньше.

import json as _json

# Допустимые роли цепочек согласования (фиксированный набор)
CHAIN_ROLES = {
    'inspector':  'Технадзор',
    'foreman':    'Прораб',
    'pto':        'Инженер ПТО',
    'manager':    'Руководитель',
    'accountant': 'Бухгалтер',
}

TENANT_DEFAULTS = {
    # цепочки согласования: JSON-списки ролей; дефолт = константы config
    'approval_chain_ks': _json.dumps(['inspector', 'foreman', 'pto', 'manager', 'accountant']),
    'approval_chain_id': _json.dumps(['inspector', 'pto', 'manager']),
}


NOTIFY_CHANNELS = ['in_app', 'email', 'push']   # in_app — базовый (запись в БД)
NOTIFY_CHANNEL_LABELS = {'in_app': 'В приложении', 'email': 'Email', 'push': 'Push-уведомления'}
NOTIFY_TYPES = {'approval': 'Согласования', 'defect': 'Замечания', 'supply': 'Снабжение'}

TENANT_DEFAULTS['notify_channels'] = _json.dumps(['in_app', 'email', 'push'])
TENANT_DEFAULTS['notify_types'] = _json.dumps(['approval', 'defect', 'supply'])
TENANT_DEFAULTS['digest_enabled'] = '1'
TENANT_DEFAULTS['digest_weekday'] = '0'   # 0 = понедельник


def get_user_setting(user_id, key, default=None):
    from db import query_db
    row = query_db('SELECT value FROM user_settings WHERE user_id = ? AND key = ?',
                   (user_id, key), one=True)
    return row['value'] if row is not None else default


def set_user_setting(user_id, key, value):
    from db import get_db
    db = get_db()
    db.execute(
        'INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) '
        'ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value',
        (user_id, key, str(value) if value is not None else None))
    db.commit()


def _json_list(raw, fallback):
    try:
        v = _json.loads(raw)
        return v if isinstance(v, list) else fallback
    except (ValueError, TypeError):
        return fallback


def recipient_channels_and_types(user_id):
    """Для notify(): (активные_каналы:set, разрешённые_типы:set) получателя.

    Каналы = пересечение(каналы тенанта, каналы пользователя); email доступен
    только при настроенном SMTP. Типы — роутинг тенанта (для developer-ролей).
    Дефолты = всё включено (текущее поведение)."""
    import config
    from db import query_db
    u = query_db('SELECT role, organization_id FROM users WHERE id = ?', (user_id,), one=True)
    if not u:
        return {'in_app'}, set(NOTIFY_TYPES)
    tenant = u['organization_id'] if u['role'] not in ('admin', 'contractor') else None

    tenant_channels = set(_json_list(get_tenant_setting(tenant, 'notify_channels'),
                                     ['in_app', 'email', 'push']))
    allowed_types = set(_json_list(get_tenant_setting(tenant, 'notify_types'),
                                   ['approval', 'defect', 'supply']))

    # пользовательские каналы: in_app всегда доступен, email/push — по флагу (дефолт вкл)
    user_channels = {'in_app'}
    if get_user_setting(user_id, 'channel_email', '1') == '1':
        user_channels.add('email')
    if get_user_setting(user_id, 'channel_push', '1') == '1':
        user_channels.add('push')

    channels = tenant_channels & user_channels
    if 'email' in channels and not config.email_enabled():
        channels.discard('email')
    return channels, allowed_types


def _parse_chain(raw, default_key):
    """JSON-список ролей → [(role, label)]. Невалидное → дефолт."""
    try:
        roles = _json.loads(raw)
        assert isinstance(roles, list) and roles
        assert all(r in CHAIN_ROLES for r in roles)
        assert len(set(roles)) == len(roles)
    except (ValueError, TypeError, AssertionError):
        roles = _json.loads(TENANT_DEFAULTS[default_key])
    return [(r, CHAIN_ROLES[r]) for r in roles]


# Переключатели видимости модулей: наборы разрешённых ролей по тенанту.
# Дефолт = текущее поведение. Значения в tenant_settings — JSON-список ролей.
MODULE_ACCESS = {
    # module: (settings_key, default_roles)
    'gpr':     ('access_gpr',     ['manager', 'pto', 'inspector', 'foreman', 'accountant', 'supply', 'admin']),
    'finance': ('access_finance', ['manager', 'pto', 'accountant', 'admin']),
    'digest':  ('access_digest',  ['manager', 'pto', 'admin']),
}


def _access_roles(tenant_org_id, module):
    key, default = MODULE_ACCESS[module]
    raw = get_tenant_setting(tenant_org_id, key)
    if raw:
        try:
            roles = _json.loads(raw)
            if isinstance(roles, list):
                return set(roles)
        except (ValueError, TypeError):
            pass
    return set(default)


def _tenant_of(user):
    """Тенант-организация пользователя (для developer-ролей). admin/contractor → None."""
    if user.role in ('admin', 'contractor') or not user.organization_id:
        return None
    return user.organization_id


def can_access(user, module):
    """Разрешён ли пользователю модуль по настройке его тенанта.
    admin — всегда да. НЕ заменяет assert_object_access: тенант-скоуп поверх."""
    if user.role == 'admin':
        return True
    return user.role in _access_roles(_tenant_of(user), module)


def can_access_for_object(user, module, object_id):
    """Доступ к модулю в контексте конкретного объекта: настройка берётся
    от ТЕНАНТА ОБЪЕКТА (developer_id), роль пользователя проверяется по ней.
    Используется, когда пользователь может быть вне тенанта объекта
    (contractor) — но тенант-скоуп (assert_object_access) всё равно обязателен."""
    if user.role == 'admin':
        return True
    from db import query_db
    row = query_db('SELECT developer_id FROM objects WHERE id = ?', (object_id,), one=True)
    dev_id = row['developer_id'] if row else None
    return user.role in _access_roles(dev_id, module)


def can_see_finance(user):
    """Видит ли пользователь финансовые данные (расценки, суммы, деньги).
    Contractor всегда видит свои деньги (это его договорные суммы)."""
    if user.role in ('admin', 'contractor'):
        return True
    return user.role in _access_roles(_tenant_of(user), 'finance')


def get_chain_for_object(object_id, kind='ks'):
    """Цепочка согласования для объекта: настройка ТЕНАНТА ОБЪЕКТА
    (organizations застройщика через objects.developer_id) или дефолт.

    kind: 'ks' (пакеты КС) | 'id' (исполнительная документация).
    Возвращает [(role, label), ...]. Применяется ТОЛЬКО при создании
    новых пакетов — активные идут по шагам, уже записанным в БД."""
    from db import query_db
    key = f'approval_chain_{kind}'
    row = query_db('SELECT developer_id FROM objects WHERE id = ?', (object_id,), one=True)
    dev_id = row['developer_id'] if row else None
    return _parse_chain(get_tenant_setting(dev_id, key), key)


def get_tenant_setting(org_id, key, default=None):
    """Настройка тенанта (организации-застройщика).
    Не задана или нет org_id → default, иначе TENANT_DEFAULTS[key]."""
    fallback = default if default is not None else TENANT_DEFAULTS.get(key)
    if not org_id:
        return fallback
    from db import query_db
    row = query_db('SELECT value FROM tenant_settings WHERE organization_id = ? AND key = ?',
                   (org_id, key), one=True)
    return row['value'] if row is not None else fallback


def set_tenant_setting(org_id, key, value):
    from db import get_db
    db = get_db()
    db.execute(
        'INSERT INTO tenant_settings (organization_id, key, value) VALUES (?, ?, ?) '
        'ON CONFLICT (organization_id, key) DO UPDATE SET value = EXCLUDED.value',
        (org_id, key, str(value) if value is not None else None))
    db.commit()


def current_tenant_setting(user, key, default=None):
    """Настройка тенанта текущего пользователя.

    - developer-роли: их organization_id и есть тенант
    - admin: платформенный дефолт (у админа нет тенанта)
    - contractor: настройки его собственной орг не тенантные — тоже дефолт;
      контекстные чтения «от тенанта объекта» делаются напрямую через
      get_tenant_setting(developer_id, ...)"""
    if user.role == 'admin' or user.role == 'contractor' or not user.organization_id:
        return default if default is not None else TENANT_DEFAULTS.get(key)
    return get_tenant_setting(user.organization_id, key, default)


def recalc_stage_actuals(stage_id):
    """Пересчитывает факт-даты этапа из его подэтапов.

    actual_start = минимальный actual_start_date подэтапов;
    actual_end   = максимальный actual_end_date, только если этап в 'done'
    (иначе очищается — этап ещё идёт)."""
    from db import get_db, query_db
    db = get_db()
    row = query_db(
        'SELECT MIN(actual_start_date) as s, MAX(actual_end_date) as e '
        'FROM substages WHERE stage_id = ?', (stage_id,), one=True)
    stage = query_db('SELECT status FROM construction_stages WHERE id = ?', (stage_id,), one=True)
    if not stage:
        return
    actual_end = row['e'] if stage['status'] == 'done' else None
    db.execute('UPDATE construction_stages SET actual_start_date = ?, actual_end_date = ? WHERE id = ?',
               (row['s'], actual_end, stage_id))
    db.commit()


def linked_contractor_org_ids(tenant_org_id):
    """Подрядные организации, связанные с тенантом: созданные им
    или работающие на этапах его объектов."""
    from db import query_db
    rows = query_db(
        "SELECT id FROM organizations WHERE type = 'contractor' AND created_by_org = ? "
        "UNION "
        "SELECT DISTINCT cs.contractor_id FROM construction_stages cs "
        "JOIN objects ob ON cs.object_id = ob.id "
        "WHERE ob.developer_id = ? AND cs.contractor_id IS NOT NULL",
        (tenant_org_id, tenant_org_id))
    return [r['id'] for r in rows]


def get_object_team(object_id):
    """Returns {role: {'id': user_id, 'full_name': ..., 'user_role': role}} for the object."""
    from db import query_db
    rows = query_db(
        'SELECT ot.role, u.id, u.full_name, u.role as user_role '
        'FROM object_team ot JOIN users u ON ot.user_id = u.id '
        'WHERE ot.object_id = ?', (object_id,))
    return {r['role']: dict(r) for r in rows}


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def _save_file(file, folder, allowed_ext):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_ext:
        return None
    filename = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    return filename


def save_photo(file):
    return _save_file(file, config.UPLOAD_FOLDER, ALLOWED_IMG)


def save_document(file):
    return _save_file(file, config.DOCS_FOLDER, ALLOWED_DOC | ALLOWED_IMG)


def save_avatar(file):
    return _save_file(file, config.AVATARS_FOLDER, ALLOWED_IMG)


def save_stage_document(file, stage_id):
    folder = os.path.join(config.DOCS_FOLDER, str(stage_id))
    return _save_file(file, folder, ALLOWED_DOC | ALLOWED_IMG)


def save_substage_photo(file, substage_id):
    folder = os.path.join(config.UPLOAD_FOLDER, 'substages', str(substage_id))
    return _save_file(file, folder, ALLOWED_IMG)


def save_journal_photo(file, entry_id):
    folder = os.path.join(config.JOURNAL_FOLDER, str(entry_id))
    return _save_file(file, folder, ALLOWED_IMG | ALLOWED_VIDEO)


def save_plan_file(file, object_id):
    folder = os.path.join(config.PLANS_FOLDER, str(object_id))
    return _save_file(file, folder, ALLOWED_IMG)


def save_org_logo(file, org_id):
    folder = os.path.join(config.LOGOS_FOLDER, str(org_id))
    return _save_file(file, folder, ALLOWED_IMG)


def save_defect_photo(file, defect_id):
    folder = os.path.join(config.DEFECTS_FOLDER, str(defect_id))
    return _save_file(file, folder, ALLOWED_IMG | ALLOWED_VIDEO)


def save_defect_audio(file, defect_id):
    folder = os.path.join(config.DEFECTS_FOLDER, str(defect_id))
    return _save_file(file, folder, ALLOWED_AUDIO)


def save_package_document(file, package_id):
    folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
    return _save_file(file, folder, ALLOWED_DOC | ALLOWED_IMG)
