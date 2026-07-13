import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/shtab')

ROLES = {
    'admin':      'Администратор',
    'manager':    'Руководитель',
    'pto':        'Инженер ПТО',
    'inspector':  'Технадзор',
    'foreman':    'Прораб',
    'supply':     'Снабженец',
    'accountant': 'Бухгалтер',
    'contractor': 'Подрядчик',
    'guest':      'Гость',
}

SELF_REGISTER_ROLES = ['manager', 'pto', 'inspector', 'foreman', 'supply', 'accountant', 'contractor']

ORG_TYPES = {
    'developer':   'Заказчик',
    'contractor':  'Подрядчик',
}

UNITS = ['м2', 'м.пог.', 'шт.', 'м3', 'м.', 'ч.', 'т.', 'кг.']

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DOCS_FOLDER = os.path.join(BASE_DIR, 'static', 'docs')
AVATARS_FOLDER = os.path.join(BASE_DIR, 'static', 'avatars')
DEFECTS_FOLDER = os.path.join(BASE_DIR, 'static', 'defects')
PACKAGES_FOLDER = os.path.join(BASE_DIR, 'static', 'packages')
PLANS_FOLDER = os.path.join(BASE_DIR, 'static', 'plans')
JOURNAL_FOLDER = os.path.join(BASE_DIR, 'static', 'journal')
ID_DOCS_FOLDER = os.path.join(BASE_DIR, 'static', 'id_docs')
LOGOS_FOLDER = os.path.join(BASE_DIR, 'static', 'logos')
MAX_CONTENT_LENGTH = 100 * 1024 * 1024

APPROVAL_CHAIN = [
    ('inspector', 'Технадзор'),
    ('foreman',   'Прораб'),
    ('pto',       'Инженер ПТО'),
    ('manager',   'Руководитель'),
    ('accountant', 'Бухгалтер'),
]

ID_APPROVAL_CHAIN = [
    ('inspector', 'Технадзор'),
    ('pto',       'Инженер ПТО'),
    ('manager',   'Руководитель'),
]
