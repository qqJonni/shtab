"""
seed_demo_data.py — масштабные демо-данные для анализа и тестирования.

ДОБАВЛЯЕТ данные, не трогая существующие. Все сид-записи помечены
маркером [DEMO] (в full_name пользователей и названиях организаций/объектов).

Создаёт:
  - 4 застройщика-тенанта с полными командами (по 6 пользователей)
  - 6 подрядных организаций (часть работает на нескольких застройщиков)
  - По 1 объекту у каждого застройщика с логичной цепочкой этапов
    стройки (котлован → ... → благоустройство) и реалистичными сроками
  - Подэтапы с объёмами и ценами; часть прогнана через сдачу
    (пакеты КС: completed / in_review на разных шагах цепочки)
  - Команды объектов (object_team)
  - 150+ замечаний с разными статусами, приоритетами и комментариями

Пароль всех пользователей: 1234

Запуск:
    python3 seed_demo_data.py          # создать
    python3 seed_demo_data.py --wipe   # удалить всё по маркеру [DEMO]
"""

import sys
import random
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash

import config

random.seed(42)
M = '[DEMO]'
PASSWORD = '1234'
TODAY = date.today()


# ═══════════════════════ Справочники сида ═══════════════════════

DEVELOPERS = [
    ('СЗ «Уральские высоты»',    'ural',   'ЖК «Уральские высоты», дом 1',   'г. Пермь, ул. Ленина, 50'),
    ('ГК «КамаДевелопмент»',     'kama',   'ЖК «Камская набережная», д. 2',  'г. Пермь, наб. Камы, 14'),
    ('СЗ «ПермьСтройИнвест»',    'psi',    'ЖК «Парковый квартал», секция А','г. Пермь, ул. Подлесная, 7'),
    ('ДК «Меридиан»',            'merid',  'БЦ «Меридиан Плаза»',            'г. Пермь, ш. Космонавтов, 111'),
]

CONTRACTORS = [
    ('ООО «ГеоФундамент»',      'geofund',  ['земляные', 'фундамент']),
    ('ООО «МонолитПро»',        'monolit',  ['каркас']),
    ('ООО «СтенКомплект»',      'stenkom',  ['кладка', 'фасад']),
    ('ООО «ИнжСети Прикамья»',  'inzhset',  ['инженерия']),
    ('ООО «КровТехМонтаж»',     'krovteh',  ['кровля']),
    ('ИП Отделкин А.В.',        'otdelka',  ['отделка', 'благоустройство']),
]

TEAM = [
    ('manager',    'Руководилов {n} Петрович'),
    ('pto',        'Сметчикова {n} Ивановна'),
    ('inspector',  'Надзоров {n} Сергеевич'),
    ('foreman',    'Прорабов {n} Андреевич'),
    ('supply',     'Снабженцев {n} Олегович'),
    ('accountant', 'Бухгалтерова {n} Юрьевна'),
]
NAMES = ['Александр', 'Дмитрий', 'Сергей', 'Михаил']

# (название, спец-ключ подрядчика, длительность дней, [(подэтап, объём, ед, цена)])
STAGE_PLAN = [
    ('Разработка котлована', 'земляные', 30, [
        ('Геодезическая разбивка', 1200, 'м2', 45),
        ('Экскавация грунта', 5400, 'м3', 380),
        ('Вывоз грунта', 4800, 'м3', 290),
        ('Водопонижение', 30, 'шт.', 12000),
    ]),
    ('Устройство фундамента', 'фундамент', 45, [
        ('Бетонная подготовка', 1150, 'м2', 520),
        ('Гидроизоляция плиты', 1150, 'м2', 640),
        ('Армирование фундаментной плиты', 118, 'т.', 58000),
        ('Бетонирование плиты', 920, 'м3', 6800),
    ]),
    ('Монолитный каркас', 'каркас', 120, [
        ('Колонны 1-5 этаж', 260, 'м3', 9200),
        ('Стены и диафрагмы 1-5 этаж', 480, 'м3', 8700),
        ('Плиты перекрытия 1-5 этаж', 3900, 'м2', 2400),
        ('Колонны 6-10 этаж', 260, 'м3', 9400),
        ('Плиты перекрытия 6-10 этаж', 3900, 'м2', 2450),
    ]),
    ('Кладка наружных и внутренних стен', 'кладка', 90, [
        ('Кладка газобетон наружные', 2800, 'м3', 4300),
        ('Кладка перегородок', 5200, 'м2', 950),
        ('Перемычки и армопояса', 640, 'м.пог.', 1200),
    ]),
    ('Кровельные работы', 'кровля', 40, [
        ('Пароизоляция', 1300, 'м2', 180),
        ('Утеплитель кровли', 1300, 'м2', 620),
        ('Наплавляемая кровля 2 слоя', 1300, 'м2', 890),
        ('Парапеты и примыкания', 210, 'м.пог.', 1500),
    ]),
    ('Фасадные работы', 'фасад', 75, [
        ('Утепление фасада', 4100, 'м2', 1450),
        ('Декоративная штукатурка', 4100, 'м2', 980),
        ('Витражи и окна', 640, 'шт.', 18500),
    ]),
    ('Внутренние инженерные сети', 'инженерия', 100, [
        ('Стояки отопления', 1850, 'м.пог.', 1250),
        ('Разводка ХВС/ГВС', 3200, 'м.пог.', 890),
        ('Канализация', 1600, 'м.пог.', 760),
        ('Электромонтаж квартир', 160, 'шт.', 42000),
        ('Слаботочные сети', 160, 'шт.', 9800),
    ]),
    ('Внутренняя отделка МОП и квартир', 'отделка', 110, [
        ('Штукатурка стен', 14500, 'м2', 420),
        ('Стяжка полов', 9800, 'м2', 510),
        ('Шпаклёвка и окраска МОП', 3600, 'м2', 380),
        ('Укладка плитки МОП', 1450, 'м2', 1150),
        ('Чистовая отделка квартир', 160, 'шт.', 185000),
    ]),
    ('Благоустройство территории', 'благоустройство', 50, [
        ('Асфальтирование проездов', 2400, 'м2', 1350),
        ('Тротуарная плитка', 1800, 'м2', 1600),
        ('Озеленение', 3500, 'м2', 450),
        ('Малые архитектурные формы', 24, 'шт.', 65000),
    ]),
]

DEFECT_TITLES = [
    ('Трещина в стяжке пола', 6), ('Отслоение штукатурки', 5),
    ('Неровность стен свыше допуска', 5), ('Протечка стояка отопления', 3),
    ('Не работает розеточная группа', 2), ('Царапины на витраже', 4),
    ('Скол плитки в МОП', 1), ('Отсутствует гидроизоляция примыкания', 7),
    ('Нарушена геометрия кладки', 5), ('Холодный шов в монолите', 9),
    ('Оголение арматуры', 9), ('Пустоты под плиткой', 6),
    ('Дефект окраски потолка', 5), ('Некачественная затирка швов', 1),
    ('Провис натяжного потолка', 5), ('Дверь задевает коробку', 4),
    ('Продувание оконного блока', 4), ('Уклон пола отсутствует', 3),
    ('Ржавчина на закладных', 9), ('Мусор строительный не вывезен', 9),
]
DEFECT_COMMENTS_WORK = [
    'Приступили к устранению, материал заказан.',
    'Устранено, просьба проверить.',
    'Требуется доступ в помещение, согласуйте дату.',
    'Частично устранено, завершим до конца недели.',
]
DEFECT_COMMENTS_CHECK = [
    'Проверено, замечание снято.',
    'Устранено не полностью — переделать угол у окна.',
    'Принято с оговоркой, наблюдаем после отопительного сезона.',
]


def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _ins(cur, table, data):
    cols = ', '.join(data)
    ph = ', '.join(['%s'] * len(data))
    cur.execute(f'INSERT INTO {table} ({cols}) VALUES ({ph}) RETURNING id', list(data.values()))
    return cur.fetchone()[0]


def _code(cur, base):
    import secrets
    cur.execute('SELECT 1 FROM organizations WHERE join_code = %s', (base,))
    if not cur.fetchone():
        return base
    while True:
        c = secrets.token_urlsafe(6)[:8].upper()
        cur.execute('SELECT 1 FROM organizations WHERE join_code = %s', (c,))
        if not cur.fetchone():
            return c


def wipe(cur):
    cur.execute("SELECT id FROM users WHERE full_name LIKE %s", (f'%{M}%',))
    uids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM objects WHERE name LIKE %s", (f'%{M}%',))
    oids = [r[0] for r in cur.fetchall()]
    if not uids and not oids:
        print('Демо-данные не найдены.')
        return
    for oid in oids:
        cur.execute("SELECT id FROM construction_stages WHERE object_id=%s", (oid,))
        sids = [r[0] for r in cur.fetchall()]
        for sid in sids:
            cur.execute("DELETE FROM approval_steps WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
            cur.execute("DELETE FROM package_items WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
            cur.execute("DELETE FROM package_documents WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
            cur.execute("DELETE FROM doc_packages WHERE stage_id=%s", (sid,))
        cur.execute("DELETE FROM defect_history WHERE defect_id IN (SELECT id FROM defects WHERE object_id=%s)", (oid,))
        cur.execute("DELETE FROM defects WHERE object_id=%s", (oid,))
        cur.execute("DELETE FROM objects WHERE id=%s", (oid,))
    for uid in uids:
        cur.execute('DELETE FROM notifications WHERE user_id=%s', (uid,))
        cur.execute('DELETE FROM object_team WHERE user_id=%s', (uid,))
        cur.execute('UPDATE approval_steps SET approver_id=NULL WHERE approver_id=%s', (uid,))
        cur.execute('UPDATE id_approval_steps SET approver_id=NULL WHERE approver_id=%s', (uid,))
        cur.execute('DELETE FROM users WHERE id=%s', (uid,))
    cur.execute("DELETE FROM organizations WHERE name LIKE %s", (f'%{M}%',))
    print(f'Удалено: {len(uids)} пользователей, {len(oids)} объектов, демо-организации.')


def seed():
    conn = _conn()
    cur = conn.cursor()
    pwd = generate_password_hash(PASSWORD)
    logins = []          # (тенант, роль, логин)

    # ── Подрядчики ──
    contr = {}           # spec key → (org_id, user_id)
    contr_by_prefix = {}
    for cname, cpref, specs in CONTRACTORS:
        org_id = _ins(cur, 'organizations', {
            'name': f'{cname} {M}', 'type': 'contractor',
            'join_code': _code(cur, (cpref + 'DEMO1234')[:8].upper()),
            'status': 'active'})
        uid = _ins(cur, 'users', {
            'username': f'demo_{cpref}', 'password_hash': pwd, 'role': 'contractor',
            'full_name': f'Подрядчиков {cpref.title()} {M}', 'is_approved': 1,
            'organization_id': org_id})
        logins.append(('Подрядчики', cname, f'demo_{cpref}'))
        contr_by_prefix[cpref] = (org_id, uid)
        for s in specs:
            contr[s] = (org_id, uid)

    # ── Застройщики, объекты, этапы ──
    all_defect_targets = []   # (object_id, stage_id, substage_id|None, inspector_id, contractor_org, foreman_id)
    stats = {'stages': 0, 'subs': 0, 'pkgs': 0, 'defects': 0}

    for di, (dname, dpref, obj_name, obj_addr) in enumerate(DEVELOPERS):
        dev_org = _ins(cur, 'organizations', {
            'name': f'{dname} {M}', 'type': 'developer',
            'join_code': _code(cur, (dpref + 'DEMO1234')[:8].upper()),
            'status': 'active'})

        team = {}
        for role, fname in TEAM:
            uid = _ins(cur, 'users', {
                'username': f'{dpref}_{role}', 'password_hash': pwd, 'role': role,
                'full_name': f'{fname.format(n=NAMES[di])} {M}', 'is_approved': 1,
                'organization_id': dev_org})
            team[role] = uid
            logins.append((dname, config.ROLES.get(role, role), f'{dpref}_{role}'))

        obj_id = _ins(cur, 'objects', {
            'name': f'{obj_name} {M}', 'address': obj_addr, 'type': 'residential',
            'status': 'active', 'developer_id': dev_org, 'created_by': team['manager']})

        for role in ('inspector', 'pto', 'foreman', 'manager', 'accountant', 'supply'):
            cur.execute('INSERT INTO object_team (object_id, role, user_id) VALUES (%s,%s,%s) '
                        'ON CONFLICT (object_id, role) DO NOTHING', (obj_id, role, team[role]))

        # сроки: стройка стартовала 14 месяцев назад (частично со сдвигом на застройщика)
        start = TODAY - timedelta(days=430 - di * 45)
        for order, (st_name, spec, dur, subs) in enumerate(STAGE_PLAN, 1):
            end = start + timedelta(days=dur)
            c_org, c_uid = contr[spec]
            if end < TODAY - timedelta(days=14):
                st_status = 'done'
            elif start <= TODAY:
                st_status = 'in_progress'
            else:
                st_status = 'planned'
            stage_id = _ins(cur, 'construction_stages', {
                'object_id': obj_id, 'name': st_name, 'order_num': order,
                'contractor_id': c_org, 'contractor_status': 'assigned',
                'plan_start_date': start.isoformat(), 'plan_end_date': end.isoformat(),
                'status': st_status, 'created_by': team['manager'],
                'contract_amount': sum(v * p for _, v, _, p in subs)})
            stats['stages'] += 1

            sub_rows = []
            n = len(subs)
            for si, (sub_name, vol, unit, price) in enumerate(subs):
                s_start = start + timedelta(days=int(dur * si / n))
                s_end = start + timedelta(days=int(dur * (si + 1) / n))
                if st_status == 'done':
                    ss = 'approved' if si < n - 1 else 'closed'
                elif st_status == 'in_progress':
                    frac = (TODAY - start).days / dur
                    pos = si / n
                    if (si + 1) / n <= frac and random.random() < 0.18:
                        # «застрявшая» работа: план целиком прошёл, а она в работе —
                        # реальная просрочка на графике
                        ss = 'in_progress'
                    elif pos < frac - 0.25:
                        ss = 'approved'
                    elif pos < frac:
                        ss = random.choice(['done', 'closed'])
                    elif pos < frac + 0.3:
                        ss = 'in_progress'
                    else:
                        ss = 'not_started'
                else:
                    ss = 'not_started'
                # ── Реалистичный факт: старт с задержкой, финиш с отклонением ──
                # (стройки чаще опаздывают: 60% с опозданием до 2 недель,
                #  25% в срок ±2 дня, 15% с опережением)
                actual_start = actual_end = None
                finished = ss in ('approved', 'closed', 'done')
                started = finished or ss == 'in_progress'
                if started:
                    a_start = s_start + timedelta(days=random.randint(-1, 6))
                    actual_start = min(a_start, TODAY).isoformat()
                if finished:
                    roll = random.random()
                    if roll < 0.60:
                        shift = random.randint(2, 14)      # опоздание
                    elif roll < 0.85:
                        shift = random.randint(-2, 2)      # в срок
                    else:
                        shift = random.randint(-8, -3)     # опережение
                    a_end = s_end + timedelta(days=shift)
                    a_end = max(a_end, date.fromisoformat(actual_start))
                    actual_end = min(a_end, TODAY).isoformat()
                sub_id = _ins(cur, 'substages', {
                    'stage_id': stage_id, 'name': sub_name, 'volume': vol, 'unit': unit,
                    'unit_price': price, 'total_price': vol * price,
                    'plan_start_date': s_start.isoformat(),
                    'plan_end_date': s_end.isoformat(), 'status': ss,
                    'actual_start_date': actual_start, 'actual_end_date': actual_end,
                    'completed_at': actual_end, 'created_by': team['pto']})
                sub_rows.append((sub_id, sub_name, vol, unit, price, ss, actual_end))
                stats['subs'] += 1
                all_defect_targets.append((obj_id, stage_id, sub_id, team['inspector'], c_org, team['foreman'], c_uid))

            # ── Пакеты КС: approved-подэтапы → completed-пакет; closed → in_review ──
            appr = [s for s in sub_rows if s[5] == 'approved']
            if appr:
                # пакет подан после факт-завершения работ, согласован ~неделю
                last_fin = max((s[6] for s in appr if s[6]), default=None)
                base_d = date.fromisoformat(last_fin) if last_fin else TODAY - timedelta(days=25)
                submitted = min(base_d + timedelta(days=random.randint(1, 4)), TODAY)
                completed = min(submitted + timedelta(days=random.randint(4, 10)), TODAY)
                pkg_id = _ins(cur, 'doc_packages', {
                    'stage_id': stage_id, 'contractor_id': c_org, 'created_by': c_uid,
                    'status': 'completed',
                    'submitted_at': submitted.isoformat(),
                    'completed_at': completed.isoformat()})
                for sub_id, _, vol, _, price, _, _ in appr:
                    cur.execute('INSERT INTO package_items (package_id, substage_id, qty, unit_price, amount) '
                                'VALUES (%s,%s,%s,%s,%s)', (pkg_id, sub_id, vol, price, vol * price))
                for i, (role, _) in enumerate(config.APPROVAL_CHAIN, 1):
                    cur.execute('INSERT INTO approval_steps (package_id, step_order, role, status, approver_id, acted_at) '
                                'VALUES (%s,%s,%s,%s,%s,%s)',
                                (pkg_id, i, role, 'approved', team[role],
                                 min(submitted + timedelta(days=i), completed).isoformat()))
                stats['pkgs'] += 1
            closed = [s for s in sub_rows if s[5] == 'closed']
            if closed:
                pkg_id = _ins(cur, 'doc_packages', {
                    'stage_id': stage_id, 'contractor_id': c_org, 'created_by': c_uid,
                    'status': 'in_review',
                    'submitted_at': (TODAY - timedelta(days=random.randint(2, 9))).isoformat()})
                for sub_id, _, vol, _, price, _, _ in closed:
                    part = random.choice([1.0, 1.0, 0.5])   # иногда процентовка
                    cur.execute('INSERT INTO package_items (package_id, substage_id, qty, unit_price, amount) '
                                'VALUES (%s,%s,%s,%s,%s)',
                                (pkg_id, sub_id, vol * part, price, round(vol * part * price, 2)))
                stop = random.randint(1, 3)   # на каком шаге цепочки стоит
                for i, (role, _) in enumerate(config.APPROVAL_CHAIN, 1):
                    st = 'approved' if i < stop else ('pending' if i == stop else 'waiting')
                    cur.execute('INSERT INTO approval_steps (package_id, step_order, role, status, approver_id) '
                                'VALUES (%s,%s,%s,%s,%s)', (pkg_id, i, role, st, team[role]))
                stats['pkgs'] += 1

            start = end + timedelta(days=random.randint(0, 7))

        # ── Факт этапов из подэтапов ──
        cur.execute('''
            UPDATE construction_stages cs SET actual_start_date = sub.mn
            FROM (SELECT stage_id, MIN(actual_start_date) mn FROM substages
                  WHERE actual_start_date IS NOT NULL GROUP BY stage_id) sub
            WHERE cs.id = sub.stage_id AND cs.object_id = %s''', (obj_id,))
        cur.execute('''
            UPDATE construction_stages cs SET actual_end_date = sub.mx
            FROM (SELECT stage_id, MAX(actual_end_date) mx FROM substages
                  WHERE actual_end_date IS NOT NULL GROUP BY stage_id) sub
            WHERE cs.id = sub.stage_id AND cs.object_id = %s AND cs.status = 'done' ''', (obj_id,))

        # ── Baseline: утверждаем исходный план, затем «жизнь» сдвигает
        #    планы будущих этапов вправо (реалистичный сдвиг графика) ──
        snapshot = {}
        cur.execute('SELECT id, plan_start_date, plan_end_date FROM construction_stages WHERE object_id=%s', (obj_id,))
        for r in cur.fetchall():
            snapshot[f'stage:{r[0]}'] = {'plan_start': r[1], 'plan_end': r[2]}
        cur.execute('''SELECT ss.id, ss.plan_start_date, ss.plan_end_date, ss.volume, ss.total_price
                       FROM substages ss JOIN construction_stages cs ON ss.stage_id = cs.id
                       WHERE cs.object_id=%s''', (obj_id,))
        for r in cur.fetchall():
            snapshot[f'sub:{r[0]}'] = {'plan_start': r[1], 'plan_end': r[2],
                                       'volume': float(r[3]) if r[3] is not None else None,
                                       'cost': float(r[4]) if r[4] is not None else None}
        import json as _json
        bl_date = (TODAY - timedelta(days=400 - di * 45))
        cur.execute('INSERT INTO schedule_baselines (object_id, name, created_by, created_at, data_json) '
                    'VALUES (%s,%s,%s,%s,%s)',
                    (obj_id, f'Утверждён {bl_date.isoformat()}', team['manager'],
                     bl_date.isoformat() + ' 10:00:00', _json.dumps(snapshot, ensure_ascii=False)))
        # сдвиг планов 2 последних (будущих) этапов на 5–15 дней вправо
        cur.execute("SELECT id FROM construction_stages WHERE object_id=%s AND status='planned' "
                    "ORDER BY order_num DESC LIMIT 2", (obj_id,))
        for (sid,) in cur.fetchall():
            shift = random.randint(5, 15)
            cur.execute("UPDATE construction_stages SET "
                        "plan_start_date = (plan_start_date::date + %s)::text, "
                        "plan_end_date = (plan_end_date::date + %s)::text WHERE id=%s",
                        (shift, shift, sid))
            cur.execute("UPDATE substages SET "
                        "plan_start_date = (plan_start_date::date + %s)::text, "
                        "plan_end_date = (plan_end_date::date + %s)::text "
                        "WHERE stage_id=%s AND plan_end_date IS NOT NULL", (shift, shift, sid))

        # ── Вехи объекта: из фактических/плановых дат этапов ──
        cur.execute('SELECT name, plan_end_date, actual_end_date, status FROM construction_stages '
                    'WHERE object_id=%s ORDER BY order_num', (obj_id,))
        st_rows = cur.fetchall()
        milestone_map = [(0, 'Котлован завершён'), (2, 'Каркас закрыт'),
                         (4, 'Кровля закрыта'), (8, 'Ввод в эксплуатацию')]
        for idx, m_name in milestone_map:
            if idx >= len(st_rows):
                continue
            _, p_end, a_end, st_status = st_rows[idx]
            plan_d = p_end
            if m_name == 'Ввод в эксплуатацию' and p_end:
                plan_d = (date.fromisoformat(p_end) + timedelta(days=45)).isoformat()
            if st_status == 'done' and a_end:
                cur.execute('INSERT INTO schedule_milestones (object_id, name, plan_date, actual_date, status, order_num) '
                            "VALUES (%s,%s,%s,%s,'done',%s)", (obj_id, m_name, plan_d, a_end, idx))
            else:
                cur.execute('INSERT INTO schedule_milestones (object_id, name, plan_date, status, order_num) '
                            "VALUES (%s,%s,%s,'pending',%s)", (obj_id, m_name, plan_d, idx))

    # ── Замечания: 160 на все объекты ──
    N_DEFECTS = 160
    for k in range(N_DEFECTS):
        obj_id, stage_id, sub_id, insp, c_org, foreman, c_uid = random.choice(all_defect_targets)
        title, type_id = random.choice(DEFECT_TITLES)
        prio = random.choices(['low', 'normal', 'high', 'critical'], [2, 5, 2, 1])[0]
        created = TODAY - timedelta(days=random.randint(1, 120))
        due = created + timedelta(days={'critical': 3, 'high': 7, 'normal': 14, 'low': 21}[prio])
        r = random.random()
        if r < 0.35:
            status, resolved_at, verified_at = 'open' if random.random() < 0.6 else 'in_progress', None, None
        elif r < 0.55:
            status, resolved_at, verified_at = 'resolved', created + timedelta(days=random.randint(2, 10)), None
        elif r < 0.9:
            status = random.choice(['verified', 'closed'])
            resolved_at = created + timedelta(days=random.randint(2, 8))
            verified_at = resolved_at + timedelta(days=random.randint(1, 4))
        else:
            status, resolved_at, verified_at = 'rejected', None, None
        d_id = _ins(cur, 'defects', {
            'object_id': obj_id, 'stage_id': stage_id, 'substage_id': sub_id,
            'title': title, 'description': f'{title}. Выявлено при обходе. Требуется устранение по нормативу.',
            'type_id': type_id, 'priority': prio, 'status': status,
            'reporter_id': insp, 'contractor_id': c_org, 'due_date': due.isoformat(),
            'created_at': created.isoformat(),
            'resolved_at': resolved_at.isoformat() if resolved_at else None,
            'verified_at': verified_at.isoformat() if verified_at else None})
        stats['defects'] += 1
        cur.execute('INSERT INTO defect_history (defect_id, user_id, action, new_value, created_at) '
                    'VALUES (%s,%s,%s,%s,%s)', (d_id, insp, 'created', title, created.isoformat()))
        if status != 'open' and random.random() < 0.7:
            cur.execute('INSERT INTO defect_history (defect_id, user_id, action, comment, created_at) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (d_id, c_uid, 'comment', random.choice(DEFECT_COMMENTS_WORK),
                         (created + timedelta(days=1)).isoformat()))
        if status in ('verified', 'closed', 'rejected') and random.random() < 0.6:
            cur.execute('INSERT INTO defect_history (defect_id, user_id, action, comment, created_at) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (d_id, insp, 'comment', random.choice(DEFECT_COMMENTS_CHECK),
                         (verified_at or created + timedelta(days=5)).isoformat()))

    conn.commit()
    cur.close()
    conn.close()

    # ── Отчёт ──
    print('═' * 64)
    print(f'  ДЕМО-ДАННЫЕ СОЗДАНЫ: 4 застройщика, {len(CONTRACTORS)} подрядчиков,')
    print(f'  {stats["stages"]} этапов, {stats["subs"]} подэтапов, {stats["pkgs"]} пакетов КС, {stats["defects"]} замечаний')
    print('═' * 64)
    print(f'\n  Пароль у всех: {PASSWORD}\n')
    cur_group = None
    for group, role, login in logins:
        if group != cur_group:
            print(f'  ── {group} ──')
            cur_group = group
        print(f'     {role:<22} {login}')
    print()
    print('  Кросс-тенантные подрядчики (работают у ВСЕХ застройщиков):')
    for cname, cpref, specs in CONTRACTORS:
        print(f'     demo_{cpref:<10} — {", ".join(specs)}')


if __name__ == '__main__':
    if '--wipe' in sys.argv:
        conn = _conn()
        cur = conn.cursor()
        wipe(cur)
        conn.commit()
        conn.close()
    else:
        seed()
