import os
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, notify
from helpers import role_required, save_stage_document, save_substage_photo

VIEWERS = ('manager', 'admin', 'pto', 'inspector', 'foreman')
EDITORS = ('manager', 'admin')
SUBSTAGE_EDITORS = ('pto', 'manager', 'admin')

DOC_TYPE_LABELS = {
    'contract': 'Договор',
    'tech_spec': 'Техническое задание',
    'work_schedule': 'График производства работ',
    'other': 'Прочее',
}


def register(app):

    @app.route('/objects')
    @login_required
    @role_required(*VIEWERS)
    def objects_list():
        status = request.args.get('status', 'active')
        objects = query_db(
            'SELECT o.*, u.full_name as creator_name '
            'FROM objects o LEFT JOIN users u ON o.created_by = u.id '
            'WHERE o.status = ? ORDER BY o.created_at DESC',
            (status,),
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

            if not name:
                flash('Введите название объекта.', 'danger')
                return render_template('objects/form.html', obj=None)

            execute_db(
                'INSERT INTO objects (name, address, type, created_by) VALUES (?, ?, ?, ?)',
                (name, address, obj_type, current_user.id),
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
        stages = query_db(
            'SELECT cs.*, org.name as contractor_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'WHERE cs.object_id = ? ORDER BY cs.order_num',
            (obj_id,),
        )
        return render_template('objects/detail.html', obj=obj, stages=stages)

    @app.route('/objects/<int:obj_id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required(*EDITORS)
    def object_edit(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            address = request.form.get('address', '').strip()
            obj_type = request.form.get('type', '').strip()

            if not name:
                flash('Введите название объекта.', 'danger')
                return render_template('objects/form.html', obj=obj)

            execute_db(
                'UPDATE objects SET name = ?, address = ?, type = ? WHERE id = ?',
                (name, address, obj_type, obj_id),
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
        new_status = 'active' if obj['status'] == 'archived' else 'archived'
        execute_db('UPDATE objects SET status = ? WHERE id = ?', (new_status, obj_id))
        label = 'восстановлен' if new_status == 'active' else 'архивирован'
        flash(f'Объект {label}.', 'success')
        return redirect(url_for('objects_list'))

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

            execute_db(
                'UPDATE construction_stages SET name=?, description=?, order_num=?, '
                'plan_start_date=?, plan_end_date=?, status=? WHERE id=?',
                (name, description, order_num, plan_start, plan_end, status, stage_id),
            )
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
            from db import get_db
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
            s['sub_done'] = sum(1 for sub in subs if sub['status'] == 'done')
        return render_template('objects/my_stages.html', stages=stages_list)

    # ═══ Страница этапа и документы ═══

    def _can_view_stage(stage):
        if current_user.role in VIEWERS:
            return True
        if current_user.role == 'contractor' and stage['contractor_id'] == current_user.organization_id:
            return True
        return False

    def _can_upload_doc(stage):
        if current_user.role in EDITORS:
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
        return render_template('objects/stage_detail.html',
                               stage=stage, docs=docs, doc_type_labels=DOC_TYPE_LABELS,
                               can_upload=_can_upload_doc(stage),
                               substages=substages, total_sum=total_sum)

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
        return send_from_directory(folder, doc['filename'], as_attachment=True,
                                   download_name=doc['title'])

    @app.route('/stages/<int:stage_id>/docs/<int:doc_id>/delete', methods=['POST'])
    @login_required
    def stage_doc_delete(stage_id, doc_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        doc = query_db('SELECT * FROM stage_documents WHERE id = ? AND stage_id = ?',
                       (doc_id, stage_id), one=True)
        if not stage or not doc:
            abort(404)
        if current_user.role not in EDITORS and doc['uploaded_by'] != current_user.id:
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
        return render_template('objects/substage_detail.html',
                               sub=sub, stage=stage, photos=photos, photos_json=photos_list,
                               status_labels=STATUS_LABELS,
                               can_change_status=_can_change_substage_status(stage),
                               can_upload_photo=_can_upload_substage_photo(stage))

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
        db = get_db()
        if new_status == 'done':
            db.execute('UPDATE substages SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?',
                       (new_status, sub_id))
        else:
            db.execute('UPDATE substages SET status = ?, completed_at = NULL WHERE id = ?',
                       (new_status, sub_id))
        db.commit()

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
