from flask import render_template, redirect, url_for, request, flash, abort, jsonify
from flask_login import login_required, current_user

from db import query_db, execute_db, get_db, notify
from helpers import role_required, save_defect_photo, save_defect_audio

DEFECT_VIEWERS = ('manager', 'admin', 'pto', 'inspector', 'foreman')
DEFECT_CREATORS = ('inspector', 'foreman', 'admin')

PRIORITY_LABELS = {
    'low': 'Низкий', 'normal': 'Обычный', 'high': 'Высокий', 'critical': 'Критический',
}
STATUS_LABELS = {
    'open': 'Открыто', 'in_progress': 'В работе', 'resolved': 'Устранено',
    'verified': 'Проверено', 'rejected': 'Отклонено', 'closed': 'Закрыто',
}


def _can_view_defect(defect):
    if current_user.role in DEFECT_VIEWERS:
        return True
    if current_user.role == 'contractor' and defect['contractor_id'] == current_user.organization_id:
        return True
    return False


def _defects_base_query(where_clauses=None, args=None):
    if where_clauses is None:
        where_clauses = []
    if args is None:
        args = []
    where = ' AND '.join(where_clauses) if where_clauses else '1=1'
    return query_db(
        f'SELECT d.*, o.name as object_name, cs.name as stage_name, '
        f'dt.name as type_name, u.full_name as reporter_name, '
        f'org.name as contractor_name '
        f'FROM defects d '
        f'JOIN objects o ON d.object_id = o.id '
        f'LEFT JOIN construction_stages cs ON d.stage_id = cs.id '
        f'LEFT JOIN defect_types dt ON d.type_id = dt.id '
        f'LEFT JOIN users u ON d.reporter_id = u.id '
        f'LEFT JOIN organizations org ON d.contractor_id = org.id '
        f'WHERE {where} '
        f'ORDER BY d.created_at DESC',
        args,
    )


def register(app):

    @app.route('/defects')
    @login_required
    def defects_list():
        if current_user.role not in DEFECT_VIEWERS and current_user.role != 'contractor':
            abort(403)

        where = []
        args = []

        if current_user.role == 'contractor':
            where.append('d.contractor_id = ?')
            args.append(current_user.organization_id)

        f_object = request.args.get('object_id', '')
        f_stage = request.args.get('stage_id', '')
        f_status = request.args.get('status', '')
        f_priority = request.args.get('priority', '')
        f_type = request.args.get('type_id', '')
        f_overdue = request.args.get('overdue', '')

        if f_object:
            where.append('d.object_id = ?')
            args.append(int(f_object))
        if f_stage:
            where.append('d.stage_id = ?')
            args.append(int(f_stage))
        if f_status:
            where.append('d.status = ?')
            args.append(f_status)
        if f_priority:
            where.append('d.priority = ?')
            args.append(f_priority)
        if f_type:
            where.append('d.type_id = ?')
            args.append(int(f_type))
        if f_overdue:
            where.append("d.due_date < date('now') AND d.status NOT IN ('closed', 'verified')")

        defects = _defects_base_query(where, args)

        # Счётчики по статусам (с учётом contractor)
        count_where = []
        count_args = []
        if current_user.role == 'contractor':
            count_where.append('contractor_id = ?')
            count_args.append(current_user.organization_id)
        count_sql = ' AND '.join(count_where) if count_where else '1=1'
        counts = {}
        for st in STATUS_LABELS:
            row = query_db(f'SELECT COUNT(*) as c FROM defects WHERE status = ? AND {count_sql}',
                           [st] + count_args, one=True)
            counts[st] = row['c']

        objects = query_db("SELECT id, name FROM objects WHERE status='active' ORDER BY name")
        types = query_db('SELECT id, name FROM defect_types ORDER BY order_num')

        return render_template('defects/list.html',
                               defects=defects, counts=counts,
                               status_labels=STATUS_LABELS, priority_labels=PRIORITY_LABELS,
                               objects=objects, types=types,
                               f_object=f_object, f_stage=f_stage, f_status=f_status,
                               f_priority=f_priority, f_type=f_type, f_overdue=f_overdue)

    @app.route('/defects/add', methods=['GET', 'POST'])
    @login_required
    @role_required(*DEFECT_CREATORS)
    def defect_add():
        if request.method == 'POST':
            object_id = request.form.get('object_id', '')
            stage_id = request.form.get('stage_id', '')
            substage_id = request.form.get('substage_id', '').strip() or None
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            type_id = request.form.get('type_id', '').strip() or None
            priority = request.form.get('priority', 'normal')
            due_date = request.form.get('due_date', '').strip() or None

            if not title or not object_id or not stage_id:
                flash('Заполните обязательные поля: объект, этап, заголовок.', 'danger')
                return redirect(url_for('defect_add'))

            stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
            if not stage:
                abort(404)
            contractor_id = stage['contractor_id']
            plan_id = request.form.get('plan_id', '').strip() or None

            pin_x = request.form.get('pin_x', '').strip() or None
            pin_y = request.form.get('pin_y', '').strip() or None
            try:
                pin_x = float(pin_x) if pin_x else None
                pin_y = float(pin_y) if pin_y else None
            except (ValueError, TypeError):
                pin_x = pin_y = None
            if plan_id:
                plan_id = int(plan_id)

            db = get_db()
            cur = db.execute(
                'INSERT INTO defects (object_id, stage_id, substage_id, title, description, '
                'type_id, priority, reporter_id, contractor_id, due_date, plan_id, pin_x, pin_y) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (object_id, stage_id, substage_id, title, description,
                 type_id, priority, current_user.id, contractor_id, due_date, plan_id, pin_x, pin_y),
            )
            defect_id = cur.lastrowid

            db.execute(
                'INSERT INTO defect_history (defect_id, user_id, action, new_value) VALUES (?, ?, ?, ?)',
                (defect_id, current_user.id, 'created', title),
            )
            db.commit()

            # Фото «до»
            photos = request.files.getlist('photos')
            for f in photos:
                filename = save_defect_photo(f, defect_id)
                if filename:
                    execute_db(
                        'INSERT INTO defect_photos (defect_id, filename, photo_type, uploaded_by) '
                        'VALUES (?, ?, ?, ?)',
                        (defect_id, filename, 'before', current_user.id),
                    )

            # Уведомить подрядчика
            if contractor_id:
                obj = query_db('SELECT name FROM objects WHERE id = ?', (object_id,), one=True)
                stage_name = stage['name']
                users = query_db('SELECT id FROM users WHERE organization_id = ? AND is_approved = 1',
                                 (contractor_id,))
                for u in users:
                    notify(u['id'], 'defect',
                           f'Новое замечание: {title}',
                           f'Замечание на объекте «{obj["name"]}», этап «{stage_name}».',
                           f'/defects/{defect_id}')

            flash('Замечание создано.', 'success')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        objects = query_db("SELECT id, name FROM objects WHERE status='active' ORDER BY name")
        types = query_db('SELECT id, name FROM defect_types ORDER BY order_num')
        preselect_object = request.args.get('object_id', '')
        preselect_plan = request.args.get('plan_id', '')
        return render_template('defects/form.html', objects=objects, types=types,
                               priority_labels=PRIORITY_LABELS,
                               preselect_object=preselect_object,
                               preselect_plan=preselect_plan)

    @app.route('/defects/<int:defect_id>')
    @login_required
    def defect_detail(defect_id):
        d = query_db(
            'SELECT d.*, o.name as object_name, cs.name as stage_name, '
            'ss.name as substage_name, dt.name as type_name, '
            'u.full_name as reporter_name, org.name as contractor_name '
            'FROM defects d '
            'JOIN objects o ON d.object_id = o.id '
            'LEFT JOIN construction_stages cs ON d.stage_id = cs.id '
            'LEFT JOIN substages ss ON d.substage_id = ss.id '
            'LEFT JOIN defect_types dt ON d.type_id = dt.id '
            'LEFT JOIN users u ON d.reporter_id = u.id '
            'LEFT JOIN organizations org ON d.contractor_id = org.id '
            'WHERE d.id = ?', (defect_id,), one=True)
        if not d or not _can_view_defect(d):
            abort(403)

        photos = query_db(
            'SELECT dp.*, u.full_name as uploader_name FROM defect_photos dp '
            'LEFT JOIN users u ON dp.uploaded_by = u.id '
            'WHERE dp.defect_id = ? ORDER BY dp.photo_type, dp.uploaded_at', (defect_id,))
        photos_before = [dict(p) for p in photos if p['photo_type'] == 'before']
        photos_after = [dict(p) for p in photos if p['photo_type'] == 'after']
        photos_all = [dict(p) for p in photos]

        history = query_db(
            'SELECT dh.*, u.full_name as user_name FROM defect_history dh '
            'LEFT JOIN users u ON dh.user_id = u.id '
            'WHERE dh.defect_id = ? ORDER BY dh.created_at DESC', (defect_id,))

        plan = None
        if d['plan_id']:
            plan = query_db('SELECT * FROM object_plans WHERE id = ?', (d['plan_id'],), one=True)

        audio = query_db(
            'SELECT da.*, u.full_name as uploader_name FROM defect_audio da '
            'LEFT JOIN users u ON da.uploaded_by = u.id '
            'WHERE da.defect_id = ? ORDER BY da.uploaded_at', (defect_id,))

        return render_template('defects/detail.html', d=d,
                               photos_before=photos_before, photos_after=photos_after,
                               photos_all=photos_all, audio=audio,
                               history=history, plan=plan,
                               status_labels=STATUS_LABELS, priority_labels=PRIORITY_LABELS)

    # ═══ Голосовые заметки ═══

    @app.route('/defects/<int:defect_id>/audio/upload', methods=['POST'])
    @login_required
    def defect_audio_upload(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d or not _can_view_defect(d):
            abort(403)
        file = request.files.get('audio')
        if not file or not file.filename:
            flash('Файл не выбран.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))
        filename = save_defect_audio(file, defect_id)
        if not filename:
            flash('Недопустимый формат аудио.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))
        execute_db('INSERT INTO defect_audio (defect_id, filename, uploaded_by) VALUES (?, ?, ?)',
                   (defect_id, filename, current_user.id))
        flash('Голосовая заметка добавлена.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/audio/<int:audio_id>/delete', methods=['POST'])
    @login_required
    def defect_audio_delete(defect_id, audio_id):
        import os
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        a = query_db('SELECT * FROM defect_audio WHERE id = ? AND defect_id = ?', (audio_id, defect_id), one=True)
        if not d or not a:
            abort(404)
        if a['uploaded_by'] != current_user.id and current_user.role not in ('manager', 'admin'):
            abort(403)
        import config as cfg
        filepath = os.path.join(cfg.DEFECTS_FOLDER, str(defect_id), a['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        execute_db('DELETE FROM defect_audio WHERE id = ?', (audio_id,))
        flash('Голосовая заметка удалена.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    # ═══ Действия с замечанием ═══

    def _is_contractor_of(defect):
        return current_user.role == 'contractor' and defect['contractor_id'] == current_user.organization_id

    def _is_reviewer(defect):
        return current_user.role in ('inspector', 'admin') or current_user.id == defect['reporter_id']

    def _notify_contractor(defect, title, body):
        if defect['contractor_id']:
            users = query_db('SELECT id FROM users WHERE organization_id = ? AND is_approved = 1',
                             (defect['contractor_id'],))
            for u in users:
                notify(u['id'], 'defect', title, body, f'/defects/{defect["id"]}')

    def _notify_reporter(defect, title, body):
        if defect['reporter_id']:
            notify(defect['reporter_id'], 'defect', title, body, f'/defects/{defect["id"]}')

    def _write_history(defect_id, action, old_value=None, new_value=None, comment=None):
        db = get_db()
        db.execute(
            'INSERT INTO defect_history (defect_id, user_id, action, old_value, new_value, comment) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (defect_id, current_user.id, action, old_value, new_value, comment))
        db.execute('UPDATE defects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (defect_id,))
        db.commit()

    @app.route('/defects/<int:defect_id>/take', methods=['POST'])
    @login_required
    def defect_take(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d:
            abort(404)
        if not _is_contractor_of(d):
            abort(403)
        if d['status'] not in ('open', 'rejected'):
            flash('Невозможно взять в работу из текущего статуса.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        execute_db("UPDATE defects SET status = 'in_progress' WHERE id = ?", (defect_id,))
        _write_history(defect_id, 'status_change', d['status'], 'in_progress')
        _notify_reporter(d, f'Замечание #{defect_id} взято в работу',
                         f'Подрядчик взял замечание «{d["title"]}» в работу.')
        flash('Замечание взято в работу.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/resolve', methods=['POST'])
    @login_required
    def defect_resolve(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d:
            abort(404)
        if not _is_contractor_of(d):
            abort(403)
        if d['status'] != 'in_progress':
            flash('Можно отметить устранённым только из статуса «В работе».', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        comment = request.form.get('comment', '').strip()
        photos = request.files.getlist('photos_after')
        saved = 0
        for f in photos:
            filename = save_defect_photo(f, defect_id)
            if filename:
                execute_db(
                    'INSERT INTO defect_photos (defect_id, filename, photo_type, uploaded_by) VALUES (?, ?, ?, ?)',
                    (defect_id, filename, 'after', current_user.id))
                saved += 1

        if saved == 0:
            flash('Приложите хотя бы одно фото «после» для подтверждения устранения.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        db = get_db()
        db.execute("UPDATE defects SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (defect_id,))
        db.commit()
        _write_history(defect_id, 'resolved', 'in_progress', 'resolved', comment)
        _notify_reporter(d, f'Замечание #{defect_id} устранено',
                         f'Подрядчик отметил замечание «{d["title"]}» как устранённое.')
        flash('Замечание отмечено устранённым.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/verify', methods=['POST'])
    @login_required
    def defect_verify(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d:
            abort(404)
        if not _is_reviewer(d):
            abort(403)
        if d['status'] != 'resolved':
            flash('Можно принять только из статуса «Устранено».', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        db = get_db()
        db.execute("UPDATE defects SET status = 'closed', verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (defect_id,))
        db.commit()
        _write_history(defect_id, 'closed', 'resolved', 'closed')
        _notify_contractor(d, f'Замечание #{defect_id} закрыто',
                           f'Замечание «{d["title"]}» принято и закрыто.')
        flash('Замечание принято и закрыто.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/reopen', methods=['POST'])
    @login_required
    def defect_reopen(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d:
            abort(404)
        if not _is_reviewer(d):
            abort(403)
        if d['status'] != 'resolved':
            flash('Вернуть можно только из статуса «Устранено».', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Укажите причину возврата.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        db = get_db()
        db.execute(
            "UPDATE defects SET status = 'in_progress', reopen_count = reopen_count + 1, "
            "resolved_at = NULL WHERE id = ?", (defect_id,))
        db.commit()
        _write_history(defect_id, 'reopened', 'resolved', 'in_progress', reason)
        _notify_contractor(d, f'Замечание #{defect_id} возвращено',
                           f'Замечание «{d["title"]}» возвращено: {reason}')
        flash('Замечание возвращено подрядчику.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/reject', methods=['POST'])
    @login_required
    def defect_reject(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d:
            abort(404)
        if not _is_reviewer(d):
            abort(403)
        if d['status'] in ('closed', 'rejected'):
            flash('Замечание уже закрыто или отклонено.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        reason = request.form.get('reason', '').strip()
        if not reason:
            flash('Укажите причину отклонения.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))

        execute_db("UPDATE defects SET status = 'rejected' WHERE id = ?", (defect_id,))
        _write_history(defect_id, 'rejected', d['status'], 'rejected', reason)
        _notify_contractor(d, f'Замечание #{defect_id} отклонено',
                           f'Замечание «{d["title"]}» отклонено: {reason}')
        flash('Замечание отклонено.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    @app.route('/defects/<int:defect_id>/comment', methods=['POST'])
    @login_required
    def defect_comment(defect_id):
        d = query_db('SELECT * FROM defects WHERE id = ?', (defect_id,), one=True)
        if not d or not _can_view_defect(d):
            abort(403)
        comment = request.form.get('comment', '').strip()
        if not comment:
            flash('Введите комментарий.', 'danger')
            return redirect(url_for('defect_detail', defect_id=defect_id))
        _write_history(defect_id, 'comment', comment=comment)

        # Уведомить другую сторону
        if current_user.role == 'contractor':
            _notify_reporter(d, f'Комментарий к замечанию #{defect_id}',
                             f'Подрядчик оставил комментарий: {comment[:100]}')
        else:
            _notify_contractor(d, f'Комментарий к замечанию #{defect_id}',
                               f'{current_user.full_name or current_user.username} оставил комментарий: {comment[:100]}')

        flash('Комментарий добавлен.', 'success')
        return redirect(url_for('defect_detail', defect_id=defect_id))

    # API для каскадных селектов
    @app.route('/api/stages-by-object/<int:object_id>')
    @login_required
    def api_stages_by_object(object_id):
        stages = query_db(
            'SELECT cs.id, cs.name, cs.contractor_id, org.name as contractor_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'WHERE cs.object_id = ? ORDER BY cs.order_num',
            (object_id,))
        return jsonify([dict(s) for s in stages])

    @app.route('/api/plans-by-object/<int:object_id>')
    @login_required
    def api_plans_by_object(object_id):
        plans = query_db(
            'SELECT id, title, level_label, filename, object_id FROM object_plans '
            'WHERE object_id = ? ORDER BY sort_order', (object_id,))
        return jsonify([dict(p) for p in plans])

    @app.route('/api/substages-by-stage/<int:stage_id>')
    @login_required
    def api_substages_by_stage(stage_id):
        subs = query_db('SELECT id, name FROM substages WHERE stage_id = ? ORDER BY id', (stage_id,))
        return jsonify([dict(s) for s in subs])
