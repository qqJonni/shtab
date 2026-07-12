"""Страница «Настройки»: личные / организация (тенант) / платформа."""
from flask import render_template, abort
from flask_login import login_required, current_user

from db import query_db


def _areas_for(user):
    """Какие области настроек доступны пользователю."""
    areas = ['personal']
    if user.role == 'manager' and user.organization_id:
        areas.append('organization')
    if user.role == 'admin':
        areas += ['organization', 'platform']
    return areas


def register(app):

    @app.route('/settings')
    @app.route('/settings/<area>')
    @login_required
    def settings_page(area='personal'):
        areas = _areas_for(current_user)
        if area not in areas:
            abort(403)

        org = None
        if area == 'organization' and current_user.organization_id:
            org = query_db('SELECT * FROM organizations WHERE id = ?',
                           (current_user.organization_id,), one=True)

        return render_template('settings/index.html',
                               area=area, areas=areas, org=org)
