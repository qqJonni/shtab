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


def get_schedule_data(object_id):
    """Данные Ганта по объекту: этапы с подэтапами, план/факт, риск, прогресс."""
    today = date.today().isoformat()
    stages = query_db(
        'SELECT cs.*, org.name as contractor_name '
        'FROM construction_stages cs '
        'LEFT JOIN organizations org ON cs.contractor_id = org.id '
        'WHERE cs.object_id = ? ORDER BY cs.order_num, cs.id', (object_id,))

    out = []
    dates = []
    for st in stages:
        s = dict(st)
        subs = [dict(r) for r in query_db(
            'SELECT * FROM substages WHERE stage_id = ? ORDER BY id', (s['id'],))]

        done_cnt = sum(1 for x in subs if x['status'] in ('done', 'closed', 'approved'))
        s['progress'] = round(done_cnt / len(subs) * 100) if subs else (100 if s['status'] == 'done' else 0)
        s['risk'] = _risk(s['status'], s['plan_end_date'], today, ('done',))

        # факт-полоса «в работе» тянется до сегодня
        s['bar_actual_end'] = s['actual_end_date'] or (today if s['actual_start_date'] else None)

        sub_rows = []
        for x in subs:
            fin = x['status'] in ('done', 'closed', 'approved')
            sub_rows.append({
                'id': x['id'], 'name': x['name'],
                'plan_start': x['plan_start_date'], 'plan_end': x['plan_end_date'],
                'actual_start': x['actual_start_date'],
                'actual_end': x['actual_end_date'] or (today if x['actual_start_date'] and not fin else x['actual_end_date']),
                'progress': 100 if fin else (50 if x['status'] == 'in_progress' else 0),
                'status': x['status'],
                'risk': _risk(x['status'], x['plan_end_date'], today, ('done', 'closed', 'approved')),
            })
            dates += [x['plan_start_date'], x['plan_end_date'], x['actual_start_date'], x['actual_end_date']]

        dates += [s['plan_start_date'], s['plan_end_date'], s['actual_start_date'], s['bar_actual_end']]
        out.append({
            'id': s['id'], 'name': s['name'], 'contractor': s['contractor_name'],
            'status': s['status'], 'progress': s['progress'], 'risk': s['risk'],
            'plan_start': s['plan_start_date'], 'plan_end': s['plan_end_date'],
            'actual_start': s['actual_start_date'], 'actual_end': s['bar_actual_end'],
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

    return {'range': {'start': rng_start, 'end': rng_end, 'today': today}, 'stages': out}


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
