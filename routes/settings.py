"""Страница «Настройки»: личные / организация (тенант) / платформа."""
import json
from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from db import query_db
from helpers import (role_required, CHAIN_ROLES, get_tenant_setting, set_tenant_setting,
                     _parse_chain, MODULE_ACCESS, _access_roles)

# Роли, доступные для назначения в переключателях модулей (без admin — он всегда)
ACCESS_ASSIGNABLE = {
    'manager': 'Руководитель', 'pto': 'Инженер ПТО', 'inspector': 'Технадзор',
    'foreman': 'Прораб', 'accountant': 'Бухгалтер', 'supply': 'Снабженец',
    'contractor': 'Подрядчик',
}
MODULE_LABELS = {
    'gpr':     'График производства работ',
    'finance': 'Финансы и суммы',
    'digest':  'Дайджест / сводка',
}


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
        chains = None
        if area == 'organization' and current_user.organization_id:
            org = query_db('SELECT * FROM organizations WHERE id = ?',
                           (current_user.organization_id,), one=True)
            oid = current_user.organization_id
            chains = {
                'ks': _parse_chain(get_tenant_setting(oid, 'approval_chain_ks'), 'approval_chain_ks'),
                'id': _parse_chain(get_tenant_setting(oid, 'approval_chain_id'), 'approval_chain_id'),
            }
            access = {m: _access_roles(oid, m) for m in MODULE_ACCESS}

        return render_template('settings/index.html',
                               area=area, areas=areas, org=org,
                               chains=chains, chain_roles=CHAIN_ROLES,
                               access=access if org else None,
                               access_assignable=ACCESS_ASSIGNABLE,
                               module_labels=MODULE_LABELS)

    def _assert_org_settings(user):
        """Тенант, чьи настройки правит пользователь (manager своей орг). admin — 403 без орг."""
        if user.role == 'manager' and user.organization_id:
            return user.organization_id
        abort(403)

    @app.route('/settings/organization/chains', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_chains_save():
        org_id = _assert_org_settings(current_user)
        kind = request.form.get('kind', '')
        if kind not in ('ks', 'id'):
            abort(400)
        roles = request.form.getlist('roles')
        # валидация: непустой, из допустимого набора, без дублей
        if not roles:
            flash('Цепочка не может быть пустой — оставьте хотя бы одну роль.', 'danger')
            return redirect(url_for('settings_page', area='organization'))
        if any(r not in CHAIN_ROLES for r in roles):
            flash('Недопустимая роль в цепочке.', 'danger')
            return redirect(url_for('settings_page', area='organization'))
        if len(set(roles)) != len(roles):
            flash('Роль не может повторяться в цепочке.', 'danger')
            return redirect(url_for('settings_page', area='organization'))
        set_tenant_setting(org_id, f'approval_chain_{kind}', json.dumps(roles))
        label = 'КС' if kind == 'ks' else 'ИД'
        flash(f'Цепочка согласования {label} сохранена. Применится к новым пакетам.', 'success')
        return redirect(url_for('settings_page', area='organization'))

    @app.route('/settings/organization/access', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_access_save():
        org_id = _assert_org_settings(current_user)
        module = request.form.get('module', '')
        if module not in MODULE_ACCESS:
            abort(400)
        roles = [r for r in request.form.getlist('roles') if r in ACCESS_ASSIGNABLE]
        # manager всегда сохраняет доступ к финансам и дайджесту — иначе тенант
        # может случайно закрыть себе управление; для gpr это не критично
        if module in ('finance', 'digest') and 'manager' not in roles:
            roles.append('manager')
        set_tenant_setting(org_id, MODULE_ACCESS[module][0], json.dumps(roles))
        flash(f'Доступ к модулю «{MODULE_LABELS[module]}» обновлён.', 'success')
        return redirect(url_for('settings_page', area='organization'))

    @app.route('/settings/organization/access/reset', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_access_reset():
        org_id = _assert_org_settings(current_user)
        module = request.form.get('module', '')
        if module not in MODULE_ACCESS:
            abort(400)
        from db import execute_db
        execute_db('DELETE FROM tenant_settings WHERE organization_id = ? AND key = ?',
                   (org_id, MODULE_ACCESS[module][0]))
        flash('Доступ сброшен к стандартному.', 'info')
        return redirect(url_for('settings_page', area='organization'))

    @app.route('/settings/organization/chains/reset', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_chains_reset():
        org_id = _assert_org_settings(current_user)
        kind = request.form.get('kind', '')
        if kind not in ('ks', 'id'):
            abort(400)
        from db import execute_db
        execute_db('DELETE FROM tenant_settings WHERE organization_id = ? AND key = ?',
                   (org_id, f'approval_chain_{kind}'))
        flash('Цепочка сброшена к стандартной.', 'info')
        return redirect(url_for('settings_page', area='organization'))
