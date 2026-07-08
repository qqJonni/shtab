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
