"""График производства работ (ГПР): Гант, план-факт."""
from datetime import date, timedelta

from flask import render_template, abort
from flask_login import login_required, current_user

from db import query_db
from helpers import assert_object_access

RISK_AHEAD_DAYS = 7   # «под риском»: плановый финиш ближе N дней, работа не завершена


def _risk(status, plan_end, today, done_statuses):
    """Цвет полосы: done / suspended / overdue / risk / on_track."""
    if status in done_statuses:
        return 'done'
    if status == 'suspended':
        return 'suspended'
    if plan_end:
        if plan_end < today:
            return 'overdue'
        if plan_end <= (date.fromisoformat(today) + timedelta(days=RISK_AHEAD_DAYS)).isoformat():
            return 'risk'
    return 'on_track'


def _days(a, b):
    """b − a в днях (ISO-строки)."""
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _deviation(plan_end, actual_end, finished, today):
    """Отклонение по срокам в днях: >0 опережение, <0 отставание, None — нет данных.

    Завершено: plan_end − actual_end.
    В работе:  если plan_end уже прошёл — накопленное отставание −(today − plan_end),
               иначе 0 (идёт по графику, судить рано)."""
    if not plan_end:
        return None
    if finished and actual_end:
        return _days(actual_end, plan_end)
    if not finished:
        return _days(today, plan_end) if plan_end < today else 0
    return None


def _closed_volumes(object_id):
    """Закрытые объёмы по подэтапам объекта из completed-пакетов.
    qty IS NULL в строке пакета = полное закрытие подэтапа."""
    rows = query_db(
        "SELECT pi.substage_id, "
        "SUM(pi.qty) as qty_sum, COUNT(*) FILTER (WHERE pi.qty IS NULL) as full_cnt "
        "FROM package_items pi "
        "JOIN doc_packages dp ON pi.package_id = dp.id "
        "JOIN construction_stages cs ON dp.stage_id = cs.id "
        "WHERE dp.status = 'completed' AND cs.object_id = ? "
        "GROUP BY pi.substage_id", (object_id,))
    return {r['substage_id']: r for r in rows}


def get_schedule_data(object_id):
    """Данные Ганта и план-факта по объекту."""
    today = date.today().isoformat()
    stages = query_db(
        'SELECT cs.*, org.name as contractor_name '
        'FROM construction_stages cs '
        'LEFT JOIN organizations org ON cs.contractor_id = org.id '
        'WHERE cs.object_id = ? ORDER BY cs.order_num, cs.id', (object_id,))
    closed = _closed_volumes(object_id)

    out = []
    dates = []
    for st in stages:
        s = dict(st)
        subs = [dict(r) for r in query_db(
            'SELECT * FROM substages WHERE stage_id = ? ORDER BY id', (s['id'],))]

        s['risk'] = _risk(s['status'], s['plan_end_date'], today, ('done',))
        # факт-полоса «в работе» тянется до сегодня
        s['bar_actual_end'] = s['actual_end_date'] or (today if s['actual_start_date'] else None)

        sub_rows = []
        st_plan_cost = 0.0   # плановая стоимость этапа
        st_done_cost = 0.0   # «выполненная» стоимость (для взвешенного %)
        for x in subs:
            fin = x['status'] in ('done', 'closed', 'approved')
            volume = float(x['volume']) if x['volume'] is not None else None
            price = float(x['unit_price']) if x['unit_price'] is not None else 0.0
            cost = float(x['total_price']) if x['total_price'] is not None else (volume or 0) * price

            # закрытый объём: из completed-пакетов; полное закрытие = весь объём
            c = closed.get(x['id'])
            closed_qty = float(c['qty_sum']) if c and c['qty_sum'] is not None else 0.0
            if c and c['full_cnt'] and volume is not None:
                closed_qty = volume
            if volume is not None:
                closed_qty = min(closed_qty, volume)

            # % выполнения: по объёму если есть; иначе по статусу
            if volume:
                progress = round(closed_qty / volume * 100)
                if fin:
                    progress = 100
            else:
                progress = 100 if fin else (50 if x['status'] == 'in_progress' else 0)

            st_plan_cost += cost
            st_done_cost += cost * progress / 100

            dev = _deviation(x['plan_end_date'], x['actual_end_date'], fin, today)
            row = {
                'id': x['id'], 'name': x['name'],
                'plan_start': x['plan_start_date'], 'plan_end': x['plan_end_date'],
                'actual_start': x['actual_start_date'],
                'actual_end': x['actual_end_date'] or (today if x['actual_start_date'] and not fin else x['actual_end_date']),
                'volume': volume, 'unit': x['unit'], 'closed_qty': closed_qty if volume else None,
                'progress': progress, 'status': x['status'],
                'deviation': dev,
                'risk': _risk(x['status'], x['plan_end_date'], today, ('done', 'closed', 'approved')),
            }
            sub_rows.append(row)
            dates += [x['plan_start_date'], x['plan_end_date'], x['actual_start_date'], x['actual_end_date']]

        # % этапа: взвешенный по стоимости; без цен — по числу завершённых
        if st_plan_cost > 0:
            s['progress'] = round(st_done_cost / st_plan_cost * 100)
        else:
            done_cnt = sum(1 for x in sub_rows if x['progress'] == 100)
            s['progress'] = round(done_cnt / len(sub_rows) * 100) if sub_rows else (100 if s['status'] == 'done' else 0)
        if s['status'] == 'done':
            s['progress'] = 100

        dates += [s['plan_start_date'], s['plan_end_date'], s['actual_start_date'], s['bar_actual_end']]
        out.append({
            'id': s['id'], 'name': s['name'], 'contractor': s['contractor_name'],
            'status': s['status'], 'progress': s['progress'], 'risk': s['risk'],
            'plan_start': s['plan_start_date'], 'plan_end': s['plan_end_date'],
            'actual_start': s['actual_start_date'], 'actual_end': s['bar_actual_end'],
            'deviation': _deviation(s['plan_end_date'], s['actual_end_date'], s['status'] == 'done', today),
            'plan_cost': st_plan_cost, 'done_cost': st_done_cost,
            'subs': sub_rows,
        })

    dates = [d for d in dates if d]
    if dates:
        rng_start = min(min(dates), today)
        rng_end = max(max(dates), today)
    else:
        rng_start = rng_end = today
    # паддинг диапазона
    rng_start = (date.fromisoformat(rng_start) - timedelta(days=7)).isoformat()
    rng_end = (date.fromisoformat(rng_end) + timedelta(days=14)).isoformat()

    # Итоги по объекту
    total_cost = sum(s['plan_cost'] for s in out)
    done_cost = sum(s['done_cost'] for s in out)
    all_rows = out + [x for s in out for x in s['subs']]
    overdue_cnt = sum(1 for s in out for x in s['subs'] if x['risk'] == 'overdue')
    # суммарное отставание: по подэтапам с отрицательным отклонением
    lag_total = sum(-x['deviation'] for s in out for x in s['subs']
                    if x['deviation'] is not None and x['deviation'] < 0)
    totals = {
        'progress': round(done_cost / total_cost * 100) if total_cost else (
            round(sum(s['progress'] for s in out) / len(out)) if out else 0),
        'plan_cost': total_cost, 'done_cost': done_cost,
        'overdue': overdue_cnt, 'lag_days': lag_total,
        'stages_total': len(out),
    }

    return {'range': {'start': rng_start, 'end': rng_end, 'today': today},
            'stages': out, 'totals': totals}


def register(app):

    @app.route('/objects/<int:obj_id>/schedule')
    @login_required
    def object_schedule(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)

        data = get_schedule_data(obj_id)
        return render_template('schedule/view.html', obj=obj, data=data)
