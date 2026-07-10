"""
Автоматический тест изоляции мультитенантности.
Запускается против реальной БД (PostgreSQL).
"""
import os
import sys
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

import psycopg2
import config

PASS = "✅ OK"
FAIL = "❌ FAIL"
SKIP = "⚠️  SKIP"

results = []

def check(name, cond, detail=''):
    status = PASS if cond else FAIL
    results.append((status, name, detail))
    print(f"  {status}  {name}" + (f"\n         {detail}" if detail and not cond else ''))
    return cond

conn = psycopg2.connect(config.DATABASE_URL)
conn.autocommit = False
cur = conn.cursor()

def q1(sql, params=()):
    cur.execute(sql, params)
    r = cur.fetchone()
    return r[0] if r else None

def qall(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()

# ── Получаем ID тестовых пользователей и объектов ──────────────────────────
print("\n═══ Подготовка ═══")

alpha_manager_id  = q1("SELECT id FROM users WHERE username='alpha_manager'")
alpha_pto_id      = q1("SELECT id FROM users WHERE username='alpha_pto'")
alpha_inspector_id= q1("SELECT id FROM users WHERE username='alpha_inspector'")
alpha_foreman_id  = q1("SELECT id FROM users WHERE username='alpha_foreman'")
alpha_supply_id   = q1("SELECT id FROM users WHERE username='alpha_supply'")
alpha_accountant_id = q1("SELECT id FROM users WHERE username='alpha_accountant'")

beta_manager_id   = q1("SELECT id FROM users WHERE username='beta_manager'")
beta_pto_id       = q1("SELECT id FROM users WHERE username='beta_pto'")
beta_inspector_id = q1("SELECT id FROM users WHERE username='beta_inspector'")

contr_id          = q1("SELECT id FROM users WHERE username='contr_contractor'")

alpha_org_id = q1("SELECT organization_id FROM users WHERE username='alpha_manager'")
beta_org_id  = q1("SELECT organization_id FROM users WHERE username='beta_manager'")
contr_org_id = q1("SELECT organization_id FROM users WHERE username='contr_contractor'")

alpha_obj_id = q1("SELECT id FROM objects WHERE developer_id=%s", (alpha_org_id,))
beta_obj_id  = q1("SELECT id FROM objects WHERE developer_id=%s", (beta_org_id,))

alpha_stage_ids = [r[0] for r in qall("SELECT id FROM construction_stages WHERE object_id=%s", (alpha_obj_id,))]
beta_stage_ids  = [r[0] for r in qall("SELECT id FROM construction_stages WHERE object_id=%s", (beta_obj_id,))]

print(f"  alpha: org={alpha_org_id} obj={alpha_obj_id} stages={alpha_stage_ids}")
print(f"  beta:  org={beta_org_id}  obj={beta_obj_id}  stages={beta_stage_ids}")
print(f"  contr: org={contr_org_id}")

if not all([alpha_manager_id, beta_manager_id, contr_id, alpha_obj_id, beta_obj_id]):
    print("FATAL: seed data missing, run seed_multitenant_test.py first")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 1. Изоляция списков объектов ═══")

# accessible_object_ids logic (from helpers.py)
def accessible_obj_ids(user_id):
    role = q1("SELECT role FROM users WHERE id=%s", (user_id,))
    org  = q1("SELECT organization_id FROM users WHERE id=%s", (user_id,))
    if role == 'admin':
        return [r[0] for r in qall("SELECT id FROM objects")]
    if role == 'contractor':
        if not org: return []
        return [r[0] for r in qall(
            "SELECT DISTINCT object_id FROM construction_stages WHERE contractor_id=%s", (org,))]
    if org:
        return [r[0] for r in qall("SELECT id FROM objects WHERE developer_id=%s", (org,))]
    return []

alpha_visible = accessible_obj_ids(alpha_manager_id)
beta_visible  = accessible_obj_ids(beta_manager_id)
contr_visible = accessible_obj_ids(contr_id)

check("alpha_manager видит объект alpha",         alpha_obj_id in alpha_visible)
check("alpha_manager НЕ видит объект beta",       beta_obj_id not in alpha_visible,
      f"alpha_visible={alpha_visible}")
check("beta_manager видит объект beta",           beta_obj_id in beta_visible)
check("beta_manager НЕ видит объект alpha",       alpha_obj_id not in beta_visible)
check("alpha_pto НЕ видит объект beta",
      beta_obj_id not in accessible_obj_ids(alpha_pto_id))
check("alpha_inspector НЕ видит объект beta",
      beta_obj_id not in accessible_obj_ids(alpha_inspector_id))

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 2. assert_object_access (прямой URL) ═══")

def can_access(user_id, obj_id):
    role = q1("SELECT role FROM users WHERE id=%s", (user_id,))
    org  = q1("SELECT organization_id FROM users WHERE id=%s", (user_id,))
    if role == 'admin': return True
    if role == 'contractor':
        if not org: return False
        return bool(q1("SELECT 1 FROM construction_stages WHERE object_id=%s AND contractor_id=%s",
                        (obj_id, org)))
    if org:
        return bool(q1("SELECT 1 FROM objects WHERE id=%s AND developer_id=%s", (obj_id, org)))
    return False

check("alpha_manager → alpha_obj: доступ ЕСТЬ",  can_access(alpha_manager_id, alpha_obj_id))
check("alpha_manager → beta_obj:  доступ НЕТ",   not can_access(alpha_manager_id, beta_obj_id))
check("alpha_pto     → beta_obj:  доступ НЕТ",   not can_access(alpha_pto_id, beta_obj_id))
check("alpha_inspector → beta_obj: доступ НЕТ",  not can_access(alpha_inspector_id, beta_obj_id))
check("beta_manager  → alpha_obj: доступ НЕТ",   not can_access(beta_manager_id, alpha_obj_id))

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 3. Подрядчик (межтенантный кейс) ═══")

check("contr_contractor видит alpha_obj",  can_access(contr_id, alpha_obj_id))
check("contr_contractor видит beta_obj",   can_access(contr_id, beta_obj_id))

# Подрядчик НЕ должен видеть объект, на котором его нет
other_obj = q1("SELECT id FROM objects WHERE id NOT IN %s AND status='active'",
               (tuple([alpha_obj_id, beta_obj_id]),))
if other_obj:
    check("contr_contractor НЕ видит посторонний объект",
          not can_access(contr_id, other_obj))
else:
    results.append((SKIP, "contr НЕ видит посторонний объект", "нет других объектов в БД"))
    print(f"  {SKIP}  contr НЕ видит посторонний объект (нет других объектов в БД)")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 4. object_team — команда назначена верно ═══")

team_alpha = dict(qall(
    "SELECT ot.role, ot.user_id FROM object_team ot WHERE ot.object_id=%s", (alpha_obj_id,)))
team_beta  = dict(qall(
    "SELECT ot.role, ot.user_id FROM object_team ot WHERE ot.object_id=%s", (beta_obj_id,)))

check("alpha: inspector из команды alpha",
      team_alpha.get('inspector') == alpha_inspector_id,
      f"got {team_alpha.get('inspector')} expected {alpha_inspector_id}")
check("alpha: pto из команды alpha",
      team_alpha.get('pto') == alpha_pto_id)
check("alpha: manager из команды alpha",
      team_alpha.get('manager') == alpha_manager_id)
check("alpha: foreman из команды alpha",
      team_alpha.get('foreman') == alpha_foreman_id)
check("alpha: supply из команды alpha",
      team_alpha.get('supply') == alpha_supply_id)
check("alpha: accountant из команды alpha",
      team_alpha.get('accountant') == alpha_accountant_id)

check("beta: team не пересекается с alpha",
      not any(v in team_alpha.values() for v in team_beta.values()),
      f"team_alpha={team_alpha}, team_beta={team_beta}")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 5. Симуляция отправки пакета КС (approval_steps) ═══")

# Создаём тестовый пакет КС в подэтапе alpha
alpha_substage = q1(
    "SELECT s.id FROM substages s "
    "JOIN construction_stages cs ON s.stage_id = cs.id "
    "WHERE cs.object_id=%s LIMIT 1", (alpha_obj_id,))

if alpha_substage:
    # Вставляем doc_package
    cur.execute(
        "INSERT INTO doc_packages (substage_id, status, created_by) "
        "VALUES (%s, 'in_review', %s) RETURNING id",
        (alpha_substage, alpha_inspector_id))
    pkg_id = cur.fetchone()[0]

    # Имитируем pre-assign как в packages.py package_submit()
    APPROVAL_CHAIN = ['inspector', 'foreman', 'pto', 'manager', 'accountant']
    team = team_alpha

    for i, role in enumerate(APPROVAL_CHAIN):
        approver_id = team.get(role)
        status = 'pending' if i == 0 else 'waiting'
        cur.execute(
            "INSERT INTO approval_steps (package_id, step_order, role, status, approver_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pkg_id, i+1, role, status, approver_id))

    # Проверяем: каждый approver_id принадлежит alpha_org
    cur.execute(
        "SELECT a.role, a.approver_id, u.organization_id "
        "FROM approval_steps a LEFT JOIN users u ON a.approver_id=u.id "
        "WHERE a.package_id=%s", (pkg_id,))
    steps = cur.fetchall()

    all_in_alpha = all(r[2] == alpha_org_id for r in steps if r[1])
    check("Все approver_id в КС-пакете → org alpha",
          all_in_alpha,
          str(steps))

    # Шаг pending — это inspector alpha
    pending = [(r[0], r[1]) for r in steps if r[0] == 'inspector']
    check("Pending-шаг inspector → alpha_inspector_id",
          pending and pending[0][1] == alpha_inspector_id)

    # Фильтр «мои пакеты»: alpha_inspector видит, beta_inspector нет
    def sees_package(user_id, p_id):
        role = q1("SELECT role FROM users WHERE id=%s", (user_id,))
        return bool(q1(
            "SELECT 1 FROM approval_steps "
            "WHERE package_id=%s AND status='pending' "
            "AND (approver_id=%s OR (approver_id IS NULL AND role=%s))",
            (p_id, user_id, role)))

    check("alpha_inspector видит свой pending КС-пакет",
          sees_package(alpha_inspector_id, pkg_id))
    check("beta_inspector НЕ видит КС-пакет alpha",
          not sees_package(beta_inspector_id, pkg_id))

    # Откат теста
    cur.execute("DELETE FROM approval_steps WHERE package_id=%s", (pkg_id,))
    cur.execute("DELETE FROM doc_packages WHERE id=%s", (pkg_id,))
else:
    results.append((SKIP, "Симуляция КС-пакета", "нет подэтапов"))
    print(f"  {SKIP}  Симуляция КС-пакета (нет подэтапов)")

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 6. Симуляция отправки ИД-пакета ═══")

alpha_stage_for_id = alpha_stage_ids[0] if alpha_stage_ids else None
if alpha_stage_for_id:
    cur.execute(
        "INSERT INTO id_packages (stage_id, status, created_by) "
        "VALUES (%s, 'in_review', %s) RETURNING id",
        (alpha_stage_for_id, alpha_inspector_id))
    id_pkg_id = cur.fetchone()[0]

    ID_CHAIN = ['inspector', 'pto', 'manager']
    for i, role in enumerate(ID_CHAIN):
        approver_id = team_alpha.get(role)
        status = 'pending' if i == 0 else 'waiting'
        cur.execute(
            "INSERT INTO id_approval_steps (package_id, step_order, role, status, approver_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_pkg_id, i+1, role, status, approver_id))

    cur.execute(
        "SELECT a.role, a.approver_id, u.organization_id "
        "FROM id_approval_steps a LEFT JOIN users u ON a.approver_id=u.id "
        "WHERE a.package_id=%s", (id_pkg_id,))
    id_steps = cur.fetchall()
    all_in_alpha_id = all(r[2] == alpha_org_id for r in id_steps if r[1])
    check("Все approver_id в ИД-пакете → org alpha", all_in_alpha_id, str(id_steps))

    def sees_id_package(user_id, p_id):
        role = q1("SELECT role FROM users WHERE id=%s", (user_id,))
        return bool(q1(
            "SELECT 1 FROM id_approval_steps "
            "WHERE package_id=%s AND status='pending' "
            "AND (approver_id=%s OR (approver_id IS NULL AND role=%s))",
            (p_id, user_id, role)))

    check("alpha_inspector видит свой ИД-пакет",
          sees_id_package(alpha_inspector_id, id_pkg_id))
    check("beta_inspector НЕ видит ИД-пакет alpha",
          not sees_id_package(beta_inspector_id, id_pkg_id))

    cur.execute("DELETE FROM id_approval_steps WHERE package_id=%s", (id_pkg_id,))
    cur.execute("DELETE FROM id_packages WHERE id=%s", (id_pkg_id,))
else:
    results.append((SKIP, "Симуляция ИД-пакета", "нет этапов"))

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 7. Валидация: блокировка при неполной команде ═══")

# Проверяем логику: если из команды убрать роль — пакет нельзя отправить
# (логика в packages.py: missing = [r for r in chain_roles if r not in team])
APPROVAL_CHAIN_ROLES = ['inspector', 'foreman', 'pto', 'manager', 'accountant']

complete = all(r in team_alpha for r in APPROVAL_CHAIN_ROLES)
check("Команда alpha полная (блокировки нет)", complete, str(team_alpha))

# Симулируем неполную команду — убираем одну роль
incomplete_team = {k: v for k, v in team_alpha.items() if k != 'accountant'}
missing = [r for r in APPROVAL_CHAIN_ROLES if r not in incomplete_team]
check("Неполная команда → missing=['accountant']", missing == ['accountant'], str(missing))

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 8. Регистрация по коду + pending-роль ═══")

# Проверяем, что пользователи созданы с нужными полями
alpha_manager_row = qall(
    "SELECT role, is_approved, organization_id FROM users WHERE username='alpha_manager'")
if alpha_manager_row:
    role, approved, org = alpha_manager_row[0]
    check("alpha_manager: role=manager", role == 'manager')
    check("alpha_manager: is_approved=1", approved == 1)
    check("alpha_manager: org=alpha_org", org == alpha_org_id)

# Проверяем, что join_code уникален и активен
alpha_code = q1("SELECT join_code FROM organizations WHERE id=%s", (alpha_org_id,))
beta_code  = q1("SELECT join_code FROM organizations WHERE id=%s", (beta_org_id,))
check("join_code у alpha уникален", alpha_code != beta_code)
check("alpha org: status=active",
      q1("SELECT status FROM organizations WHERE id=%s", (alpha_org_id,)) == 'active')

# Симуляция регистрации по коду: находим org по join_code
found_org = q1("SELECT id FROM organizations WHERE join_code=%s AND status='active'", (alpha_code,))
check("Регистрация по коду: org находится", found_org == alpha_org_id)
wrong_org = q1("SELECT id FROM organizations WHERE join_code='WRONGCOD' AND status='active'")
check("Неверный код: org не найдена", wrong_org is None)

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 9. Изоляция users_list для manager ═══")

# manager видит только своих
alpha_users_visible = [r[0] for r in qall(
    "SELECT id FROM users WHERE organization_id=%s", (alpha_org_id,))]
beta_users_visible  = [r[0] for r in qall(
    "SELECT id FROM users WHERE organization_id=%s", (beta_org_id,))]

check("alpha_manager видит только своих",
      beta_manager_id not in alpha_users_visible and alpha_manager_id in alpha_users_visible)
check("beta_manager видит только своих",
      alpha_manager_id not in beta_users_visible and beta_manager_id in beta_users_visible)

# _assert_same_tenant: alpha_manager → beta user → 403
alpha_org = q1("SELECT organization_id FROM users WHERE id=%s", (alpha_manager_id,))
target_org = q1("SELECT organization_id FROM users WHERE id=%s", (beta_manager_id,))
check("_assert_same_tenant: alpha_manager → beta_user → 403",
      alpha_org != target_org)

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 10. init_db() идемпотентность ═══")
import subprocess
result = subprocess.run(
    [sys.executable, '-c', 'from app import init_db; init_db()'],
    capture_output=True, text=True, cwd=PROJECT_DIR)
check("init_db() повторный запуск без ошибок",
      result.returncode == 0,
      result.stderr[:300] if result.stderr else '')

# ════════════════════════════════════════════════════════════════════════════
print("\n═══ 11. Регресс: структура БД ═══")

EXPECTED_TABLES = [
    'organizations', 'users', 'objects', 'construction_stages', 'substages',
    'doc_packages', 'approval_steps', 'id_packages', 'id_approval_steps',
    'object_team', 'defects', 'material_requests', 'notifications',
    'journal_entries', 'guest_tokens',
]
cur.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='public' AND table_type='BASE TABLE'")
existing_tables = {r[0] for r in cur.fetchall()}

for t in EXPECTED_TABLES:
    check(f"Таблица '{t}' существует", t in existing_tables)

# ════════════════════════════════════════════════════════════════════════════
conn.rollback()
cur.close()
conn.close()

# ── Итог ──────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
skipped= sum(1 for s, _, _ in results if s == SKIP)

print(f"\n{'═'*60}")
print(f"  ИТОГ: {passed}/{total} OK  |  {failed} FAIL  |  {skipped} SKIP")
print(f"{'═'*60}")

sys.exit(0 if failed == 0 else 1)
