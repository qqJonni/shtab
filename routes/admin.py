from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db
from helpers import role_required


def register(app):

    def _linked_contractor_org_ids(tenant_org_id):
        """Contractor orgs linked to the tenant: created by it OR working on its objects."""
        rows = query_db(
            "SELECT id FROM organizations WHERE type = 'contractor' AND created_by_org = ? "
            "UNION "
            "SELECT DISTINCT cs.contractor_id FROM construction_stages cs "
            "JOIN objects ob ON cs.object_id = ob.id "
            "WHERE ob.developer_id = ? AND cs.contractor_id IS NOT NULL",
            (tenant_org_id, tenant_org_id))
        return [r['id'] for r in rows]

    @app.route('/admin/users')
    @login_required
    @role_required('admin', 'manager')
    def users_list():
        if current_user.role == 'manager':
            # manager sees users of their own org + users of linked contractor orgs
            org_ids = [current_user.organization_id] + _linked_contractor_org_ids(current_user.organization_id)
            users = query_db(
                'SELECT u.*, o.name as org_name FROM users u '
                'LEFT JOIN organizations o ON u.organization_id = o.id '
                'WHERE u.organization_id = ANY(?) '
                'ORDER BY u.is_approved, u.created_at DESC',
                (org_ids,))
        else:
            users = query_db(
                'SELECT u.*, o.name as org_name FROM users u '
                'LEFT JOIN organizations o ON u.organization_id = o.id '
                'ORDER BY u.is_approved, u.created_at DESC')
        return render_template('admin/users.html', users=users)

    def _assert_same_tenant(user_id):
        """Returns (user_row, is_own_org). Aborts 403 if manager cannot act on the user.

        Manager can act on users of their own org, and on users of linked
        contractor orgs (created by the tenant or working on its objects)."""
        target = query_db('SELECT * FROM users WHERE id = ?', (user_id,), one=True)
        if not target:
            abort(404)
        if current_user.role != 'manager':
            return target, True
        if target['organization_id'] == current_user.organization_id:
            return target, True
        if target['organization_id'] in _linked_contractor_org_ids(current_user.organization_id):
            return target, False
        abort(403)

    @app.route('/admin/users/<int:user_id>/approve', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def user_approve(user_id):
        target, is_own_org = _assert_same_tenant(user_id)
        role = request.form.get('role', '').strip()
        if is_own_org:
            assignable = [r for r in config.ROLES if r not in ('admin', 'guest')]
        else:
            # users of linked contractor orgs can only be approved as contractor
            assignable = ['contractor']
        if role not in assignable:
            if not is_own_org:
                flash('Пользователю подрядной организации можно назначить только роль «Подрядчик».', 'danger')
            else:
                flash('Выберите роль перед подтверждением.', 'danger')
            return redirect(url_for('users_list'))
        execute_db('UPDATE users SET is_approved = 1, role = ? WHERE id = ?', (role, user_id))
        flash('Пользователь подтверждён и роль назначена.', 'success')
        return redirect(url_for('users_list'))

    @app.route('/admin/users/<int:user_id>/block', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def user_block(user_id):
        user, _ = _assert_same_tenant(user_id)
        if user['role'] == 'admin':
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
        _, is_own_org = _assert_same_tenant(user_id)
        # manager может редактировать данные (ФИО/логин/пароль) только своих;
        # пользователей подрядных организаций — лишь подтверждать/блокировать
        if current_user.role == 'manager' and not is_own_org:
            abort(403)
        user = query_db(
            'SELECT u.*, o.name as org_name FROM users u '
            'LEFT JOIN organizations o ON u.organization_id = o.id WHERE u.id = ?',
            (user_id,), one=True)
        if not user:
            abort(404)
        # admin can reassign org; manager cannot change org at all
        orgs = query_db("SELECT id, name FROM organizations ORDER BY name") if current_user.role == 'admin' else []

        if request.method == 'POST':
            full_name = request.form.get('full_name', '').strip()
            username = request.form.get('username', '').strip()
            role = request.form.get('role', user['role'])
            # manager: org stays fixed; admin: can change
            if current_user.role == 'admin':
                org_id = request.form.get('organization_id', '').strip() or None
            else:
                org_id = user['organization_id']
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
            db.execute('DELETE FROM object_team WHERE user_id = ?', (user_id,))
            db.execute('DELETE FROM push_subscriptions WHERE user_id = ?', (user_id,))
            db.execute('UPDATE id_approval_steps SET approver_id = NULL WHERE approver_id = ?', (user_id,))
            db.execute('UPDATE id_checklist_items SET created_by = NULL WHERE created_by = ?', (user_id,))
            db.execute('UPDATE id_documents SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE id_packages SET created_by = 1 WHERE created_by = ?', (user_id,))
            db.execute('UPDATE journal_entries SET author_id = 1 WHERE author_id = ?', (user_id,))
            db.execute('UPDATE defect_audio SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE object_plans SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('UPDATE smeta_imports SET uploaded_by = NULL WHERE uploaded_by = ?', (user_id,))
            db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            db.commit()
            flash(f'Пользователь «{user["username"]}» удалён. Связи откреплены.', 'success')
            return redirect(url_for('users_list'))

        return render_template('admin/user_delete.html', user=user, links=links)

    # ═══ Организации ═══

    @app.route('/admin/organizations')
    @login_required
    @role_required('admin')
    def org_list():
        orgs = query_db(
            'SELECT o.*, COUNT(u.id) as user_count '
            'FROM organizations o LEFT JOIN users u ON u.organization_id = o.id '
            'GROUP BY o.id ORDER BY o.type, o.name')
        return render_template('admin/organizations.html', orgs=orgs)

    @app.route('/admin/organizations/add', methods=['GET', 'POST'])
    @login_required
    @role_required('admin')
    def org_add():
        import secrets
        if request.method == 'POST':
            name    = request.form.get('name', '').strip()
            org_type = request.form.get('type', 'developer')
            if org_type not in ('developer', 'contractor'):
                org_type = 'developer'
            if not name:
                flash('Введите название организации.', 'danger')
                return render_template('admin/org_add.html')
            # generate unique join_code
            for _ in range(10):
                code = secrets.token_urlsafe(6)[:8].upper()
                if not query_db('SELECT 1 FROM organizations WHERE join_code = ?', (code,), one=True):
                    break
            execute_db(
                "INSERT INTO organizations (name, type, join_code, status) VALUES (?, ?, ?, 'active')",
                (name, org_type, code)
            )
            org = query_db('SELECT id FROM organizations WHERE join_code = ?', (code,), one=True)
            flash(
                f'Организация «{name}» создана. Код для регистрации: <strong>{code}</strong>. '
                'Передайте его сотрудникам.',
                'success'
            )
            return redirect(url_for('org_list'))
        return render_template('admin/org_add.html')

    @app.route('/admin/organizations/<int:org_id>/toggle', methods=['POST'])
    @login_required
    @role_required('admin')
    def org_toggle(org_id):
        org = query_db('SELECT status FROM organizations WHERE id = ?', (org_id,), one=True)
        if not org:
            abort(404)
        new_status = 'inactive' if org['status'] == 'active' else 'active'
        execute_db('UPDATE organizations SET status = ? WHERE id = ?', (new_status, org_id))
        flash('Статус организации изменён.', 'success')
        return redirect(url_for('org_list'))

    @app.route('/admin/organizations/<int:org_id>/regen_code', methods=['POST'])
    @login_required
    @role_required('admin')
    def org_regen_code(org_id):
        import secrets
        org = query_db('SELECT id FROM organizations WHERE id = ?', (org_id,), one=True)
        if not org:
            abort(404)
        for _ in range(10):
            code = secrets.token_urlsafe(6)[:8].upper()
            if not query_db('SELECT 1 FROM organizations WHERE join_code = ? AND id != ?', (code, org_id), one=True):
                break
        execute_db('UPDATE organizations SET join_code = ? WHERE id = ?', (code, org_id))
        flash(f'Новый код организации: <strong>{code}</strong>', 'success')
        return redirect(url_for('org_list'))


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

    cnt = query_db('SELECT COUNT(*) as c FROM object_team WHERE user_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-people-fill', 'label': 'Команды объектов', 'count': cnt, 'action': 'Будут удалены'})

    cnt = query_db('SELECT COUNT(*) as c FROM id_approval_steps WHERE approver_id=?', (user_id,), one=True)['c']
    if cnt:
        links.append({'icon': 'bi-file-earmark-check', 'label': 'Согласования ИД', 'count': cnt, 'action': 'Открепить'})

    return links
