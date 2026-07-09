"""
seed_multitenant_test.py — тестовый сид для проверки изоляции мультитенантности.

ТОЛЬКО ДЛЯ РАЗРАБОТКИ. Не запускать на проде.

Создаёт:
  - 2 застройщика (developer-организации) с полными командами
  - 1 подрядчика (contractor-org), назначенного на этапы у ОБОИХ застройщиков
  - По одному объекту с этапами/подэтапами у каждого застройщика
  - Назначенные команды объектов (object_team)

Запуск:
    python3 seed_multitenant_test.py
    python3 seed_multitenant_test.py --wipe   # удалить сид-данные перед пересозданием
"""

import sys
import secrets
import psycopg2
from werkzeug.security import generate_password_hash

import config  # DATABASE_URL, TEAM_ROLES и т.п.

# ─── константы сида ──────────────────────────────────────────────────────────
SEED_MARKER = '[SEED]'   # маркер в full_name — по нему можно очистить сид

TENANTS = [
    {
        'org_name': 'ГК Альфа-Строй',
        'join_code': 'ALPHA001',
        'object_name': 'ЖК «Альфа Парк»',
        'prefix': 'alpha',
        'users': [
            ('manager',    'Смирнов Алексей Петрович'),
            ('pto',        'Козлова Наталья Ивановна'),
            ('inspector',  'Фёдоров Дмитрий Сергеевич'),
            ('foreman',    'Николаев Павел Андреевич'),
            ('supply',     'Орлова Светлана Михайловна'),
            ('accountant', 'Беляева Ирина Юрьевна'),
        ],
    },
    {
        'org_name': 'Девелопмент Бета',
        'join_code': 'BETA0002',
        'object_name': 'БЦ «Бета Плаза»',
        'prefix': 'beta',
        'users': [
            ('manager',    'Захаров Виктор Николаевич'),
            ('pto',        'Морозова Анна Александровна'),
            ('inspector',  'Попов Игорь Дмитриевич'),
            ('foreman',    'Соколов Максим Олегович'),
            ('supply',     'Титова Елена Васильевна'),
            ('accountant', 'Громова Юлия Сергеевна'),
        ],
    },
]

CONTRACTOR = {
    'org_name': 'СК «ОбщийПодрядчик»',
    'join_code': 'CONTR001',
    'prefix': 'contr',
    'users': [
        ('contractor', 'Петров Сергей Владимирович'),
    ],
}

STAGES = [
    ('Монолитные работы',      'Устройство монолитных конструкций'),
    ('Кирпичная кладка',       'Кладка наружных и внутренних стен'),
    ('Кровельные работы',      'Устройство кровли и парапетов'),
]

SUBSTAGES_PER_STAGE = [
    ('Разметка и опалубка',  None),
    ('Армирование',          None),
    ('Бетонирование',        None),
]

PASSWORD = 'Test1234!'   # единый пароль для всех сид-пользователей


# ─── helpers ─────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _insert(cur, table, data: dict) -> int:
    cols = ', '.join(data.keys())
    placeholders = ', '.join(['%s'] * len(data))
    cur.execute(
        f'INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id',
        list(data.values())
    )
    return cur.fetchone()[0]


def _gen_code(cur, base_code):
    """Use base_code if free, otherwise append random suffix."""
    cur.execute('SELECT 1 FROM organizations WHERE join_code = %s', (base_code,))
    if not cur.fetchone():
        return base_code
    for _ in range(20):
        code = secrets.token_urlsafe(6)[:8].upper()
        cur.execute('SELECT 1 FROM organizations WHERE join_code = %s', (code,))
        if not cur.fetchone():
            return code
    raise RuntimeError('Cannot generate unique join_code')


# ─── wipe ────────────────────────────────────────────────────────────────────

def wipe_seed(cur):
    cur.execute("SELECT id FROM users WHERE full_name LIKE %s", (f'%{SEED_MARKER}%',))
    user_ids = [r[0] for r in cur.fetchall()]
    if not user_ids:
        print('Сид-данные не найдены, нечего удалять.')
        return

    # 1. objects first (CASCADE removes stages, substages, object_team rows)
    cur.execute(
        "DELETE FROM objects WHERE name IN %s",
        (tuple([t['object_name'] for t in TENANTS]),)
    )
    # 2. now safe to remove users
    for uid in user_ids:
        cur.execute('DELETE FROM notifications WHERE user_id = %s', (uid,))
        cur.execute('DELETE FROM object_team WHERE user_id = %s', (uid,))
    cur.execute(
        'DELETE FROM users WHERE full_name LIKE %s',
        (f'%{SEED_MARKER}%',)
    )
    # 3. remove seed orgs
    all_codes = tuple([t['join_code'] for t in TENANTS] + [CONTRACTOR['join_code']])
    cur.execute("DELETE FROM organizations WHERE join_code IN %s", (all_codes,))
    print('Сид-данные удалены.')


# ─── seed ────────────────────────────────────────────────────────────────────

def seed():
    conn = _conn()
    cur = conn.cursor()

    print('\n' + '═' * 60)
    print('  SEED: мультитенантный тест')
    print('═' * 60)

    # ── Подрядчик (общий для обоих тенантов) ──
    code = _gen_code(cur, CONTRACTOR['join_code'])
    contractor_org_id = _insert(cur, 'organizations', {
        'name': CONTRACTOR['org_name'],
        'type': 'contractor',
        'join_code': code,
        'status': 'active',
    })
    contractor_user_ids = {}
    for role, full_name in CONTRACTOR['users']:
        username = f"{CONTRACTOR['prefix']}_{role}"
        cur.execute('SELECT id FROM users WHERE username = %s', (username,))
        if cur.fetchone():
            username += '_seed'
        uid = _insert(cur, 'users', {
            'username':      username,
            'password_hash': generate_password_hash(PASSWORD),
            'role':          role,
            'full_name':     f'{full_name} {SEED_MARKER}',
            'is_approved':   1,
            'organization_id': contractor_org_id,
        })
        contractor_user_ids[role] = uid

    print(f'\n┌─ Подрядчик: {CONTRACTOR["org_name"]} (join_code: {code})')
    for role, full_name in CONTRACTOR['users']:
        print(f'│   {role:12} │ логин: {CONTRACTOR["prefix"]}_{role}  │ пароль: {PASSWORD}')

    # ── Два тенанта ──
    tenant_data = []   # [(org_id, user_map, object_id), ...]

    for t in TENANTS:
        print(f'\n┌─ Тенант: {t["org_name"]} (join_code: {t["join_code"]})')
        code = _gen_code(cur, t['join_code'])

        org_id = _insert(cur, 'organizations', {
            'name':      t['org_name'],
            'type':      'developer',
            'join_code': code,
            'status':    'active',
        })

        user_map = {}   # role → user_id
        for role, full_name in t['users']:
            username = f"{t['prefix']}_{role}"
            cur.execute('SELECT id FROM users WHERE username = %s', (username,))
            if cur.fetchone():
                username += '_seed'
            uid = _insert(cur, 'users', {
                'username':        username,
                'password_hash':   generate_password_hash(PASSWORD),
                'role':            role,
                'full_name':       f'{full_name} {SEED_MARKER}',
                'is_approved':     1,
                'organization_id': org_id,
            })
            user_map[role] = uid
            print(f'│   {role:12} │ логин: {username:<22} │ пароль: {PASSWORD}')

        # ── Объект ──
        manager_uid = user_map.get('manager')
        obj_id = _insert(cur, 'objects', {
            'name':         t['object_name'],
            'address':      f'г. Пермь, ул. Тестовая, {t["prefix"].upper()}',
            'type':         'residential',
            'status':       'active',
            'developer_id': org_id,
            'created_by':   manager_uid,
        })
        print(f'│   Объект: {t["object_name"]} (id={obj_id}, developer_id={org_id})')

        # ── Этапы + подэтапы ──
        for order, (stage_name, stage_desc) in enumerate(STAGES, 1):
            stage_id = _insert(cur, 'construction_stages', {
                'object_id':     obj_id,
                'name':          stage_name,
                'order_num':     order,
                'description':   stage_desc,
                'contractor_id': contractor_org_id,
                'status':        'in_progress',
                'created_by':    manager_uid,
            })
            for ss_name, ss_desc in SUBSTAGES_PER_STAGE:
                _insert(cur, 'substages', {
                    'stage_id':   stage_id,
                    'name':       ss_name,
                    'status':     'not_started',
                    'created_by': manager_uid,
                })

        # ── Команда объекта (object_team) ──
        team_roles = ['inspector', 'pto', 'foreman', 'manager', 'accountant', 'supply']
        for role in team_roles:
            uid = user_map.get(role)
            if uid:
                cur.execute(
                    'INSERT INTO object_team (object_id, role, user_id) VALUES (%s, %s, %s) '
                    'ON CONFLICT (object_id, role) DO UPDATE SET user_id = EXCLUDED.user_id',
                    (obj_id, role, uid)
                )

        tenant_data.append((org_id, user_map, obj_id))

    conn.commit()
    cur.close()
    conn.close()

    # ── Сводная таблица для теста ──
    print('\n' + '═' * 60)
    print('  ГОТОВО. Схема изоляции для ручного теста:')
    print('═' * 60)
    print()
    for i, (t, (org_id, user_map, obj_id)) in enumerate(zip(TENANTS, tenant_data), 1):
        prefix = t['prefix']
        print(f'  Тенант {i}: {t["org_name"]}  (org_id={org_id})')
        print(f'    Объект: {t["object_name"]}  (object_id={obj_id})')
        print(f'    Подрядчик на всех этапах: {CONTRACTOR["org_name"]}')
        print()
        print(f'    {"Роль":<12}  {"Логин":<24}  Пароль')
        print(f'    {"─"*12}  {"─"*24}  {"─"*10}')
        for role, _ in t['users']:
            print(f'    {role:<12}  {prefix + "_" + role:<24}  {PASSWORD}')
        print()

    print(f'  Подрядчик (виден в ОБОИХ тенантах):')
    print(f'    {"Роль":<12}  {"Логин":<24}  Пароль')
    print(f'    {"─"*12}  {"─"*24}  {"─"*10}')
    for role, _ in CONTRACTOR['users']:
        print(f'    {role:<12}  {CONTRACTOR["prefix"] + "_" + role:<24}  {PASSWORD}')

    print()
    print('  ТЕСТ-КЕЙСЫ:')
    print('  1. Войти как alpha_manager → видит только объект ЖК «Альфа Парк»')
    print('  2. Войти как beta_manager  → видит только объект БЦ «Бета Плаза»')
    print('  3. Войти как contr_contractor → видит этапы у ОБОИХ объектов')
    print('  4. alpha_manager → /admin/users → только пользователи ГК Альфа-Строй')
    print('  5. Прямой URL /objects/<beta_object_id> под alpha_manager → 403')
    print('  6. Отправить пакет КС в alpha → approver_id из команды alpha (не beta)')
    print('═' * 60)


# ─── entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if '--wipe' in sys.argv:
        conn = _conn()
        cur = conn.cursor()
        wipe_seed(cur)
        conn.commit()
        cur.close()
        conn.close()
    else:
        seed()
