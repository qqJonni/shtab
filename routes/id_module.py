import os
import uuid
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import get_db, query_db, execute_db, notify
from helpers import role_required, assert_object_access, get_object_team

# Роли, которые могут набирать/редактировать состав ИД
ID_EDITORS = ('manager', 'pto', 'inspector', 'admin')
# Роли, которые могут загружать файлы к пунктам ИД
ID_UPLOADERS = ('contractor', 'foreman', 'manager', 'pto', 'admin')


def _get_stage_or_403(stage_id):
    stage = query_db(
        'SELECT cs.*, org.name as contractor_name '
        'FROM construction_stages cs '
        'LEFT JOIN organizations org ON cs.contractor_id = org.id '
        'WHERE cs.id = %s', (stage_id,), one=True)
    if not stage:
        abort(404)
    # Доступ: все внутренние роли + подрядчик своего этапа
    if current_user.role not in ('manager', 'admin', 'pto', 'inspector', 'foreman', 'accountant'):
        if not (current_user.role == 'contractor' and
                stage['contractor_id'] == current_user.organization_id):
            abort(403)
    assert_object_access(current_user, stage['object_id'])
    return stage


def _can_edit_id(stage):
    return current_user.role in ID_EDITORS


def _can_upload_id(stage):
    if current_user.role in ('manager', 'pto', 'admin'):
        return True
    if current_user.role in ('contractor', 'foreman'):
        return stage['contractor_id'] == current_user.organization_id
    return False


def register(app):

    # ─── Добавить пункт ИД из справочника или произвольный ───────────────────

    @app.route('/stages/<int:stage_id>/id/add', methods=['POST'])
    @login_required
    def id_item_add(stage_id):
        stage = _get_stage_or_403(stage_id)
        if not _can_edit_id(stage):
            abort(403)

        type_id = request.form.get('type_id', '').strip()
        custom_title = request.form.get('custom_title', '').strip()
        is_required = 1 if request.form.get('is_required') else 0

        if type_id:
            itype = query_db('SELECT * FROM id_item_types WHERE id = %s', (type_id,), one=True)
            if not itype:
                flash('Тип не найден.', 'danger')
                return redirect(url_for('stage_detail', stage_id=stage_id))
            title = itype['name']
            type_id = int(type_id)
        elif custom_title:
            title = custom_title
            type_id = None
        else:
            flash('Выберите тип или введите название пункта ИД.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        # order_num = следующий после последнего
        last = query_db(
            'SELECT MAX(order_num) as m FROM id_checklist_items WHERE stage_id = %s',
            (stage_id,), one=True)
        order_num = (last['m'] or 0) + 1

        execute_db(
            'INSERT INTO id_checklist_items '
            '(stage_id, type_id, title, is_required, order_num, created_by) '
            'VALUES (%s, %s, %s, %s, %s, %s)',
            (stage_id, type_id, title, is_required, order_num, current_user.id),
        )
        flash('Пункт ИД добавлен.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ─── Удалить пункт ИД ─────────────────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/<int:item_id>/delete', methods=['POST'])
    @login_required
    def id_item_delete(stage_id, item_id):
        stage = _get_stage_or_403(stage_id)
        if not _can_edit_id(stage):
            abort(403)

        item = query_db(
            'SELECT * FROM id_checklist_items WHERE id = %s AND stage_id = %s',
            (item_id, stage_id), one=True)
        if not item:
            abort(404)

        # Нельзя удалить если есть файлы — предупредить
        files = query_db('SELECT id FROM id_documents WHERE item_id = %s', (item_id,))
        if files:
            flash('Нельзя удалить пункт с загруженными файлами. Сначала удалите файлы.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

        execute_db('DELETE FROM id_checklist_items WHERE id = %s', (item_id,))
        flash('Пункт ИД удалён.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ─── Переключить обязательность ───────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/<int:item_id>/toggle-required', methods=['POST'])
    @login_required
    def id_item_toggle_required(stage_id, item_id):
        stage = _get_stage_or_403(stage_id)
        if not _can_edit_id(stage):
            abort(403)

        item = query_db(
            'SELECT * FROM id_checklist_items WHERE id = %s AND stage_id = %s',
            (item_id, stage_id), one=True)
        if not item:
            abort(404)

        new_val = 0 if item['is_required'] else 1
        execute_db('UPDATE id_checklist_items SET is_required = %s WHERE id = %s',
                   (new_val, item_id))
        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ─── Перемещение пункта вверх/вниз ────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/<int:item_id>/move/<direction>', methods=['POST'])
    @login_required
    def id_item_move(stage_id, item_id, direction):
        stage = _get_stage_or_403(stage_id)
        if not _can_edit_id(stage):
            abort(403)

        items = query_db(
            'SELECT * FROM id_checklist_items WHERE stage_id = %s ORDER BY order_num, id',
            (stage_id,))
        ids = [i['id'] for i in items]
        if item_id not in ids:
            abort(404)

        idx = ids.index(item_id)
        if direction == 'up' and idx > 0:
            ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
        elif direction == 'down' and idx < len(ids) - 1:
            ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]

        for order, iid in enumerate(ids, 1):
            execute_db('UPDATE id_checklist_items SET order_num = %s WHERE id = %s', (order, iid))

        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ─── Загрузка файла к пункту ИД ───────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/<int:item_id>/upload', methods=['POST'])
    @login_required
    def id_file_upload(stage_id, item_id):
        stage = _get_stage_or_403(stage_id)
        if not _can_upload_id(stage):
            abort(403)

        item = query_db(
            'SELECT * FROM id_checklist_items WHERE id = %s AND stage_id = %s',
            (item_id, stage_id), one=True)
        if not item:
            abort(404)

        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            flash('Файл не выбран.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

        allowed_ext = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'jpeg', 'png', 'dwg', 'dxf', 'zip'}
        folder = os.path.join(config.ID_DOCS_FOLDER, str(stage_id))
        os.makedirs(folder, exist_ok=True)

        count = 0
        for f in files:
            if not f.filename:
                continue
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in allowed_ext:
                flash(f'Недопустимый формат: {f.filename}', 'warning')
                continue
            unique_name = f'{uuid.uuid4().hex}.{ext}'
            f.save(os.path.join(folder, unique_name))
            execute_db(
                'INSERT INTO id_documents (item_id, filename, original_name, uploaded_by) '
                'VALUES (%s, %s, %s, %s)',
                (item_id, unique_name, f.filename, current_user.id),
            )
            count += 1

        if count:
            flash(f'Загружено файлов: {count}.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ─── Скачать файл ─────────────────────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/files/<int:file_id>/download')
    @login_required
    def id_file_download(stage_id, file_id):
        _get_stage_or_403(stage_id)
        doc = query_db('SELECT id_documents.*, id_checklist_items.stage_id as chk_stage_id '
                       'FROM id_documents '
                       'JOIN id_checklist_items ON id_documents.item_id = id_checklist_items.id '
                       'WHERE id_documents.id = %s', (file_id,), one=True)
        if not doc or doc['chk_stage_id'] != stage_id:
            abort(404)
        folder = os.path.join(config.ID_DOCS_FOLDER, str(stage_id))
        return send_from_directory(folder, doc['filename'],
                                   download_name=doc['original_name'] or doc['filename'])

    # ─── Удалить файл ─────────────────────────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/files/<int:file_id>/delete', methods=['POST'])
    @login_required
    def id_file_delete(stage_id, file_id):
        stage = _get_stage_or_403(stage_id)
        doc = query_db('SELECT id_documents.*, id_checklist_items.stage_id as chk_stage_id '
                       'FROM id_documents '
                       'JOIN id_checklist_items ON id_documents.item_id = id_checklist_items.id '
                       'WHERE id_documents.id = %s', (file_id,), one=True)
        if not doc or doc['chk_stage_id'] != stage_id:
            abort(404)
        # Удалять файл может тот, кто загрузил, + редакторы
        if not (_can_edit_id(stage) or doc['uploaded_by'] == current_user.id):
            abort(403)

        filepath = os.path.join(config.ID_DOCS_FOLDER, str(stage_id), doc['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        execute_db('DELETE FROM id_documents WHERE id = %s', (file_id,))
        flash('Файл удалён.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

    # ═══ Приёмка пакета ИД ═══════════════════════════════════════════════════

    def _get_id_package_full(pkg_id):
        return query_db(
            'SELECT ip.*, cs.name as stage_name, cs.contractor_id, cs.object_id, '
            'o.name as object_name, org.name as contractor_name, u.full_name as creator_name '
            'FROM id_packages ip '
            'JOIN construction_stages cs ON ip.stage_id = cs.id '
            'JOIN objects o ON cs.object_id = o.id '
            'LEFT JOIN organizations org ON ip.contractor_id = org.id '
            'LEFT JOIN users u ON ip.created_by = u.id '
            'WHERE ip.id = %s', (pkg_id,), one=True)

    def _can_view_id_pkg(pkg):
        if current_user.role in ('manager', 'admin', 'pto', 'inspector', 'foreman'):
            return True
        if current_user.role == 'contractor' and pkg['contractor_id'] == current_user.organization_id:
            return True
        return False

    def _is_id_pkg_contractor(pkg):
        return (current_user.role in ('contractor', 'foreman') and
                pkg['contractor_id'] == current_user.organization_id)

    def _get_my_id_pending_step(pkg_id):
        chain_roles = [r for r, _ in config.ID_APPROVAL_CHAIN]
        if current_user.role not in chain_roles:
            return None
        return query_db(
            "SELECT * FROM id_approval_steps WHERE package_id = %s AND status = 'pending' "
            "AND (approver_id = %s OR (approver_id IS NULL AND role = %s))",
            (pkg_id, current_user.id, current_user.role), one=True)

    # ─── Страница пакета ИД ──────────────────────────────────────────────────

    @app.route('/id-packages/<int:pkg_id>')
    @login_required
    def id_package_detail(pkg_id):
        pkg = _get_id_package_full(pkg_id)
        if not pkg or not _can_view_id_pkg(pkg):
            abort(403)
        steps = query_db(
            'SELECT s.*, u.full_name as approver_name '
            'FROM id_approval_steps s LEFT JOIN users u ON s.approver_id = u.id '
            'WHERE s.package_id = %s ORDER BY s.step_order', (pkg_id,))
        my_step = _get_my_id_pending_step(pkg_id)
        # ИД-пункты этапа для контекста
        id_items = query_db(
            'SELECT ci.*, '
            '(SELECT COUNT(*) FROM id_documents WHERE item_id = ci.id) as file_count '
            'FROM id_checklist_items ci WHERE ci.stage_id = %s ORDER BY ci.order_num, ci.id',
            (pkg['stage_id'],))
        return render_template('id/package_detail.html',
                               pkg=pkg, steps=steps, my_step=my_step,
                               id_items=id_items,
                               chain=config.ID_APPROVAL_CHAIN,
                               chain_labels=dict(config.ID_APPROVAL_CHAIN),
                               is_contractor=_is_id_pkg_contractor(pkg))

    # ─── Отправить пакет ИД на приёмку ──────────────────────────────────────

    @app.route('/stages/<int:stage_id>/id/submit', methods=['POST'])
    @login_required
    def id_package_submit(stage_id):
        stage = _get_stage_or_403(stage_id)
        if not _is_id_pkg_contractor(stage):
            abort(403)

        # Только один активный пакет на этап
        existing = query_db(
            "SELECT id FROM id_packages WHERE stage_id = %s AND status NOT IN ('accepted')",
            (stage_id,), one=True)
        if existing:
            flash('Пакет ИД уже отправлен на приёмку.', 'warning')
            return redirect(url_for('id_package_detail', pkg_id=existing['id']))

        # Проверка готовности обязательных пунктов
        items = query_db(
            'SELECT ci.is_required, '
            '(SELECT COUNT(*) FROM id_documents WHERE item_id = ci.id) as file_count '
            'FROM id_checklist_items ci WHERE ci.stage_id = %s', (stage_id,))
        req_total = sum(1 for i in items if i['is_required'])
        req_done = sum(1 for i in items if i['is_required'] and i['file_count'] > 0)
        if req_total > 0 and req_done < req_total:
            flash(f'Не все обязательные документы загружены ({req_done}/{req_total}). '
                  'Завершите загрузку или снимите обязательность.', 'warning')
            return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

        if not items:
            flash('Состав ИД пуст — добавьте пункты.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

        # Проверяем команду объекта
        team = get_object_team(stage['object_id'])
        chain_roles = [role for role, _ in config.ID_APPROVAL_CHAIN]
        missing = [dict(config.ID_APPROVAL_CHAIN).get(r, r) for r in chain_roles if r not in team]
        if missing:
            flash(f'Нельзя отправить: в команде объекта не назначены — {", ".join(missing)}. '
                  'Руководитель должен назначить команду на странице объекта.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id) + '#id-checklist')

        db = get_db()
        cur = db.execute(
            'INSERT INTO id_packages (stage_id, contractor_id, created_by, status) '
            'VALUES (%s, %s, %s, %s)',
            (stage_id, stage['contractor_id'], current_user.id, 'in_review'))
        pkg_id = cur.lastrowid
        db.execute(
            'UPDATE id_packages SET submitted_at = CURRENT_TIMESTAMP WHERE id = %s', (pkg_id,))

        for i, (role, _) in enumerate(config.ID_APPROVAL_CHAIN, 1):
            status = 'pending' if i == 1 else 'waiting'
            approver_id = team.get(role, {}).get('id')
            db.execute(
                'INSERT INTO id_approval_steps (package_id, step_order, role, status, approver_id) '
                'VALUES (%s, %s, %s, %s, %s)',
                (pkg_id, i, role, status, approver_id))
        db.commit()

        first_role = config.ID_APPROVAL_CHAIN[0][0]
        first_approver = team.get(first_role, {})
        if first_approver.get('id'):
            notify(first_approver['id'], 'approval',
                   f'Пакет ИД на приёмку: {stage["name"]}',
                   f'Подрядчик «{stage["contractor_name"]}» отправил пакет ИД по этапу «{stage["name"]}».',
                   f'/id-packages/{pkg_id}')
        else:
            users = query_db('SELECT id FROM users WHERE role = %s AND is_approved = 1', (first_role,))
            for u in users:
                notify(u['id'], 'approval',
                       f'Пакет ИД на приёмку: {stage["name"]}',
                       f'Подрядчик «{stage["contractor_name"]}» отправил пакет ИД по этапу «{stage["name"]}».',
                       f'/id-packages/{pkg_id}')

        flash('Пакет ИД отправлен на приёмку.', 'success')
        return redirect(url_for('id_package_detail', pkg_id=pkg_id))

    # ─── Повторная отправка после возврата ──────────────────────────────────

    @app.route('/id-packages/<int:pkg_id>/resubmit', methods=['POST'])
    @login_required
    def id_package_resubmit(pkg_id):
        pkg = _get_id_package_full(pkg_id)
        if not pkg or not _is_id_pkg_contractor(pkg):
            abort(403)
        if pkg['status'] != 'returned':
            flash('Пакет не в статусе возврата.', 'warning')
            return redirect(url_for('id_package_detail', pkg_id=pkg_id))

        return_to = pkg['return_to_role']
        db = get_db()
        db.execute("UPDATE id_packages SET status = 'in_review', return_to_role = NULL WHERE id = %s",
                   (pkg_id,))
        if return_to:
            db.execute(
                "UPDATE id_approval_steps SET status = 'pending', comment = NULL, acted_at = NULL "
                "WHERE package_id = %s AND role = %s AND status = 'returned'",
                (pkg_id, return_to))
        db.commit()

        target_role = return_to or config.ID_APPROVAL_CHAIN[0][0]
        pending_step = query_db(
            "SELECT approver_id FROM id_approval_steps WHERE package_id = %s AND role = %s AND status = 'pending'",
            (pkg_id, target_role), one=True)
        if pending_step and pending_step['approver_id']:
            notify(pending_step['approver_id'], 'approval',
                   f'Пакет ИД повторно отправлен: {pkg["stage_name"]}',
                   f'Подрядчик исправил и повторно отправил пакет ИД по этапу «{pkg["stage_name"]}».',
                   f'/id-packages/{pkg_id}')
        else:
            users = query_db('SELECT id FROM users WHERE role = %s AND is_approved = 1', (target_role,))
            for u in users:
                notify(u['id'], 'approval',
                       f'Пакет ИД повторно отправлен: {pkg["stage_name"]}',
                       f'Подрядчик исправил и повторно отправил пакет ИД по этапу «{pkg["stage_name"]}».',
                       f'/id-packages/{pkg_id}')

        flash('Пакет ИД повторно отправлен.', 'success')
        return redirect(url_for('id_package_detail', pkg_id=pkg_id))

    # ─── Принять шаг ─────────────────────────────────────────────────────────

    @app.route('/id-packages/<int:pkg_id>/approve', methods=['POST'])
    @login_required
    def id_package_approve(pkg_id):
        pkg = query_db('SELECT * FROM id_packages WHERE id = %s', (pkg_id,), one=True)
        if not pkg or pkg['status'] != 'in_review':
            abort(404)
        step = _get_my_id_pending_step(pkg_id)
        if not step:
            abort(403)

        full = _get_id_package_full(pkg_id)
        comment = request.form.get('comment', '').strip() or None
        db = get_db()
        db.execute(
            "UPDATE id_approval_steps SET status = 'approved', approver_id = %s, "
            "comment = %s, acted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (current_user.id, comment, step['id']))

        next_step = query_db(
            'SELECT * FROM id_approval_steps WHERE package_id = %s AND step_order = %s',
            (pkg_id, step['step_order'] + 1), one=True)

        if next_step:
            db.execute("UPDATE id_approval_steps SET status = 'pending' WHERE id = %s", (next_step['id'],))
            db.commit()
            role_label = dict(config.ID_APPROVAL_CHAIN).get(current_user.role, current_user.role)
            if next_step.get('approver_id'):
                notify(next_step['approver_id'], 'approval',
                       f'Ваша очередь: пакет ИД «{full["stage_name"]}»',
                       f'{role_label} принял(а) пакет ИД по этапу «{full["stage_name"]}». Ваша очередь.',
                       f'/id-packages/{pkg_id}')
            else:
                users = query_db('SELECT id FROM users WHERE role = %s AND is_approved = 1', (next_step['role'],))
                for u in users:
                    notify(u['id'], 'approval',
                           f'Ваша очередь: пакет ИД «{full["stage_name"]}»',
                           f'{role_label} принял(а) пакет ИД по этапу «{full["stage_name"]}». Ваша очередь.',
                           f'/id-packages/{pkg_id}')
        else:
            # Последний шаг — пакет принят
            db.execute(
                "UPDATE id_packages SET status = 'accepted', accepted_at = CURRENT_TIMESTAMP WHERE id = %s",
                (pkg_id,))
            db.commit()
            # Уведомить подрядчика
            if full['contractor_id']:
                users = query_db('SELECT id FROM users WHERE organization_id = %s AND is_approved = 1',
                                 (full['contractor_id'],))
                for u in users:
                    notify(u['id'], 'approval',
                           f'Пакет ИД принят: {full["stage_name"]}',
                           f'Пакет исполнительной документации по этапу «{full["stage_name"]}» принят.',
                           f'/id-packages/{pkg_id}')
            # Уведомить руководителей
            managers = query_db("SELECT id FROM users WHERE role = 'manager' AND is_approved = 1")
            for u in managers:
                notify(u['id'], 'approval',
                       f'Пакет ИД принят: {full["stage_name"]}',
                       f'Исполнительная документация по этапу «{full["stage_name"]}» прошла все согласования.',
                       f'/id-packages/{pkg_id}')

        flash('Принято.', 'success')
        return redirect(url_for('id_package_detail', pkg_id=pkg_id))

    # ─── Вернуть подрядчику ──────────────────────────────────────────────────

    @app.route('/id-packages/<int:pkg_id>/return', methods=['POST'])
    @login_required
    def id_package_return(pkg_id):
        pkg = query_db('SELECT * FROM id_packages WHERE id = %s', (pkg_id,), one=True)
        if not pkg or pkg['status'] != 'in_review':
            abort(404)
        step = _get_my_id_pending_step(pkg_id)
        if not step:
            abort(403)

        comment = request.form.get('comment', '').strip()
        if not comment:
            flash('Укажите причину возврата.', 'danger')
            return redirect(url_for('id_package_detail', pkg_id=pkg_id))

        full = _get_id_package_full(pkg_id)
        role_label = dict(config.ID_APPROVAL_CHAIN).get(current_user.role, current_user.role)
        db = get_db()
        db.execute(
            "UPDATE id_approval_steps SET status = 'returned', approver_id = %s, "
            "comment = %s, acted_at = CURRENT_TIMESTAMP WHERE id = %s",
            (current_user.id, comment, step['id']))
        db.execute(
            "UPDATE id_packages SET status = 'returned', return_to_role = %s WHERE id = %s",
            (current_user.role, pkg_id))
        db.commit()

        if full['contractor_id']:
            users = query_db('SELECT id FROM users WHERE organization_id = %s AND is_approved = 1',
                             (full['contractor_id'],))
            for u in users:
                notify(u['id'], 'approval',
                       f'Пакет ИД возвращён: {full["stage_name"]}',
                       f'{role_label} вернул(а) пакет ИД по этапу «{full["stage_name"]}»: {comment}',
                       f'/id-packages/{pkg_id}')

        flash('Пакет возвращён подрядчику.', 'success')
        return redirect(url_for('id_package_detail', pkg_id=pkg_id))
