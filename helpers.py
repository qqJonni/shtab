import os
import uuid
from functools import wraps
from flask import abort
from flask_login import current_user
from werkzeug.utils import secure_filename

import config

ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_DOC = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip', 'rar'}


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


def save_defect_photo(file, defect_id):
    folder = os.path.join(config.DEFECTS_FOLDER, str(defect_id))
    return _save_file(file, folder, ALLOWED_IMG)
