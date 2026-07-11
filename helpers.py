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


def save_defect_photo(file, defect_id):
    folder = os.path.join(config.DEFECTS_FOLDER, str(defect_id))
    return _save_file(file, folder, ALLOWED_IMG | ALLOWED_VIDEO)


def save_defect_audio(file, defect_id):
    folder = os.path.join(config.DEFECTS_FOLDER, str(defect_id))
    return _save_file(file, folder, ALLOWED_AUDIO)


def save_package_document(file, package_id):
    folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
    return _save_file(file, folder, ALLOWED_DOC | ALLOWED_IMG)
