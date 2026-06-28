from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db
from helpers import role_required


def register(app):

    @app.route('/admin/users')
    @login_required
    @role_required('admin', 'manager')
    def users_list():
        users = query_db(
            'SELECT u.*, o.name as org_name FROM users u '
            'LEFT JOIN organizations o ON u.organization_id = o.id '
            'ORDER BY u.is_approved, u.created_at DESC')
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/<int:user_id>/approve', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def user_approve(user_id):
        execute_db('UPDATE users SET is_approved = 1 WHERE id = ?', (user_id,))
        flash('Пользователь подтверждён.', 'success')
        return redirect(url_for('users_list'))

    @app.route('/admin/users/<int:user_id>/block', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def user_block(user_id):
        user = query_db('SELECT role FROM users WHERE id = ?', (user_id,), one=True)
        if user and user['role'] == 'admin':
            flash('Нельзя заблокировать администратора.', 'danger')
            return redirect(url_for('users_list'))
        execute_db('UPDATE users SET is_approved = 0 WHERE id = ?', (user_id,))
        flash('Пользователь заблокирован.', 'warning')
        return redirect(url_for('users_list'))

    @app.route('/admin/users/<int:user_id>/role', methods=['POST'])
    @login_required
    @role_required('admin')
    def user_change_role(user_id):
        new_role = request.form.get('role', '')
        if new_role not in config.ROLES:
            flash('Недопустимая роль.', 'danger')
            return redirect(url_for('users_list'))
        execute_db('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
        flash('Роль изменена.', 'success')
        return redirect(url_for('users_list'))

    @app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required('admin', 'manager')
    def user_edit(user_id):
        user = query_db(
            'SELECT u.*, o.name as org_name FROM users u '
            'LEFT JOIN organizations o ON u.organization_id = o.id WHERE u.id = ?',
            (user_id,), one=True)
        if not user:
            abort(404)
        orgs = query_db("SELECT id, name FROM organizations ORDER BY name")

        if request.method == 'POST':
            full_name = request.form.get('full_name', '').strip()
            username = request.form.get('username', '').strip()
            role = request.form.get('role', user['role'])
            org_id = request.form.get('organization_id', '').strip() or None
            new_password = request.form.get('new_password', '').strip()

            if not username or not full_name:
                flash('Заполните ФИО и логин.', 'danger')
                return redirect(url_for('user_edit', user_id=user_id))

            existing = query_db('SELECT id FROM users WHERE username = ? AND id != ?',
                                (username, user_id), one=True)
            if existing:
                flash('Логин занят.', 'danger')
                return redirect(url_for('user_edit', user_id=user_id))

            if role not in config.ROLES:
                role = user['role']

            db = get_db()
            db.execute('UPDATE users SET full_name=?, username=?, role=?, organization_id=? WHERE id=?',
                       (full_name, username, role, org_id, user_id))
            if new_password:
                from werkzeug.security import generate_password_hash
                db.execute('UPDATE users SET password_hash=? WHERE id=?',
                           (generate_password_hash(new_password), user_id))
            db.commit()
            flash('Пользователь обновлён.', 'success')
            return redirect(url_for('users_list'))

        return render_template('admin/user_edit.html', user=user, orgs=orgs)

    @app.route('/admin/users/<int:user_id>/delete', methods=['GET', 'POST'])
    @login_required
    @role_required('admin')
    def user_delete(user_id):
        if user_id == current_user.id:
            flash('Нельзя удалить самого себя.', 'danger')
            return redirect(url_for('users_list'))
        user = query_db('SELECT u.*, o.name as org_name FROM users u LEFT JOIN organizations o ON u.organization_id=o.id WHERE u.id=?', (user_id,), one=True)
        if not user:
            abort(404)

        links = _get_user_links(user_id)

        if request.method == 'POST':
            db = get_db()
            db.execute('DELETE FROM notifications WHERE user_id = ?', (user_id,))
            db.execute('UPDATE defects SET reporter_id = 1 WHERE reporter_id = ?', (user_id,))
            db.execute('UPDATE defects SET responsible_id = NULL WHERE responsible_id = ?', (user_id,))
            db.execute('UPDATE defect_history SET user_id = 1 WHERE user_id = ?', (user_id,))
            db.execute('UPDATE defect_photos SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE stage_documents SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE substage_photos SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE substages SET created_by = NULL WHERE created_by = ?', (user_id,))
            db.execute('UPDATE construction_stages SET created_by = NULL WHERE created_by = ?', (user_id,))
            db.execute('UPDATE objects SET created_by = NULL WHERE created_by = ?', (user_id,))
            db.execute('UPDATE doc_packages SET created_by = 1 WHERE created_by = ?', (user_id,))
            db.execute('UPDATE material_requests SET requested_by = 1 WHERE requested_by = ?', (user_id,))
            db.execute('UPDATE material_request_history SET user_id = 1 WHERE user_id = ?', (user_id,))
            db.execute('UPDATE approval_steps SET approver_id = NULL WHERE approver_id = ?', (user_id,))
            db.execute('UPDATE guest_tokens SET created_by = NULL WHERE created_by = ?', (user_id,))
            db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            db.commit()
            flash(f'Пользователь «{user["username"]}» удалён. Связи откреплены.', 'success')
            return redirect(url_for('users_list'))

        return render_template('admin/user_delete.html', user=user, links=links)


def _get_user_links(user_id):
    links = []

    cnt = query_db('SELECT COUNT(*) as c FROM notifications WHERE user_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-bell', 'label': 'Уведомления', 'count': cnt, 'action': 'Будут удалены'})

    cnt = query_db('SELECT COUNT(*) as c FROM defects WHERE reporter_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-exclamation-triangle', 'label': 'Замечания (автор)', 'count': cnt, 'action': 'Автор → admin'})

    cnt = query_db('SELECT COUNT(*) as c FROM defect_history WHERE user_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-clock-history', 'label': 'История замечаний', 'count': cnt, 'action': 'Автор → admin'})

    cnt = query_db('SELECT COUNT(*) as c FROM objects WHERE created_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-building', 'label': 'Объекты (создатель)', 'count': cnt, 'action': 'Открепить'})

    cnt = query_db('SELECT COUNT(*) as c FROM construction_stages WHERE created_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-layers', 'label': 'Этапы (создатель)', 'count': cnt, 'action': 'Открепить'})

    cnt = query_db('SELECT COUNT(*) as c FROM substages WHERE created_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-list-check', 'label': 'Подэтапы (создатель)', 'count': cnt, 'action': 'Открепить'})

    cnt = query_db('SELECT COUNT(*) as c FROM doc_packages WHERE created_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-folder', 'label': 'Пакеты документов', 'count': cnt, 'action': 'Автор → admin'})

    cnt = query_db('SELECT COUNT(*) as c FROM approval_steps WHERE approver_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-check2-square', 'label': 'Согласования', 'count': cnt, 'action': 'Открепить'})

    cnt = query_db('SELECT COUNT(*) as c FROM material_requests WHERE requested_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-box-seam', 'label': 'Заявки на материал', 'count': cnt, 'action': 'Автор → admin'})

    cnt = query_db('SELECT COUNT(*) as c FROM stage_documents WHERE uploaded_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-file-earmark', 'label': 'Документы этапов', 'count': cnt, 'action': 'Открепить'})

    cnt = query_db('SELECT COUNT(*) as c FROM guest_tokens WHERE created_by=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-link-45deg', 'label': 'Гостевые ссылки', 'count': cnt, 'action': 'Открепить'})

    return links
