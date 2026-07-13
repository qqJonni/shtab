"""
seed_journal_zhelezno.py — реалистичный журнал производства работ
для объекта «дом №3» застройщика «Железно Пермь».

Генерирует записи журнала по факт-датам этапов (из seed_zhelezno):
вид работ, подрядчик этапа, автор (прораб/технадзор), погода Перми
по сезону, осмысленное описание за день. Текущий этап чистовой
отделки — больше свежих записей.

ID определяются динамически — работает локально и на проде.

Запуск:  python3 seed_journal_zhelezno.py          # создать
         python3 seed_journal_zhelezno.py --wipe   # удалить журнал объекта №3
"""

import sys
import random
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

import config

random.seed(303)


def _conn():
    return psycopg2.connect(config.DATABASE_URL)


def _resolve(cur):
    cur.execute("SELECT id FROM organizations WHERE name LIKE '%Железно%' AND type='developer' ORDER BY id LIMIT 1")
    r = cur.fetchone()
    if not r:
        raise SystemExit('Застройщик «Железно» не найден.')
    dev_id = r['id']
    cur.execute("SELECT id, name FROM objects WHERE developer_id=%s AND name LIKE '%%№3%%' ORDER BY id LIMIT 1", (dev_id,))
    r = cur.fetchone()
    if not r:
        raise SystemExit('Объект «дом №3» не найден.')
    return dev_id, r['id'], r['name']


# Погода Перми по месяцам (диапазон °C, типичные явления)
WEATHER = {
    1: ('−18…−12 °C', ['ясно, морозно', 'снег, метель', 'облачно, −15 °C']),
    2: ('−16…−8 °C',  ['снег', 'ясно, −12 °C', 'облачно с прояснениями']),
    3: ('−6…+2 °C',   ['облачно, мокрый снег', 'ясно, −3 °C', 'переменная облачность']),
    4: ('+3…+12 °C',  ['ясно, +8 °C', 'дождь', 'облачно, ветрено']),
    5: ('+10…+20 °C', ['ясно, +17 °C', 'кратковременный дождь', 'облачно, +14 °C']),
    6: ('+15…+25 °C', ['ясно, жарко +24 °C', 'гроза во второй половине дня', 'переменная облачность']),
    7: ('+18…+27 °C', ['ясно, +26 °C', 'кратковременный дождь', 'жарко, +25 °C']),
    8: ('+16…+25 °C', ['ясно, +23 °C', 'облачно, тёплый дождь', 'ясно, +21 °C']),
    9: ('+8…+17 °C',  ['облачно, +12 °C', 'дождь', 'ясно, +15 °C']),
    10:('+2…+9 °C',   ['дождь, ветрено', 'облачно, +6 °C', 'первый снег']),
    11:('−5…+2 °C',   ['снег с дождём', 'облачно, −2 °C', 'морозно, ясно']),
    12:('−14…−6 °C',  ['снег', 'ясно, −10 °C', 'метель, −12 °C']),
}


def _weather(d):
    rng, phen = WEATHER[d.month]
    return f'{rng}, {random.choice(phen)}'


# Шаблоны записей по ключевому слову в названии этапа.
# {bs} — блок-секция, {n} — число, {vol} — объём, {fl} — этаж
TEMPLATES = {
    'котлован': [
        'Разработка грунта экскаватором в осях {a}-{b}. Вывезено {n} а/самосвалов.',
        'Ведётся водопонижение иглофильтровой установкой по периметру котлована.',
        'Зачистка дна котлована вручную, вынос осей под фундаментную плиту.',
        'Обратная засыпка пазух с послойным уплотнением, секция БС-{bs}.',
        'Приёмка основания котлована с представителем технадзора. Отклонений нет.',
    ],
    'фундамент': [
        'Устройство бетонной подготовки под фундаментную плиту, захватка {n}.',
        'Оклеечная гидроизоляция плиты, секции БС-{bs}, БС-{bs2}.',
        'Вязка арматурных каркасов фундаментной плиты, ось {a}-{b}.',
        'Бетонирование фундаментной плиты, уложено {vol} м³ бетона В25 W8.',
        'Уход за бетоном плиты, набор прочности. Распалубка бортов.',
    ],
    'монолит': [
        'Бетонирование колонн {fl} этажа, секция БС-{bs}. Уложено {vol} м³.',
        'Монтаж опалубки перекрытия {fl} этажа, армирование в два слоя.',
        'Бетонирование перекрытия {fl} этажа, секция БС-{bs}, {vol} м³.',
        'Распалубка колонн и стен {fl} этажа. Устройство лестничного марша.',
        'Вязка арматуры стен и диафрагм жёсткости {fl} этажа, секция БС-{bs}.',
        'Геодезический контроль вертикальности колонн, секции БС-{bs}, БС-{bs2}.',
    ],
    'стены': [
        'Кладка наружных стен из газобетона, {fl} этаж, секция БС-{bs}.',
        'Монтаж перегородок из газобетонных блоков, секция БС-{bs}.',
        'Устройство перемычек и армопоясов над проёмами, {fl} этаж.',
        'Монтаж оконных блоков ПВХ, секция БС-{bs}, {n} шт. за смену.',
    ],
    'кровель': [
        'Устройство пароизоляции кровли над секциями БС-{bs}, БС-{bs2}.',
        'Укладка утеплителя (минплита) в два слоя, {vol} м².',
        'Наплавление нижнего слоя кровельного ковра, захватка {n}.',
        'Устройство парапетов и примыканий к вентшахтам. Монтаж воронок.',
    ],
    'фасад': [
        'Монтаж подсистемы вентфасада, секция БС-{bs}, {fl}-{fl2} этажи.',
        'Установка утеплителя фасада с креплением тарельчатыми дюбелями.',
        'Облицовка керамогранитом, секция БС-{bs}, {vol} м² за смену.',
        'Остекление лоджий, секция БС-{bs}, {n} шт.',
        'Устройство откосов и отливов оконных проёмов, {fl} этаж.',
    ],
    'инженер': [
        'Монтаж стояков отопления, секция БС-{bs}, {fl}-{fl2} этажи.',
        'Прокладка сетей ХВС/ГВС по этажам, секция БС-{bs}.',
        'Монтаж канализационных стояков и лежаков в техподполье.',
        'Электромонтаж этажных щитов и стояковой разводки, секция БС-{bs}.',
        'Прокладка слаботочных сетей (СКС, домофония), секция БС-{bs}.',
        'Опрессовка системы отопления, секция БС-{bs}. Замечаний нет.',
    ],
    'моп': [
        'Штукатурка стен лестничных клеток, секция БС-{bs}, {fl}-{fl2} этажи.',
        'Шпаклёвка и окраска стен и потолков МОП, секция БС-{bs}.',
        'Устройство стяжки полов в лифтовых холлах, секция БС-{bs}.',
        'Облицовка полов керамогранитом, входная группа секции БС-{bs}.',
        'Монтаж ограждений и поручней лестниц, секция БС-{bs}.',
        'Установка дверных и тамбурных блоков МОП, {n} шт.',
    ],
    'чистов': [
        'Штукатурка и шпаклёвка стен квартир, секция БС-{bs}, {fl} этаж.',
        'Облицовка санузлов керамической плиткой, секция БС-{bs}.',
        'Устройство натяжных потолков, секция БС-{bs}, {n} квартир.',
        'Оклейка стен обоями, укладка ламината, секция БС-{bs}.',
        'Монтаж сантехприборов (ванны, унитазы, раковины), секция БС-{bs}.',
        'Установка светильников и электроустановочных изделий, секция БС-{bs}.',
        'Устройство плинтусов, установка порогов, финишная уборка, секция БС-{bs}.',
    ],
    'благоустрой': [
        'Устройство оснований проездов и площадок.',
        'Асфальтирование внутриквартальных проездов.',
        'Укладка тротуарной плитки, монтаж бортового камня.',
        'Озеленение территории, посадка деревьев и кустарников.',
    ],
}
DEFAULT_TPL = ['Производятся работы по этапу. Ведётся исполнительная документация.']


def _templates_for(stage_name):
    nl = stage_name.lower()
    for key, tpls in TEMPLATES.items():
        if key in nl:
            return tpls
    return DEFAULT_TPL


def _fill(tpl):
    bs = random.randint(1, 12)
    bs2 = random.choice([x for x in range(1, 13) if x != bs])
    fl = random.randint(1, 10)
    return tpl.format(
        a=random.randint(1, 6), b=random.randint(7, 14),
        n=random.randint(3, 24), vol=random.choice([48, 96, 132, 210, 340, 480]),
        fl=fl, fl2=min(fl + random.randint(1, 3), 10),
        bs=bs, bs2=bs2)


def _workdays(d0, d1, count):
    """Выбирает count рабочих дат (пн-сб) в [d0, d1]."""
    span = (d1 - d0).days
    if span <= 0:
        return [d0]
    picks = set()
    tries = 0
    while len(picks) < count and tries < count * 20:
        tries += 1
        cand = d0 + timedelta(days=random.randint(0, span))
        if cand.weekday() != 6:   # не воскресенье
            picks.add(cand)
    return sorted(picks)


def wipe(cur):
    dev_id, obj_id, obj_name = _resolve(cur)
    cur.execute("SELECT id FROM journal_entries WHERE object_id=%s", (obj_id,))
    ids = [r['id'] for r in cur.fetchall()]
    for eid in ids:
        cur.execute("DELETE FROM journal_photos WHERE entry_id=%s", (eid,))
    cur.execute("DELETE FROM journal_entries WHERE object_id=%s", (obj_id,))
    print(f'Удалено записей журнала: {len(ids)} (объект «{obj_name}»).')


def seed():
    conn = _conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    dev_id, obj_id, obj_name = _resolve(cur)

    cur.execute("SELECT COUNT(*) c FROM journal_entries WHERE object_id=%s", (obj_id,))
    if cur.fetchone()['c'] > 0:
        print('Журнал уже заполнен. Сначала: python3 seed_journal_zhelezno.py --wipe')
        return

    # авторы: прораб и технадзор объекта
    cur.execute("SELECT role, user_id FROM object_team WHERE object_id=%s AND role IN ('foreman','inspector','pto')",
                (obj_id,))
    team = {r['role']: r['user_id'] for r in cur.fetchall()}
    foreman = team.get('foreman') or team.get('pto')
    inspector = team.get('inspector')
    authors = [a for a in (foreman, foreman, foreman, inspector) if a]  # чаще прораб
    if not authors:
        # fallback — любой пользователь тенанта
        cur.execute("SELECT id FROM users WHERE organization_id=%s LIMIT 1", (dev_id,))
        authors = [cur.fetchone()['id']]

    # этапы с факт-датами (пропускаем planned без факта)
    cur.execute(
        "SELECT id, name, contractor_id, status, actual_start_date, actual_end_date, "
        "plan_start_date, plan_end_date FROM construction_stages "
        "WHERE object_id=%s ORDER BY order_num, id", (obj_id,))
    stages = cur.fetchall()

    today = date.today()
    count = 0
    for st in stages:
        if st['status'] == 'planned':
            continue   # ещё не начат — записей нет
        # период записей
        d0 = date.fromisoformat(st['actual_start_date'] or st['plan_start_date'])
        if st['status'] == 'done':
            d1 = date.fromisoformat(st['actual_end_date'] or st['plan_end_date'])
            n_entries = random.randint(3, 6)
        else:  # in_progress (чистовая отделка) — до сегодня, больше записей
            d1 = min(today, date.fromisoformat(st['plan_end_date'] or today.isoformat()))
            n_entries = random.randint(10, 15)
        d1 = min(d1, today)
        tpls = _templates_for(st['name'])
        for wd in _workdays(d0, d1, n_entries):
            author = random.choice(authors)
            # технадзор иногда пишет контрольную запись
            text = _fill(random.choice(tpls))
            if author == inspector and random.random() < 0.5:
                text = 'Контрольный обход. ' + text + ' Качество соответствует проекту.'
            cur.execute(
                "INSERT INTO journal_entries (object_id, author_id, entry_date, text, weather, work_type, contractor_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (obj_id, author, wd.isoformat(), text, _weather(wd),
                 st['name'], st['contractor_id'], wd.isoformat() + ' 18:00:00'))
            count += 1

    conn.commit()
    cur.close()
    conn.close()
    print('═' * 60)
    print(f'  ЖУРНАЛ РАБОТ — объект «{obj_name}»')
    print(f'  Создано записей: {count}')
    print(f'  Период: по факт-датам этапов (котлован → текущая отделка)')
    print('═' * 60)


if __name__ == '__main__':
    if '--wipe' in sys.argv:
        conn = _conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        wipe(cur)
        conn.commit()
        conn.close()
    else:
        seed()
