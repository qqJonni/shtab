import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
DATABASE = os.path.join(BASE_DIR, 'shtab.db')

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

UNITS = ['м2', 'м.пог.', 'шт.', 'м3', 'м.']

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DOCS_FOLDER = os.path.join(BASE_DIR, 'static', 'docs')
AVATARS_FOLDER = os.path.join(BASE_DIR, 'static', 'avatars')
DEFECTS_FOLDER = os.path.join(BASE_DIR, 'static', 'defects')
MAX_CONTENT_LENGTH = 100 * 1024 * 1024
