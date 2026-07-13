"""Страница «Настройки»: личные / организация (тенант) / платформа."""
import json
from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from db import query_db
from helpers import (role_required, CHAIN_ROLES, get_tenant_setting, set_tenant_setting,
                     _parse_chain, MODULE_ACCESS, _access_roles,
                     NOTIFY_CHANNELS, NOTIFY_CHANNEL_LABELS, NOTIFY_TYPES,
                     get_user_setting, set_user_setting, _json_list)

WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']

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
        access = None
        tnotify = None
        import config as _cfg
        if area == 'organization' and current_user.organization_id:
            org = query_db('SELECT * FROM organizations WHERE id = ?',
                           (current_user.organization_id,), one=True)
            oid = current_user.organization_id
            chains = {
                'ks': _parse_chain(get_tenant_setting(oid, 'approval_chain_ks'), 'approval_chain_ks'),
                'id': _parse_chain(get_tenant_setting(oid, 'approval_chain_id'), 'approval_chain_id'),
            }
            access = {m: _access_roles(oid, m) for m in MODULE_ACCESS}
            tnotify = {
                'channels': set(_json_list(get_tenant_setting(oid, 'notify_channels'), NOTIFY_CHANNELS)),
                'types': set(_json_list(get_tenant_setting(oid, 'notify_types'), list(NOTIFY_TYPES))),
                'digest_enabled': get_tenant_setting(oid, 'digest_enabled', '1') == '1',
                'digest_weekday': int(get_tenant_setting(oid, 'digest_weekday', '0')),
            }

        # личные настройки уведомлений
        pnotify = {
            'email': get_user_setting(current_user.id, 'channel_email', '1') == '1',
            'push': get_user_setting(current_user.id, 'channel_push', '1') == '1',
            'digest': get_user_setting(current_user.id, 'digest_subscribed', '1') == '1',
        }

        return render_template('settings/index.html',
                               area=area, areas=areas, org=org,
                               chains=chains, chain_roles=CHAIN_ROLES,
                               access=access,
                               access_assignable=ACCESS_ASSIGNABLE,
                               module_labels=MODULE_LABELS,
                               tnotify=tnotify, pnotify=pnotify,
                               notify_channel_labels=NOTIFY_CHANNEL_LABELS,
                               notify_types=NOTIFY_TYPES, weekdays=WEEKDAYS,
                               email_enabled=_cfg.email_enabled())

    @app.route('/settings/personal/notifications', methods=['POST'])
    @login_required
    def settings_personal_notify():
        set_user_setting(current_user.id, 'channel_email', '1' if request.form.get('channel_email') else '0')
        set_user_setting(current_user.id, 'channel_push', '1' if request.form.get('channel_push') else '0')
        set_user_setting(current_user.id, 'digest_subscribed', '1' if request.form.get('digest_subscribed') else '0')
        flash('Настройки уведомлений сохранены.', 'success')
        return redirect(url_for('settings_page', area='personal'))

    @app.route('/settings/organization/notifications', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_tenant_notify():
        org_id = _assert_org_settings(current_user)
        channels = [c for c in request.form.getlist('channels') if c in NOTIFY_CHANNELS]
        if 'in_app' not in channels:
            channels.append('in_app')   # базовый канал нельзя выключить
        types = [t for t in request.form.getlist('types') if t in NOTIFY_TYPES]
        set_tenant_setting(org_id, 'notify_channels', json.dumps(channels))
        set_tenant_setting(org_id, 'notify_types', json.dumps(types))
        set_tenant_setting(org_id, 'digest_enabled', '1' if request.form.get('digest_enabled') else '0')
        wd = request.form.get('digest_weekday', '0')
        set_tenant_setting(org_id, 'digest_weekday', wd if wd in [str(i) for i in range(7)] else '0')
        flash('Настройки уведомлений организации сохранены.', 'success')
        return redirect(url_for('settings_page', area='organization'))

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

    @app.route('/settings/organization/requisites', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_requisites_save():
        org_id = _assert_org_settings(current_user)
        from db import execute_db
        from helpers import save_org_logo
        fields = ('name', 'inn', 'kpp', 'ogrn', 'okpo', 'address', 'phone', 'email',
                  'rep_position', 'rep_name')
        vals = {f: request.form.get(f, '').strip() for f in fields}
        if not vals['name']:
            flash('Название организации обязательно.', 'danger')
            return redirect(url_for('settings_page', area='organization'))
        execute_db(
            'UPDATE organizations SET name=?, inn=?, kpp=?, ogrn=?, okpo=?, address=?, '
            'phone=?, email=?, rep_position=?, rep_name=? WHERE id=?',
            (*[vals[f] for f in fields], org_id))

        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename:
            fname = save_org_logo(logo_file, org_id)
            if fname:
                execute_db('UPDATE organizations SET logo=? WHERE id=?', (fname, org_id))
            else:
                flash('Логотип: недопустимый формат (нужен PNG/JPG/WEBP).', 'warning')
        flash('Реквизиты организации сохранены.', 'success')
        return redirect(url_for('settings_page', area='organization'))

    @app.route('/settings/organization/logo/delete', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def settings_logo_delete():
        org_id = _assert_org_settings(current_user)
        import os
        from db import execute_db
        row = query_db('SELECT logo FROM organizations WHERE id = ?', (org_id,), one=True)
        if row and row['logo']:
            import config as _c
            fp = os.path.join(_c.LOGOS_FOLDER, str(org_id), row['logo'])
            if os.path.exists(fp):
                os.remove(fp)
        execute_db('UPDATE organizations SET logo=NULL WHERE id=?', (org_id,))
        flash('Логотип удалён.', 'info')
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
