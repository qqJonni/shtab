from datetime import date, timedelta

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from db import query_db
from helpers import role_required

DIGEST_ROLES = ('manager', 'admin', 'pto')


def register(app):

    @app.route('/objects/<int:obj_id>/digest')
    @login_required
    @role_required(*DIGEST_ROLES)
    def object_digest_view(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        # Период из query-параметра: week | month | custom
        period = request.args.get('period', 'week')
        try:
            period_days = int(request.args.get('days', 7))
        except ValueError:
            period_days = 7

        if period == 'month':
            period_days = 30
        elif period == 'week':
            period_days = 7
        # 'custom' — берём period_days из GET-параметра days

        from digest import object_digest
        d = object_digest(obj_id, period_days=period_days)

        return render_template(
            'digest/view.html',
            obj=obj, d=d,
            period=period, period_days=period_days,
        )
