"""
seed_zhelezno.py — реалистичные данные для ООО Спецзастройщик «Железно Пермь».

Объект №8 «Многоквартирный жилой дом №3 (1 очередь)» — дом на 12 блок-секций.
Достраивает полный цикл этапов от котлована до благоустройства ПЕРЕД
существующим этапом чистовой отделки квартир (ИП Львов, id=20) — его НЕ ТРОГАЕМ.

- Профильные подрядчики (земляные / фундамент / монолит / кладка / кровля /
  фасад / инженерка / отделка МОП), привязаны к тенанту «Железно» (created_by_org).
- Подэтапы по видам работ, суммарные объёмы на весь дом (12 БС), цены РФ 2025.
- Завершённые этапы (done) с факт-датами и пакетами КС (completed) — освоение.
- Благоустройство — planned.

Запуск:  python3 seed_zhelezno.py          # создать
         python3 seed_zhelezno.py --wipe   # удалить созданное (этап Львова цел)
"""

import sys
import random
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

import config

random.seed(2026)

OBJECT_ID = 8
DEVELOPER_ID = 12            # ООО «Железно Пермь»
KEEP_STAGE_ID = 20          # чистовая отделка квартир (ИП Львов) — не трогать
KEEP_STAGE_ORDER = 9        # её место в порядке (перед благоустройством)

# ── Профильные подрядчики (реалистичные пермские названия) ──
CONTRACTORS = {
    'zemlya':  ('ООО «ПермГрунтСтрой»',    '5904111201', '590401001'),
    'fund':    ('ООО «БазисФундамент»',     '5904111202', '590401001'),
    'monolit': ('ООО «МонолитУрал»',        '5904111203', '590401001'),
    'klad':    ('ООО «КамКладка»',          '5904111204', '590401001'),
    'krovlya': ('ООО «КровляСервис-Пермь»', '5904111205', '590401001'),
    'fasad':   ('ООО «ФасадПрофиль»',       '5904111206', '590401001'),
    'inzh':    ('ООО «ПермИнжСети»',        '5904111207', '590401001'),
    'motdelka':('ООО «ОтделкаМОП»',         '5904111208', '590401001'),
}

# ── Этапы: (order, название, ключ подрядчика, [(подэтап, объём, ед, цена)]) ──
# Объёмы — суммарно на дом 12 блок-секций (~10 этажей, ~34 тыс. м² общая).
STAGES = [
    (1, 'Разработка котлована', 'zemlya', [
        ('Геодезическая разбивка осей здания', 3200, 'м2', 55),
        ('Разработка грунта экскаватором', 19400, 'м3', 410),
        ('Вывоз грунта автосамосвалами', 16200, 'м3', 300),
        ('Доработка грунта вручную', 640, 'м3', 1100),
        ('Обратная засыпка пазух с уплотнением', 3100, 'м3', 360),
        ('Водопонижение (иглофильтры)', 90, 'сут.', 9500),
    ]),
    (2, 'Устройство фундаментной плиты', 'fund', [
        ('Устройство бетонной подготовки', 3200, 'м2', 560),
        ('Оклеечная гидроизоляция плиты', 3400, 'м2', 680),
        ('Армирование фундаментной плиты', 468, 'т.', 61000),
        ('Бетонирование фундаментной плиты', 3840, 'м3', 7200),
        ('Устройство приямков и ростверков', 180, 'м3', 8800),
    ]),
    (3, 'Монолитный каркас', 'monolit', [
        ('Армирование колонн и пилонов', 780, 'т.', 62000),
        ('Бетонирование колонн и пилонов', 4200, 'м3', 9200),
        ('Армирование стен и диафрагм', 640, 'т.', 61000),
        ('Бетонирование стен и диафрагм', 5100, 'м3', 8700),
        ('Армирование плит перекрытий', 1120, 'т.', 60000),
        ('Бетонирование плит перекрытий', 34200, 'м2', 2450),
        ('Устройство лестничных маршей', 264, 'шт.', 14500),
    ]),
    (4, 'Наружные стены и перегородки', 'klad', [
        ('Кладка наружных стен из газобетона', 7800, 'м3', 4300),
        ('Кладка перегородок из газобетона', 24600, 'м2', 950),
        ('Устройство перемычек и армопоясов', 2400, 'пог. м', 1250),
        ('Монтаж оконных и балконных блоков', 3960, 'шт.', 16500),
    ]),
    (5, 'Кровельные работы', 'krovlya', [
        ('Устройство пароизоляции', 3400, 'м2', 190),
        ('Утепление кровли минплитой', 3400, 'м2', 640),
        ('Устройство наплавляемой кровли в 2 слоя', 3400, 'м2', 920),
        ('Устройство парапетов и примыканий', 720, 'пог. м', 1550),
        ('Монтаж воронок и водостоков', 96, 'шт.', 4200),
    ]),
    (6, 'Фасадные работы', 'fasad', [
        ('Устройство навесного вентфасада (подсистема)', 21800, 'м2', 1350),
        ('Утепление фасада минплитой', 21800, 'м2', 780),
        ('Облицовка керамогранитом', 21800, 'м2', 1450),
        ('Остекление лоджий', 4800, 'м2', 4200),
        ('Устройство отливов и откосов', 3200, 'пог. м', 650),
    ]),
    (7, 'Внутренние инженерные сети', 'inzh', [
        ('Монтаж стояков отопления', 8600, 'пог. м', 1250),
        ('Монтаж радиаторов отопления', 1980, 'шт.', 4800),
        ('Прокладка сетей ХВС и ГВС', 14200, 'пог. м', 890),
        ('Прокладка сетей канализации', 7600, 'пог. м', 760),
        ('Электромонтаж этажных щитов и стояков', 132, 'шт.', 38000),
        ('Монтаж слаботочных сетей (СКС, домофон)', 264, 'шт.', 12500),
        ('Монтаж систем дымоудаления и ВПВ', 24, 'компл.', 185000),
    ]),
    (8, 'Отделка мест общего пользования (МОП)', 'motdelka', [
        ('Штукатурка стен МОП', 18400, 'м2', 430),
        ('Шпаклёвка и окраска стен и потолков МОП', 26800, 'м2', 380),
        ('Устройство стяжки полов МОП', 6200, 'м2', 520),
        ('Облицовка полов керамогранитом (МОП)', 4200, 'м2', 1250),
        ('Облицовка стен плиткой (лифтовые холлы)', 3100, 'м2', 1650),
        ('Монтаж ограждений и поручней лестниц', 2600, 'пог. м', 2400),
        ('Установка дверей МОП и тамбурных блоков', 480, 'шт.', 9800),
        ('Установка светильников МОП', 960, 'шт.', 650),
    ]),
    (10, 'Благоустройство территории', 'zemlya', [
        ('Устройство оснований проездов и площадок', 6400, 'м2', 780),
        ('Асфальтирование проездов', 4200, 'м2', 1350),
        ('Устройство тротуаров из плитки', 3100, 'м2', 1600),
        ('Озеленение и посадка деревьев', 5800, 'м2', 480),
        ('Установка МАФ и детских площадок', 8, 'компл.', 420000),
        ('Устройство наружного освещения', 64, 'шт.', 18500),
    ]),
]


def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _gen_code(cur, base):
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
    names = [c[0] for c in CONTRACTORS.values()]
    stage_names = [s[1] for s in STAGES]
    # этапы объекта 8, созданные нами (по именам), кроме этапа Львова
    cur.execute("SELECT id FROM construction_stages WHERE object_id=%s AND name = ANY(%s) AND id != %s",
                (OBJECT_ID, stage_names, KEEP_STAGE_ID))
    stage_ids = [r[0] for r in cur.fetchall()]
    for sid in stage_ids:
        cur.execute("DELETE FROM approval_steps WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
        cur.execute("DELETE FROM package_items WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
        cur.execute("DELETE FROM package_documents WHERE package_id IN (SELECT id FROM doc_packages WHERE stage_id=%s)", (sid,))
        cur.execute("DELETE FROM doc_packages WHERE stage_id=%s", (sid,))
        cur.execute("DELETE FROM substages WHERE stage_id=%s", (sid,))
        cur.execute("DELETE FROM construction_stages WHERE id=%s", (sid,))
    # подрядные организации (только если на них нет других этапов)
    for nm in names:
        cur.execute("SELECT id FROM organizations WHERE name=%s", (nm,))
        row = cur.fetchone()
        if not row:
            continue
        oid = row[0]
        cur.execute("SELECT COUNT(*) FROM construction_stages WHERE contractor_id=%s", (oid,))
        if cur.fetchone()[0] == 0:
            cur.execute("DELETE FROM tenant_settings WHERE organization_id=%s", (oid,))
            cur.execute("DELETE FROM organizations WHERE id=%s", (oid,))
    print(f'Удалено этапов: {len(stage_ids)}, подрядчики очищены. Этап Львова (id={KEEP_STAGE_ID}) не тронут.')


def seed():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # защита от повторного запуска
    cur.execute("SELECT COUNT(*) c FROM construction_stages WHERE object_id=%s AND name = ANY(%s)",
                (OBJECT_ID, [s[1] for s in STAGES]))
    if cur.fetchone()['c'] > 0:
        print('Данные уже созданы. Сначала: python3 seed_zhelezno.py --wipe')
        return

    # команда объекта — для approver_id пакетов
    cur.execute("SELECT role, user_id FROM object_team WHERE object_id=%s", (OBJECT_ID,))
    team = {r['role']: r['user_id'] for r in cur.fetchall()}
    manager_id = team.get('manager')

    # ── подрядчики ──
    contr_ids = {}
    for key, (name, inn, kpp) in CONTRACTORS.items():
        cur.execute("SELECT id FROM organizations WHERE name=%s", (name,))
        row = cur.fetchone()
        if row:
            contr_ids[key] = row['id']
            continue
        code = _gen_code(cur, ('C' + inn[-6:]))
        cur.execute(
            "INSERT INTO organizations (name, type, inn, kpp, join_code, status, created_by_org) "
            "VALUES (%s,'contractor',%s,%s,%s,'active',%s) RETURNING id",
            (name, inn, kpp, code, DEVELOPER_ID))
        contr_ids[key] = cur.fetchone()['id']

    # существующий этап Львова → его место в порядке
    cur.execute("UPDATE construction_stages SET order_num=%s WHERE id=%s", (KEEP_STAGE_ORDER, KEEP_STAGE_ID))

    # ── графики этапов (реалистичные сроки стройки 2023–2026) ──
    schedule = {
        1:  ('2023-04-03', '2023-06-16'),
        2:  ('2023-06-19', '2023-10-13'),
        3:  ('2023-10-16', '2024-11-29'),
        4:  ('2024-06-03', '2025-02-14'),
        5:  ('2024-10-07', '2024-12-20'),
        6:  ('2024-12-23', '2025-08-15'),
        7:  ('2025-03-03', '2025-11-14'),
        8:  ('2025-09-01', '2026-01-30'),
        10: ('2026-06-01', '2026-08-31'),   # благоустройство — план
    }

    stats = {'stages': 0, 'subs': 0, 'pkgs': 0, 'sum': 0.0}
    for order, sname, ckey, subs in STAGES:
        p_start, p_end = schedule[order]
        planned_future = order == 10
        st_status = 'planned' if planned_future else 'done'
        c_org = contr_ids[ckey]
        total = sum(v * p for _, v, _, p in subs)

        # факт-даты завершённых этапов (небольшое реалистичное отклонение)
        if st_status == 'done':
            a_start = (date.fromisoformat(p_start) + timedelta(days=random.randint(-2, 5))).isoformat()
            a_end = (date.fromisoformat(p_end) + timedelta(days=random.randint(-3, 18))).isoformat()
        else:
            a_start = a_end = None

        cur.execute(
            "INSERT INTO construction_stages "
            "(object_id, name, order_num, contractor_id, contractor_status, "
            " plan_start_date, plan_end_date, actual_start_date, actual_end_date, "
            " status, created_by, contract_amount) "
            "VALUES (%s,%s,%s,%s,'assigned',%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (OBJECT_ID, sname, order, c_org, p_start, p_end, a_start, a_end,
             st_status, manager_id, total))
        stage_id = cur.fetchone()['id']
        stats['stages'] += 1

        # подэтапы
        sub_ids = []
        n = len(subs)
        for i, (subname, vol, unit, price) in enumerate(subs):
            if planned_future:
                ss_status, ss_astart, ss_aend = 'not_started', None, None
            else:
                ss_status = 'approved'
                # факт подэтапа в пределах периода этапа
                d0, d1 = date.fromisoformat(p_start), date.fromisoformat(p_end)
                span = (d1 - d0).days
                s0 = d0 + timedelta(days=int(span * i / n))
                s1 = d0 + timedelta(days=int(span * (i + 1) / n))
                ss_astart = (s0 + timedelta(days=random.randint(0, 3))).isoformat()
                ss_aend = (s1 + timedelta(days=random.randint(-2, 6))).isoformat()
            cur.execute(
                "INSERT INTO substages "
                "(stage_id, name, volume, unit, unit_price, total_price, "
                " plan_start_date, plan_end_date, actual_start_date, actual_end_date, "
                " completed_at, status, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (stage_id, subname, vol, unit, price, vol * price,
                 p_start, p_end, ss_astart, ss_aend, ss_aend, ss_status, team.get('pto')))
            sub_ids.append((cur.fetchone()['id'], vol, price))
            stats['subs'] += 1
            stats['sum'] += vol * price

        # пакет КС (completed) для завершённого этапа — освоение
        if st_status == 'done':
            submitted = (date.fromisoformat(a_end) + timedelta(days=random.randint(2, 6))).isoformat()
            completed = (date.fromisoformat(submitted) + timedelta(days=random.randint(5, 12))).isoformat()
            cur.execute(
                "INSERT INTO doc_packages (stage_id, contractor_id, created_by, status, submitted_at, completed_at) "
                "VALUES (%s,%s,%s,'completed',%s,%s) RETURNING id",
                (stage_id, c_org, manager_id, submitted, completed))
            pkg_id = cur.fetchone()['id']
            for sid, vol, price in sub_ids:
                cur.execute("INSERT INTO package_items (package_id, substage_id, qty, unit_price, amount) "
                            "VALUES (%s,%s,%s,%s,%s)", (pkg_id, sid, vol, price, vol * price))
            for i, (role, _) in enumerate(config.APPROVAL_CHAIN, 1):
                acted = (date.fromisoformat(submitted) + timedelta(days=min(i, 10))).isoformat()
                cur.execute("INSERT INTO approval_steps (package_id, step_order, role, status, approver_id, acted_at) "
                            "VALUES (%s,%s,%s,'approved',%s,%s)", (pkg_id, i, role, team.get(role), acted))
            stats['pkgs'] += 1

    conn.commit()
    cur.close()
    conn.close()

    print('═' * 64)
    print('  ЖЕЛЕЗНО ПЕРМЬ — объект №8, дом на 12 блок-секций')
    print('═' * 64)
    print(f'  Подрядчиков создано: {len(CONTRACTORS)}')
    print(f'  Этапов: {stats["stages"]} (+ существующий этап отделки ИП Львов)')
    print(f'  Подэтапов: {stats["subs"]}, пакетов КС: {stats["pkgs"]}')
    print(f'  Сумма СМР по добавленным этапам: {stats["sum"]:,.0f} ₽'.replace(',', ' '))
    print()
    print('  Порядок этапов на объекте:')
    print('   1. Разработка котлована            → ООО «ПермГрунтСтрой»')
    print('   2. Устройство фундаментной плиты   → ООО «БазисФундамент»')
    print('   3. Монолитный каркас               → ООО «МонолитУрал»')
    print('   4. Наружные стены и перегородки    → ООО «КамКладка»')
    print('   5. Кровельные работы               → ООО «КровляСервис-Пермь»')
    print('   6. Фасадные работы                 → ООО «ФасадПрофиль»')
    print('   7. Внутренние инженерные сети      → ООО «ПермИнжСети»')
    print('   8. Отделка МОП                     → ООО «ОтделкаМОП»')
    print('   9. Чистовая отделка квартир        → ИП Львов (текущий, не тронут)')
    print('  10. Благоустройство территории      → ООО «ПермГрунтСтрой» (план)')


if __name__ == '__main__':
    if '--wipe' in sys.argv:
        conn = _conn()
        cur = conn.cursor()
        wipe(cur)
        conn.commit()
        conn.close()
    else:
        seed()
