import os
import uuid
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, notify
from helpers import role_required

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
