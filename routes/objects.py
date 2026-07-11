import os
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db, notify
from helpers import role_required, accessible_object_ids, assert_object_access, \
    get_object_team, TEAM_ROLES, linked_contractor_org_ids, \
    save_stage_document, save_substage_photo, save_plan_file

VIEWERS = ('manager', 'admin', 'pto', 'inspector', 'foreman')
EDITORS = ('manager', 'admin')
SUBSTAGE_EDITORS = ('pto', 'manager', 'admin')

DOC_TYPE_LABELS = {
    'contract': 'Договор',
    'tech_spec': 'Техническое задание',
    'price_doc': 'Ценовой документ',
    'work_schedule': 'График производства работ',
    'other': 'Прочее',
}


def register(app):

    # ═══ Организации ═══

    def _manager_org_scope():
        """ID организаций, доступных текущему manager: своя + связанные подрядные."""
        return [current_user.organization_id] + linked_contractor_org_ids(current_user.organization_id)

    def _assert_org_access(org_id):
        """403, если manager обращается к организации вне своего скоупа."""
        if current_user.role == 'manager' and org_id not in _manager_org_scope():
            abort(403)

    @app.route('/organizations')
    @login_required
    @role_required('admin', 'manager')
    def organizations_list():
        if current_user.role == 'manager':
            orgs = query_db('SELECT * FROM organizations WHERE id = ANY(?) ORDER BY type, name',
                            (_manager_org_scope(),))
        else:
            orgs = query_db('SELECT * FROM organizations ORDER BY type, name')
        return render_template('organizations/list.html', orgs=orgs)

    @app.route('/organizations/<int:org_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required('admin', 'manager')
    def organization_edit(org_id):
        _assert_org_access(org_id)
        org = query_db('SELECT * FROM organizations WHERE id = ?', (org_id,), one=True)
        if not org:
            abort(404)
        if request.method == 'POST':
            execute_db(
                'UPDATE organizations SET name=?, inn=?, kpp=?, address=?, ogrn=?, okpo=?, phone=?, email=?, '
                'rep_position=?, rep_name=? WHERE id=?',
                (request.form.get('name', '').strip(),
                 request.form.get('inn', '').strip(),
                 request.form.get('kpp', '').strip(),
                 request.form.get('address', '').strip(),
                 request.form.get('ogrn', '').strip(),
                 request.form.get('okpo', '').strip(),
                 request.form.get('phone', '').strip(),
                 request.form.get('email', '').strip(),
                 request.form.get('rep_position', '').strip(),
                 request.form.get('rep_name', '').strip(),
                 org_id))
            flash('Организация обновлена.', 'success')
            return redirect(url_for('organizations_list'))
        free_contractors = query_db(
            "SELECT id, full_name, username FROM users WHERE role='contractor' "
            "AND (organization_id IS NULL) AND is_approved=1 ORDER BY full_name")
        return render_template('organizations/edit.html', org=org,
                               contractors=_get_contractor_users(org_id),
                               all_contractors=free_contractors)

    @app.route('/organizations/add', methods=['GET', 'POST'])
    @login_required
    @role_required('admin', 'manager')
    def organization_add():
        contractors = query_db(
            "SELECT id, full_name, username FROM users WHERE role='contractor' "
            "AND (organization_id IS NULL) AND is_approved=1 ORDER BY full_name")
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Введите название организации.', 'danger')
                return render_template('organizations/add.html', contractors=contractors)
            org_type = request.form.get('type', 'contractor')
            if org_type not in ('developer', 'contractor'):
                org_type = 'contractor'

            import secrets
            join_code = None
            for _ in range(10):
                code = secrets.token_urlsafe(6)[:8].upper()
                if not query_db('SELECT 1 FROM organizations WHERE join_code = ?', (code,), one=True):
                    join_code = code
                    break

            created_by_org = current_user.organization_id if current_user.role == 'manager' else None

            db = get_db()
            cur = db.execute(
                'INSERT INTO organizations (name, type, inn, kpp, address, ogrn, okpo, phone, email, rep_position, rep_name, join_code, status, created_by_org) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (name, org_type,
                 request.form.get('inn', '').strip(),
                 request.form.get('kpp', '').strip(),
                 request.form.get('address', '').strip(),
                 request.form.get('ogrn', '').strip(),
                 request.form.get('okpo', '').strip(),
                 request.form.get('phone', '').strip(),
                 request.form.get('email', '').strip(),
                 request.form.get('rep_position', '').strip(),
                 request.form.get('rep_name', '').strip(),
                 join_code, 'active', created_by_org))
            org_id = cur.lastrowid

            linked = request.form.getlist('contractor_ids')
            for uid in linked:
                db.execute('UPDATE users SET organization_id = ? WHERE id = ? AND role = ?',
                           (org_id, int(uid), 'contractor'))
            db.commit()

            flash(f'Организация «{name}» создана. Код для регистрации сотрудников: {join_code}', 'success')
            return redirect(url_for('organizations_list'))

        return render_template('organizations/add.html', contractors=contractors)

    def _get_contractor_users(org_id):
        return query_db(
            'SELECT id, full_name, username FROM users WHERE organization_id = ? ORDER BY full_name',
            (org_id,))

    @app.route('/organizations/<int:org_id>/link-user', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def organization_link_user(org_id):
        _assert_org_access(org_id)
        user_id = request.form.get('user_id', '')
        if user_id:
            execute_db('UPDATE users SET organization_id = ? WHERE id = ? AND role = ?',
                       (org_id, int(user_id), 'contractor'))
            flash('Подрядчик привязан к организации.', 'success')
        return redirect(url_for('organization_edit', org_id=org_id))

    @app.route('/organizations/<int:org_id>/unlink-user/<int:user_id>', methods=['POST'])
    @login_required
    @role_required('admin', 'manager')
    def organization_unlink_user(org_id, user_id):
        _assert_org_access(org_id)
        execute_db('UPDATE users SET organization_id = NULL WHERE id = ? AND organization_id = ?',
                   (user_id, org_id))
        flash('Подрядчик откреплён от организации.', 'success')
        return redirect(url_for('organization_edit', org_id=org_id))

    @app.route('/organizations/<int:org_id>/delete', methods=['GET', 'POST'])
    @login_required
    @role_required('admin', 'manager')
    def organization_delete(org_id):
        _assert_org_access(org_id)
        org = query_db('SELECT * FROM organizations WHERE id = ?', (org_id,), one=True)
        if not org:
            abort(404)

        links = []
        cnt = query_db('SELECT COUNT(*) as c FROM users WHERE organization_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-people', 'label': 'Пользователи', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM construction_stages WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-layers', 'label': 'Этапы (подрядчик)', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM defects WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-exclamation-triangle', 'label': 'Замечания', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM doc_packages WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-folder', 'label': 'Пакеты документов', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM material_requests WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-box-seam', 'label': 'Заявки на материал', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM id_packages WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-file-earmark-check', 'label': 'Пакеты ИД', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM journal_entries WHERE contractor_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-journal-text', 'label': 'Записи журнала', 'count': cnt, 'action': 'Открепить'})
        cnt = query_db('SELECT COUNT(*) as c FROM objects WHERE developer_id=?', (org_id,), one=True)['c']
        if cnt:
            links.append({'icon': 'bi-building', 'label': 'Объекты (застройщик)', 'count': cnt, 'action': 'Открепить'})

        if request.method == 'POST':
            db = get_db()
            db.execute('UPDATE users SET organization_id = NULL WHERE organization_id = ?', (org_id,))
            db.execute("UPDATE construction_stages SET contractor_id = NULL, contractor_status = 'search' WHERE contractor_id = ?", (org_id,))
            db.execute('UPDATE defects SET contractor_id = NULL WHERE contractor_id = ?', (org_id,))
            db.execute('UPDATE doc_packages SET contractor_id = NULL WHERE contractor_id = ?', (org_id,))
            db.execute('UPDATE material_requests SET contractor_id = NULL WHERE contractor_id = ?', (org_id,))
            db.execute('UPDATE id_packages SET contractor_id = NULL WHERE contractor_id = ?', (org_id,))
            db.execute('UPDATE journal_entries SET contractor_id = NULL WHERE contractor_id = ?', (org_id,))
            db.execute('UPDATE objects SET developer_id = NULL WHERE developer_id = ?', (org_id,))
            db.execute('DELETE FROM organizations WHERE id = ?', (org_id,))
            db.commit()
            flash(f'Организация «{org["name"]}» удалена. Связи откреплены.', 'success')
            return redirect(url_for('organizations_list'))

        return render_template('organizations/delete.html', org=org, links=links)

    # ═══ Объекты ═══

    @app.route('/objects')
    @login_required
    @role_required(*VIEWERS)
    def objects_list():
        status = request.args.get('status', 'active')
        ids = accessible_object_ids(current_user)
        if not ids:
            objects = []
        else:
            objects = query_db(
                'SELECT o.*, u.full_name as creator_name '
                'FROM objects o LEFT JOIN users u ON o.created_by = u.id '
                'WHERE o.status = ? AND o.id = ANY(?) ORDER BY o.created_at DESC',
                (status, ids),
            )
        return render_template('objects/list.html', objects=objects, status=status)

    @app.route('/objects/add', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def object_add():
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            address = request.form.get('address', '').strip()
            obj_type = request.form.get('type', '').strip()
            construction_name = request.form.get('construction_name', '').strip()
            construction_address = request.form.get('construction_address', '').strip()
            cadastral_number = request.form.get('cadastral_number', '').strip()

            if not name:
                flash('Введите название объекта.', 'danger')
                return render_template('objects/form.html', obj=None)

            developer_id = current_user.organization_id if current_user.role in ('manager', 'pto', 'inspector', 'foreman', 'supply', 'accountant') else None
            execute_db(
                'INSERT INTO objects (name, address, type, construction_name, construction_address, cadastral_number, created_by, developer_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (name, address, obj_type, construction_name, construction_address, cadastral_number, current_user.id, developer_id),
            )
            flash('Объект создан.', 'success')
            return redirect(url_for('objects_list'))

        return render_template('objects/form.html', obj=None)

    @app.route('/objects/<int:obj_id>')
    @login_required
    @role_required(*VIEWERS)
    def object_detail(obj_id):
        obj = query_db('SELECT o.*, u.full_name as creator_name FROM objects o LEFT JOIN users u ON o.created_by = u.id WHERE o.id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)
        stages = query_db(
            'SELECT cs.*, org.name as contractor_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'WHERE cs.object_id = ? ORDER BY cs.order_num',
            (obj_id,),
        )
        stages_list = [dict(s) for s in stages]
        total_subs = 0
        total_done = 0
        for s in stages_list:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            s['sub_total'] = len(subs)
            s['sub_done'] = sum(1 for sub in subs if sub['status'] in ('done', 'closed', 'approved'))
            total_subs += s['sub_total']
            total_done += s['sub_done']
            # ИД-готовность для карточки этапа
            id_items = query_db(
                'SELECT ci.id, ci.title, ci.is_required, '
                '(SELECT COUNT(*) FROM id_documents WHERE item_id = ci.id) as file_count '
                'FROM id_checklist_items ci WHERE ci.stage_id = ?', (s['id'],))
            s['id_total'] = len(id_items)
            s['id_req_total'] = sum(1 for i in id_items if i['is_required'])
            s['id_req_done'] = sum(1 for i in id_items if i['is_required'] and i['file_count'] > 0)
            s['id_missing'] = [i['title'] for i in id_items if i['is_required'] and i['file_count'] == 0]
        progress = round(total_done / total_subs * 100) if total_subs > 0 else 0

        defects_open = query_db(
            "SELECT COUNT(*) as c FROM defects WHERE object_id=? AND status NOT IN ('closed','verified')",
            (obj_id,), one=True)['c']
        defects_closed = query_db(
            "SELECT COUNT(*) as c FROM defects WHERE object_id=? AND status IN ('closed','verified')",
            (obj_id,), one=True)['c']
        packages_active = query_db(
            "SELECT COUNT(*) as c FROM doc_packages dp "
            "JOIN construction_stages cs ON dp.stage_id=cs.id "
            "WHERE cs.object_id=? AND dp.status IN ('in_review','returned')", (obj_id,), one=True)['c']
        mr_active = query_db(
            "SELECT COUNT(*) as c FROM material_requests mr "
            "JOIN construction_stages cs ON mr.stage_id=cs.id "
            "WHERE cs.object_id=? AND mr.status NOT IN ('completed')", (obj_id,), one=True)['c']

        guest_token = query_db('SELECT token FROM guest_tokens WHERE object_id=?', (obj_id,), one=True)

        plans = query_db('SELECT * FROM object_plans WHERE object_id=? ORDER BY sort_order, id', (obj_id,))

        team = get_object_team(obj_id)
        # Пользователи тенанта для назначения в команду
        tenant_users = {}
        if current_user.role in ('manager', 'admin'):
            dev_id = obj.get('developer_id') or (current_user.organization_id if current_user.role != 'admin' else None)
            for role_key, _ in TEAM_ROLES:
                if dev_id:
                    users = query_db(
                        "SELECT id, full_name FROM users WHERE role = ? AND organization_id = ? AND is_approved = 1 ORDER BY full_name",
                        (role_key, dev_id))
                else:
                    users = query_db(
                        "SELECT id, full_name FROM users WHERE role = ? AND is_approved = 1 ORDER BY full_name",
                        (role_key,))
                tenant_users[role_key] = list(users)

        return render_template('objects/detail.html', obj=obj, stages=stages_list,
                               progress=progress, total_subs=total_subs, total_done=total_done,
                               defects_open=defects_open, defects_closed=defects_closed,
                               packages_active=packages_active, mr_active=mr_active,
                               guest_token=guest_token, plans=plans,
                               team=team, tenant_users=tenant_users, team_roles=TEAM_ROLES)

    @app.route('/objects/<int:obj_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def object_edit(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            address = request.form.get('address', '').strip()
            obj_type = request.form.get('type', '').strip()
            construction_name = request.form.get('construction_name', '').strip()
            construction_address = request.form.get('construction_address', '').strip()
            cadastral_number = request.form.get('cadastral_number', '').strip()

            if not name:
                flash('Введите название объекта.', 'danger')
                return render_template('objects/form.html', obj=obj)

            execute_db(
                'UPDATE objects SET name=?, address=?, type=?, construction_name=?, construction_address=?, cadastral_number=? WHERE id=?',
                (name, address, obj_type, construction_name, construction_address, cadastral_number, obj_id),
            )
            flash('Объект обновлён.', 'success')
            return redirect(url_for('object_detail', obj_id=obj_id))

        return render_template('objects/form.html', obj=obj)

    @app.route('/objects/<int:obj_id>/archive', methods=['POST'])
    @login_required
    @role_required(*EDITORS)
    def object_archive(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)
        new_status = 'active' if obj['status'] == 'archived' else 'archived'
        execute_db('UPDATE objects SET status = ? WHERE id = ?', (new_status, obj_id))
        label = 'восстановлен' if new_status == 'active' else 'архивирован'
        flash(f'Объект {label}.', 'success')
        return redirect(url_for('objects_list'))

    @app.route('/objects/<int:obj_id>/delete', methods=['POST'])
    @login_required
    @role_required(*EDITORS)
    def object_delete(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)
        db = get_db()
        db.execute('DELETE FROM objects WHERE id = ?', (obj_id,))
        db.commit()
        flash(f'Объект «{obj["name"]}» удалён.', 'success')
        return redirect(url_for('objects_list'))

    # ═══ Планы объекта ═══

    PLAN_EDITORS = ('manager', 'pto', 'admin')

    @app.route('/objects/<int:obj_id>/plans/upload', methods=['POST'])
    @login_required
    @role_required(*PLAN_EDITORS)
    def plan_upload(obj_id):
        obj = query_db('SELECT id FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)
        file = request.files.get('file')
        title = request.form.get('title', '').strip()
        level_label = request.form.get('level_label', '').strip()

        if not file or not file.filename:
            flash('Выберите файл плана.', 'danger')
            return redirect(url_for('object_detail', obj_id=obj_id))

        filename = save_plan_file(file, obj_id)
        if not filename:
            flash('Недопустимый формат. Допустимы: PNG, JPG, WEBP.', 'danger')
            return redirect(url_for('object_detail', obj_id=obj_id))

        if not title:
            title = file.filename

        max_order = query_db('SELECT MAX(sort_order) as mx FROM object_plans WHERE object_id=?',
                             (obj_id,), one=True)['mx'] or 0

        execute_db(
            'INSERT INTO object_plans (object_id, title, level_label, filename, sort_order, uploaded_by) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (obj_id, title, level_label, filename, max_order + 1, current_user.id))
        flash('План загружен.', 'success')
        return redirect(url_for('object_detail', obj_id=obj_id))

    @app.route('/objects/<int:obj_id>/plans/<int:plan_id>/delete', methods=['POST'])
    @login_required
    @role_required(*PLAN_EDITORS)
    def plan_delete(obj_id, plan_id):
        plan = query_db('SELECT * FROM object_plans WHERE id=? AND object_id=?', (plan_id, obj_id), one=True)
        if not plan:
            abort(404)
        assert_object_access(current_user, obj_id)
        filepath = os.path.join(config.PLANS_FOLDER, str(obj_id), plan['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        execute_db('UPDATE defects SET plan_id=NULL, pin_x=NULL, pin_y=NULL WHERE plan_id=?', (plan_id,))
        execute_db('DELETE FROM object_plans WHERE id=?', (plan_id,))
        flash('План удалён.', 'success')
        return redirect(url_for('object_detail', obj_id=obj_id, tab='plans'))

    # ═══ Команда объекта ═══

    @app.route('/objects/<int:obj_id>/team/assign', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def object_team_assign(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        assert_object_access(current_user, obj_id)
        role = request.form.get('role', '').strip()
        user_id = request.form.get('user_id', '').strip()
        valid_roles = [r for r, _ in TEAM_ROLES]
        if role not in valid_roles:
            flash('Неверная роль.', 'danger')
            return redirect(url_for('object_detail', obj_id=obj_id) + '#team')
        db = get_db()
        if not user_id:
            # Удалить из команды
            db.execute('DELETE FROM object_team WHERE object_id = ? AND role = ?', (obj_id, role))
            db.commit()
            flash('Участник убран из команды.', 'success')
            return redirect(url_for('object_detail', obj_id=obj_id) + '#team')
        uid = int(user_id)
        # Проверяем принадлежность пользователя тенанту
        dev_id = obj.get('developer_id')
        if dev_id:
            u = query_db('SELECT id FROM users WHERE id = ? AND organization_id = ? AND is_approved = 1',
                         (uid, dev_id), one=True)
        else:
            u = query_db('SELECT id FROM users WHERE id = ? AND is_approved = 1', (uid,), one=True)
        if not u:
            flash('Пользователь не найден или не в вашей организации.', 'danger')
            return redirect(url_for('object_detail', obj_id=obj_id) + '#team')
        # Upsert
        existing = query_db('SELECT id FROM object_team WHERE object_id = ? AND role = ?',
                            (obj_id, role), one=True)
        if existing:
            old_user_id = query_db('SELECT user_id FROM object_team WHERE object_id = ? AND role = ?',
                                   (obj_id, role), one=True)['user_id']
            db.execute('UPDATE object_team SET user_id = ? WHERE object_id = ? AND role = ?',
                       (uid, obj_id, role))
            # Переназначить pending-шаги КС этого объекта
            ks_pkg_ids = [r['id'] for r in query_db(
                'SELECT dp.id FROM doc_packages dp '
                'JOIN construction_stages cs ON dp.stage_id = cs.id '
                'WHERE cs.object_id = ?', (obj_id,))]
            if ks_pkg_ids:
                db.execute(
                    'UPDATE approval_steps SET approver_id = ? '
                    'WHERE approver_id = ? AND status = ? AND role = ? '
                    'AND package_id = ANY(?)',
                    (uid, old_user_id, 'pending', role, ks_pkg_ids))
            # Переназначить pending-шаги ИД этого объекта
            id_pkg_ids = [r['id'] for r in query_db(
                'SELECT ip.id FROM id_packages ip '
                'JOIN construction_stages cs ON ip.stage_id = cs.id '
                'WHERE cs.object_id = ?', (obj_id,))]
            if id_pkg_ids:
                db.execute(
                    'UPDATE id_approval_steps SET approver_id = ? '
                    'WHERE approver_id = ? AND status = ? AND role = ? '
                    'AND package_id = ANY(?)',
                    (uid, old_user_id, 'pending', role, id_pkg_ids))
        else:
            db.execute(
                'INSERT INTO object_team (object_id, role, user_id) VALUES (?, ?, ?)',
                (obj_id, role, uid))
        db.commit()
        flash('Команда обновлена.', 'success')
        return redirect(url_for('object_detail', obj_id=obj_id) + '#team')

    # ═══ Этапы строительства ═══

    def _next_order(obj_id):
        row = query_db('SELECT MAX(order_num) as mx FROM construction_stages WHERE object_id = ?',
                        (obj_id,), one=True)
        return (row['mx'] or 0) + 1

    @app.route('/objects/<int:obj_id>/stages/add', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def stage_add(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            order_num = request.form.get('order_num', '')
            plan_start = request.form.get('plan_start_date', '').strip() or None
            plan_end = request.form.get('plan_end_date', '').strip() or None
            status = request.form.get('status', 'planned')

            if not name:
                flash('Введите название этапа.', 'danger')
                return render_template('objects/stage_form.html', obj=obj, stage=None,
                                       next_order=_next_order(obj_id))

            try:
                order_num = int(order_num)
            except (ValueError, TypeError):
                order_num = _next_order(obj_id)

            execute_db(
                'INSERT INTO construction_stages '
                '(object_id, name, description, order_num, plan_start_date, plan_end_date, status, created_by) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (obj_id, name, description, order_num, plan_start, plan_end, status, current_user.id),
            )
            flash('Этап создан.', 'success')
            return redirect(url_for('object_detail', obj_id=obj_id))

        return render_template('objects/stage_form.html', obj=obj, stage=None,
                               next_order=_next_order(obj_id))

    @app.route('/stages/<int:stage_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def stage_edit(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)
        obj = query_db('SELECT * FROM objects WHERE id = ?', (stage['object_id'],), one=True)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            order_num = request.form.get('order_num', stage['order_num'])
            plan_start = request.form.get('plan_start_date', '').strip() or None
            plan_end = request.form.get('plan_end_date', '').strip() or None
            status = request.form.get('status', stage['status'])

            if not name:
                flash('Введите название этапа.', 'danger')
                return render_template('objects/stage_form.html', obj=obj, stage=stage, next_order=None)

            try:
                order_num = int(order_num)
            except (ValueError, TypeError):
                order_num = stage['order_num']

            # ── Гейт закрытия этапа ──────────────────────────────────────────
            if status == 'done' and stage['status'] != 'done':
                subs = query_db(
                    'SELECT status FROM substages WHERE stage_id = ?', (stage_id,))
                not_done = [s for s in subs if s['status'] not in ('done', 'closed', 'approved')]
                id_pkg = query_db(
                    "SELECT status FROM id_packages WHERE stage_id = ? ORDER BY id DESC LIMIT 1",
                    (stage_id,), one=True)
                has_id_items = query_db(
                    'SELECT COUNT(*) as c FROM id_checklist_items WHERE stage_id = ?',
                    (stage_id,), one=True)['c'] > 0
                id_not_accepted = has_id_items and (not id_pkg or id_pkg['status'] != 'accepted')

                reasons = []
                if not_done:
                    reasons.append(f'не завершены подэтапы ({len(not_done)} шт.)')
                if id_not_accepted:
                    reasons.append('пакет ИД не принят')
                if reasons:
                    flash(f'Нельзя закрыть этап: {"; ".join(reasons)}.', 'danger')
                    return render_template('objects/stage_form.html', obj=obj, stage=stage, next_order=None)

            execute_db(
                'UPDATE construction_stages SET name=?, description=?, order_num=?, '
                'plan_start_date=?, plan_end_date=?, status=? WHERE id=?',
                (name, description, order_num, plan_start, plan_end, status, stage_id),
            )
            # авто-факт этапа: закрытие → факт-финиш, откат из done → очистка
            from helpers import recalc_stage_actuals
            recalc_stage_actuals(stage_id)
            if status == 'done':
                from datetime import date
                execute_db(
                    'UPDATE construction_stages SET actual_end_date = COALESCE(actual_end_date, ?) WHERE id = ?',
                    (date.today().isoformat(), stage_id))
            flash('Этап обновлён.', 'success')
            return redirect(url_for('object_detail', obj_id=stage['object_id']))

        return render_template('objects/stage_form.html', obj=obj, stage=stage, next_order=None)

    @app.route('/stages/<int:stage_id>/delete', methods=['POST'])
    @login_required
    @role_required(*EDITORS)
    def stage_delete(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)
        execute_db('DELETE FROM construction_stages WHERE id = ?', (stage_id,))
        flash('Этап удалён.', 'success')
        return redirect(url_for('object_detail', obj_id=stage['object_id']))

    @app.route('/stages/<int:stage_id>/move/<direction>', methods=['POST'])
    @login_required
    @role_required(*EDITORS)
    def stage_move(stage_id, direction):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)

        if direction == 'up':
            neighbor = query_db(
                'SELECT * FROM construction_stages WHERE object_id = ? AND order_num < ? ORDER BY order_num DESC LIMIT 1',
                (stage['object_id'], stage['order_num']), one=True)
        else:
            neighbor = query_db(
                'SELECT * FROM construction_stages WHERE object_id = ? AND order_num > ? ORDER BY order_num ASC LIMIT 1',
                (stage['object_id'], stage['order_num']), one=True)

        if neighbor:
            db = get_db()
            db.execute('UPDATE construction_stages SET order_num = ? WHERE id = ?',
                       (neighbor['order_num'], stage['id']))
            db.execute('UPDATE construction_stages SET order_num = ? WHERE id = ?',
                       (stage['order_num'], neighbor['id']))
            db.commit()

        return redirect(url_for('object_detail', obj_id=stage['object_id']))

    # ═══ Назначение подрядчика ═══

    @app.route('/stages/<int:stage_id>/contractor', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def stage_contractor(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)
        obj = query_db('SELECT * FROM objects WHERE id = ?', (stage['object_id'],), one=True)
        contractors = query_db("SELECT id, name FROM organizations WHERE type = 'contractor' ORDER BY name")

        if request.method == 'POST':
            contractor_id = request.form.get('contractor_id', '').strip()
            contract_number = request.form.get('contract_number', '').strip()
            contract_date = request.form.get('contract_date', '').strip() or None
            contract_amount = request.form.get('contract_amount', '').strip() or None
            try:
                contract_amount = float(contract_amount) if contract_amount else None
            except ValueError:
                contract_amount = None

            execute_db(
                'UPDATE construction_stages SET contract_number=?, contract_date=?, contract_amount=? WHERE id=?',
                (contract_number, contract_date, contract_amount, stage_id))

            if contractor_id:
                contractor_id = int(contractor_id)
                org = query_db('SELECT name FROM organizations WHERE id = ?', (contractor_id,), one=True)
                execute_db(
                    "UPDATE construction_stages SET contractor_id = ?, contractor_status = 'assigned' WHERE id = ?",
                    (contractor_id, stage_id),
                )
                users = query_db(
                    'SELECT id FROM users WHERE organization_id = ? AND is_approved = 1',
                    (contractor_id,),
                )
                for u in users:
                    notify(
                        u['id'], 'stage',
                        f'Назначен этап «{stage["name"]}»',
                        f'Вам назначен этап «{stage["name"]}» на объекте «{obj["name"]}».',
                        f'/objects/{obj["id"]}',
                    )
                flash(f'Подрядчик «{org["name"]}» назначен на этап.', 'success')
            else:
                execute_db(
                    "UPDATE construction_stages SET contractor_id = NULL, contractor_status = 'search' WHERE id = ?",
                    (stage_id,),
                )
                flash('Подрядчик снят с этапа.', 'success')

            return redirect(url_for('object_detail', obj_id=stage['object_id']))

        return render_template('objects/stage_contractor.html',
                               obj=obj, stage=stage, contractors=contractors)

    # ═══ Мои этапы (подрядчик) ═══

    @app.route('/my-stages')
    @login_required
    @role_required('contractor')
    def my_stages():
        stages = query_db(
            'SELECT cs.*, o.name as object_name, o.address as object_address '
            'FROM construction_stages cs '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE cs.contractor_id = ? '
            'ORDER BY o.name, cs.order_num',
            (current_user.organization_id,),
        )
        stages_list = [dict(s) for s in stages]
        for s in stages_list:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            s['sub_total'] = len(subs)
            s['sub_done'] = sum(1 for sub in subs if sub['status'] in ('done', 'closed', 'approved'))
        return render_template('objects/my_stages.html', stages=stages_list)

    # ═══ Страница этапа и документы ═══

    def _can_view_stage(stage):
        if current_user.role in VIEWERS:
            return True
        if current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id:
            return True
        return False

    def _can_upload_doc(stage):
        if current_user.role in EDITORS or current_user.role == 'pto':
            return True
        if current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id:
            return True
        return False

    @app.route('/stages/<int:stage_id>')
    @login_required
    def stage_detail(stage_id):
        stage = query_db(
            'SELECT cs.*, org.name as contractor_name, o.name as object_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE cs.id = ?', (stage_id,), one=True)
        if not stage or not _can_view_stage(stage):
            abort(403)
        docs = query_db(
            'SELECT sd.*, u.full_name as uploader_name '
            'FROM stage_documents sd LEFT JOIN users u ON sd.uploaded_by = u.id '
            'WHERE sd.stage_id = ? ORDER BY sd.uploaded_at DESC', (stage_id,))
        substages = query_db(
            'SELECT * FROM substages WHERE stage_id = ? ORDER BY id', (stage_id,))
        total_sum = sum(s['total_price'] or 0 for s in substages)

        # ИД: состав чеклиста + файлы к каждому пункту
        id_items = query_db(
            'SELECT ci.*, t.name as type_name, '
            '(SELECT COUNT(*) FROM id_documents WHERE item_id = ci.id) as file_count '
            'FROM id_checklist_items ci '
            'LEFT JOIN id_item_types t ON ci.type_id = t.id '
            'WHERE ci.stage_id = ? ORDER BY ci.order_num, ci.id',
            (stage_id,))
        id_item_types = query_db('SELECT * FROM id_item_types ORDER BY order_num')
        id_files_by_item = {}
        for item in id_items:
            id_files_by_item[item['id']] = query_db(
                'SELECT idf.*, u.full_name as uploader_name '
                'FROM id_documents idf LEFT JOIN users u ON idf.uploaded_by = u.id '
                'WHERE idf.item_id = ? ORDER BY idf.uploaded_at',
                (item['id'],))
        id_required_total = sum(1 for i in id_items if i['is_required'])
        id_required_done = sum(1 for i in id_items if i['is_required'] and i['file_count'] > 0)
        id_missing = [i['title'] for i in id_items if i['is_required'] and i['file_count'] == 0]
        # Активный пакет ИД на этом этапе
        id_package = query_db(
            "SELECT * FROM id_packages WHERE stage_id = ? ORDER BY id DESC LIMIT 1",
            (stage_id,), one=True)

        # Индикатор готовности к закрытию этапа
        close_blockers = []
        if stage['status'] != 'done':
            not_done_subs = [s for s in substages if s['status'] not in ('done', 'closed', 'approved')]
            if not_done_subs:
                close_blockers.append(f'не завершены подэтапы ({len(not_done_subs)} шт.)')
            if id_items:
                id_not_accepted = not id_package or id_package['status'] != 'accepted'
                if id_not_accepted:
                    close_blockers.append('пакет ИД не принят')

        return render_template('objects/stage_detail.html',
                               stage=stage, docs=docs, doc_type_labels=DOC_TYPE_LABELS,
                               can_upload=_can_upload_doc(stage),
                               substages=substages, total_sum=total_sum,
                               id_items=id_items, id_item_types=id_item_types,
                               id_files_by_item=id_files_by_item,
                               id_required_total=id_required_total,
                               id_required_done=id_required_done,
                               id_missing=id_missing,
                               id_package=id_package,
                               close_blockers=close_blockers,
                               can_edit_id=current_user.role in ('manager','pto','inspector','admin'),
                               can_upload_id=current_user.role in ('manager','pto','admin') or (
                                   current_user.role in ('contractor','foreman') and
                                   stage['contractor_id'] == current_user.organization_id))

    @app.route('/stages/<int:stage_id>/docs/upload', methods=['POST'])
    @login_required
    def stage_doc_upload(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage or not _can_upload_doc(stage):
            abort(403)

        file = request.files.get('file')
        doc_type = request.form.get('doc_type', 'other')
        title = request.form.get('title', '').strip()

        if not file or not file.filename:
            flash('Выберите файл.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        if doc_type not in DOC_TYPE_LABELS:
            doc_type = 'other'

        filename = save_stage_document(file, stage_id)
        if not filename:
            flash('Недопустимый формат файла. Допустимы: pdf, doc, docx, xls, xlsx, jpg, png, zip, rar.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        if not title:
            title = file.filename

        execute_db(
            'INSERT INTO stage_documents (stage_id, doc_type, title, filename, uploaded_by) '
            'VALUES (?, ?, ?, ?, ?)',
            (stage_id, doc_type, title, filename, current_user.id),
        )
        flash('Документ загружен.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id))

    @app.route('/stages/<int:stage_id>/docs/<int:doc_id>/download')
    @login_required
    def stage_doc_download(stage_id, doc_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage or not _can_view_stage(stage):
            abort(403)
        doc = query_db('SELECT * FROM stage_documents WHERE id = ? AND stage_id = ?',
                       (doc_id, stage_id), one=True)
        if not doc:
            abort(404)
        folder = os.path.join(config.DOCS_FOLDER, str(stage_id))
        ext = doc['filename'].rsplit('.', 1)[-1] if '.' in doc['filename'] else ''
        dl_name = doc['title']
        if ext and not dl_name.lower().endswith('.' + ext.lower()):
            dl_name = f"{dl_name}.{ext}"
        return send_from_directory(folder, doc['filename'], as_attachment=True,
                                   download_name=dl_name)

    @app.route('/stages/<int:stage_id>/docs/<int:doc_id>/delete', methods=['POST'])
    @login_required
    def stage_doc_delete(stage_id, doc_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        doc = query_db('SELECT * FROM stage_documents WHERE id = ? AND stage_id = ?',
                       (doc_id, stage_id), one=True)
        if not stage or not doc:
            abort(404)
        can_delete = (current_user.role in EDITORS
                      or current_user.role == 'pto'
                      or (current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id))
        if not can_delete:
            abort(403)
        # Удаляем файл с диска
        filepath = os.path.join(config.DOCS_FOLDER, str(stage_id), doc['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        execute_db('DELETE FROM stage_documents WHERE id = ?', (doc_id,))
        flash('Документ удалён.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id))

    # ═══ Подэтапы (состав работ) ═══

    def _calc_total(volume, unit_price):
        if volume is not None and unit_price is not None:
            try:
                return round(float(volume) * float(unit_price), 2)
            except (ValueError, TypeError):
                pass
        return None

    @app.route('/stages/<int:stage_id>/substages/add', methods=['GET', 'POST'])
    @login_required
    @role_required(*SUBSTAGE_EDITORS)
    def substage_add(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            volume = request.form.get('volume', '').strip() or None
            unit = request.form.get('unit', '').strip()
            unit_price = request.form.get('unit_price', '').strip() or None
            plan_end = request.form.get('plan_end_date', '').strip() or None

            if not name:
                flash('Введите название подэтапа.', 'danger')
                return render_template('objects/substage_form.html', stage=stage, sub=None)

            if plan_end and stage['plan_end_date'] and plan_end > stage['plan_end_date']:
                flash(f'Срок подэтапа ({plan_end}) не может быть позже срока этапа ({stage["plan_end_date"]}).', 'danger')
                return render_template('objects/substage_form.html', stage=stage, sub=None)

            try:
                volume = float(volume) if volume else None
            except ValueError:
                volume = None
            try:
                unit_price = float(unit_price) if unit_price else None
            except ValueError:
                unit_price = None

            total_price = _calc_total(volume, unit_price)

            execute_db(
                'INSERT INTO substages (stage_id, name, description, volume, unit, unit_price, total_price, plan_end_date, created_by) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (stage_id, name, description, volume, unit, unit_price, total_price, plan_end, current_user.id),
            )
            flash('Подэтап создан.', 'success')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        return render_template('objects/substage_form.html', stage=stage, sub=None)

    @app.route('/substages/<int:sub_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required(*SUBSTAGE_EDITORS)
    def substage_edit(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (sub['stage_id'],), one=True)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            volume = request.form.get('volume', '').strip() or None
            unit = request.form.get('unit', '').strip()
            unit_price = request.form.get('unit_price', '').strip() or None
            plan_end = request.form.get('plan_end_date', '').strip() or None

            if not name:
                flash('Введите название подэтапа.', 'danger')
                return render_template('objects/substage_form.html', stage=stage, sub=sub)

            if plan_end and stage['plan_end_date'] and plan_end > stage['plan_end_date']:
                flash(f'Срок подэтапа ({plan_end}) не может быть позже срока этапа ({stage["plan_end_date"]}).', 'danger')
                return render_template('objects/substage_form.html', stage=stage, sub=sub)

            try:
                volume = float(volume) if volume else None
            except ValueError:
                volume = None
            try:
                unit_price = float(unit_price) if unit_price else None
            except ValueError:
                unit_price = None

            total_price = _calc_total(volume, unit_price)

            execute_db(
                'UPDATE substages SET name=?, description=?, volume=?, unit=?, unit_price=?, total_price=?, plan_end_date=? WHERE id=?',
                (name, description, volume, unit, unit_price, total_price, plan_end, sub_id),
            )
            flash('Подэтап обновлён.', 'success')
            return redirect(url_for('stage_detail', stage_id=sub['stage_id']))

        return render_template('objects/substage_form.html', stage=stage, sub=sub)

    @app.route('/substages/<int:sub_id>/delete', methods=['POST'])
    @login_required
    @role_required(*SUBSTAGE_EDITORS)
    def substage_delete(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        execute_db('DELETE FROM substages WHERE id = ?', (sub_id,))
        flash('Подэтап удалён.', 'success')
        return redirect(url_for('stage_detail', stage_id=sub['stage_id']))

    # ═══ Страница подэтапа, статус, фото ═══

    STATUS_LABELS = {
        'not_started': 'Не начат',
        'in_progress': 'В работе',
        'done': 'Выполнен',
        'closed': 'Закрыт',
        'approved': 'Согласован',
    }

    def _can_change_substage_status(stage):
        if current_user.role in SUBSTAGE_EDITORS:
            return True
        if current_user.role == 'foreman':
            return True
        if current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id:
            return True
        return False

    def _can_upload_substage_photo(stage):
        if current_user.role == 'foreman':
            return True
        if current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id:
            return True
        return False

    @app.route('/substages/<int:sub_id>')
    @login_required
    def substage_detail(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        stage = query_db(
            'SELECT cs.*, org.name as contractor_name, o.name as object_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE cs.id = ?', (sub['stage_id'],), one=True)
        if not stage or not _can_view_stage(stage):
            abort(403)
        photos = query_db(
            'SELECT sp.*, u.full_name as uploader_name '
            'FROM substage_photos sp LEFT JOIN users u ON sp.uploaded_by = u.id '
            'WHERE sp.substage_id = ? ORDER BY sp.uploaded_at DESC', (sub_id,))
        photos_list = [dict(p) for p in photos]
        package = query_db(
            "SELECT dp.* FROM doc_packages dp JOIN package_items pi ON pi.package_id = dp.id "
            "WHERE pi.substage_id = ? AND dp.status != 'completed' ORDER BY dp.id DESC LIMIT 1",
            (sub_id,), one=True)
        return render_template('objects/substage_detail.html',
                               sub=sub, stage=stage, photos=photos, photos_json=photos_list,
                               status_labels=STATUS_LABELS,
                               can_change_status=_can_change_substage_status(stage),
                               can_upload_photo=_can_upload_substage_photo(stage),
                               package=package)

    @app.route('/substages/<int:sub_id>/status', methods=['POST'])
    @login_required
    def substage_status(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (sub['stage_id'],), one=True)
        if not _can_change_substage_status(stage):
            abort(403)

        new_status = request.form.get('status', '')
        if new_status not in ('not_started', 'in_progress', 'done'):
            flash('Недопустимый статус.', 'danger')
            return redirect(url_for('substage_detail', sub_id=sub_id))

        from db import get_db
        from datetime import date
        db = get_db()
        today = date.today().isoformat()
        if new_status == 'done':
            db.execute(
                'UPDATE substages SET status = ?, completed_at = CURRENT_TIMESTAMP, '
                'actual_start_date = COALESCE(actual_start_date, ?), '
                'actual_end_date = COALESCE(actual_end_date, ?) WHERE id = ?',
                (new_status, today, today, sub_id))
        elif new_status == 'in_progress':
            db.execute(
                'UPDATE substages SET status = ?, completed_at = NULL, '
                'actual_start_date = COALESCE(actual_start_date, ?), '
                'actual_end_date = NULL WHERE id = ?',
                (new_status, today, sub_id))
        else:  # not_started — полный откат факта
            db.execute(
                'UPDATE substages SET status = ?, completed_at = NULL, '
                'actual_start_date = NULL, actual_end_date = NULL WHERE id = ?',
                (new_status, sub_id))
        db.commit()

        from helpers import recalc_stage_actuals
        recalc_stage_actuals(sub['stage_id'])

        flash(f'Статус изменён: {STATUS_LABELS.get(new_status, new_status)}.', 'success')
        return redirect(url_for('substage_detail', sub_id=sub_id))

    @app.route('/substages/<int:sub_id>/photos/upload', methods=['POST'])
    @login_required
    def substage_photo_upload(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (sub['stage_id'],), one=True)
        if not _can_upload_substage_photo(stage):
            abort(403)

        files = request.files.getlist('photos')
        count = 0
        for file in files:
            filename = save_substage_photo(file, sub_id)
            if filename:
                execute_db(
                    'INSERT INTO substage_photos (substage_id, filename, uploaded_by) VALUES (?, ?, ?)',
                    (sub_id, filename, current_user.id),
                )
                count += 1

        if count:
            flash(f'Загружено фото: {count}.', 'success')
        else:
            flash('Не удалось загрузить фото. Допустимы: jpg, png, gif, webp.', 'danger')
        return redirect(url_for('substage_detail', sub_id=sub_id))

    @app.route('/substages/<int:sub_id>/photos/<int:photo_id>/delete', methods=['POST'])
    @login_required
    def substage_photo_delete(sub_id, photo_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        photo = query_db('SELECT * FROM substage_photos WHERE id = ? AND substage_id = ?',
                         (photo_id, sub_id), one=True)
        if not sub or not photo:
            abort(404)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (sub['stage_id'],), one=True)
        if not _can_upload_substage_photo(stage) and current_user.role not in SUBSTAGE_EDITORS:
            abort(403)

        filepath = os.path.join(config.UPLOAD_FOLDER, 'substages', str(sub_id), photo['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        execute_db('DELETE FROM substage_photos WHERE id = ?', (photo_id,))
        flash('Фото удалено.', 'success')
        return redirect(url_for('substage_detail', sub_id=sub_id))
