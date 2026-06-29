from flask import render_template, abort, request
from flask_login import login_required, current_user

from db import query_db
from helpers import role_required

VIEWERS = ('manager', 'admin', 'pto', 'inspector', 'foreman')


def register(app):

    @app.route('/plans/<int:plan_id>')
    @login_required
    def plan_view(plan_id):
        plan = query_db('SELECT * FROM object_plans WHERE id = ?', (plan_id,), one=True)
        if not plan:
            abort(404)
        obj = query_db('SELECT * FROM objects WHERE id = ?', (plan['object_id'],), one=True)
        if not obj:
            abort(404)
        if current_user.role not in VIEWERS and not (
            current_user.role == 'contractor' and query_db(
                'SELECT id FROM construction_stages WHERE object_id=? AND contractor_id=?',
                (obj['id'], current_user.organization_id), one=True)):
            abort(403)

        status_filter = request.args.get('status', '')

        where_status = ''
        args = [plan_id]
        if status_filter:
            where_status = ' AND d.status = ?'
            args.append(status_filter)

        defects = query_db(
            'SELECT d.id, d.title, d.status, d.priority, d.pin_x, d.pin_y, '
            'u.full_name as reporter_name, org.name as contractor_name '
            'FROM defects d '
            'LEFT JOIN users u ON d.reporter_id = u.id '
            'LEFT JOIN organizations org ON d.contractor_id = org.id '
            f'WHERE d.plan_id = ? AND d.pin_x IS NOT NULL{where_status}',
            args)
        pins = [dict(d) for d in defects]

        # Counts by status for filter
        all_defects = query_db(
            'SELECT status, COUNT(*) as cnt FROM defects '
            'WHERE plan_id = ? AND pin_x IS NOT NULL GROUP BY status',
            (plan_id,))
        status_counts = {r['status']: r['cnt'] for r in all_defects}
        total_pins = sum(status_counts.values())

        can_create = current_user.role in ('inspector', 'foreman', 'admin')

        return render_template('plans/view.html', plan=plan, obj=obj, pins=pins,
                               status_counts=status_counts, total_pins=total_pins,
                               status_filter=status_filter, can_create=can_create)
