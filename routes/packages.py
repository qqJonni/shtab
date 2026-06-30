import os
import json
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db, notify
from helpers import role_required, save_package_document

DOC_TYPE_LABELS = {
    'ks2': 'КС-2',
    'ks3': 'КС-3',
    'invoice': 'Счёт-фактура',
    'raw_material_report': 'Отчёт по давальческому',
    'free_form': 'Свободная форма',
}

PACKAGE_STATUS_LABELS = {
    'draft': 'Черновик',
    'in_review': 'На согласовании',
    'returned': 'Возвращён',
    'approved': 'Согласован',
    'completed': 'Завершён',
}


def _get_package_full(package_id):
    return query_db(
        'SELECT dp.*, ss.name as substage_name, ss.stage_id, '
        'cs.name as stage_name, cs.contractor_id, o.name as object_name, o.id as object_id, '
        'org.name as contractor_name, u.full_name as creator_name '
        'FROM doc_packages dp '
        'JOIN substages ss ON dp.substage_id = ss.id '
        'JOIN construction_stages cs ON ss.stage_id = cs.id '
        'JOIN objects o ON cs.object_id = o.id '
        'LEFT JOIN organizations org ON dp.contractor_id = org.id '
        'LEFT JOIN users u ON dp.created_by = u.id '
        'WHERE dp.id = ?', (package_id,), one=True)


def _can_view_package(pkg):
    if current_user.role in ('manager', 'admin', 'pto', 'inspector', 'foreman', 'accountant'):
        return True
    if current_user.role == 'contractor' and pkg['contractor_id'] == current_user.organization_id:
        return True
    return False


def _is_package_contractor(pkg):
    return current_user.role == 'contractor' and pkg['contractor_id'] == current_user.organization_id


def register(app):

    @app.route('/substages/<int:sub_id>/create-package', methods=['POST'])
    @login_required
    def package_create(sub_id):
        sub = query_db('SELECT * FROM substages WHERE id = ?', (sub_id,), one=True)
        if not sub:
            abort(404)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (sub['stage_id'],), one=True)
        if not stage or current_user.role != 'contractor' or stage['contractor_id'] != current_user.organization_id:
            abort(403)
        if sub['status'] != 'done':
            flash('Пакет можно создать только для подэтапа со статусом «Выполнен».', 'danger')
            return redirect(url_for('substage_detail', sub_id=sub_id))

        existing = query_db("SELECT id FROM doc_packages WHERE substage_id = ? AND status NOT IN ('completed')",
                            (sub_id,), one=True)
        if existing:
            flash('Пакет для этого подэтапа уже существует.', 'warning')
            return redirect(url_for('package_detail', package_id=existing['id']))

        db = get_db()
        cur = db.execute(
            'INSERT INTO doc_packages (substage_id, contractor_id, created_by) VALUES (?, ?, ?)',
            (sub_id, stage['contractor_id'], current_user.id))
        package_id = cur.lastrowid
        db.commit()
        flash('Пакет документов создан. Добавьте документы и отправьте на согласование.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    @app.route('/packages/<int:package_id>')
    @login_required
    def package_detail(package_id):
        pkg = _get_package_full(package_id)
        if not pkg or not _can_view_package(pkg):
            abort(403)

        docs = query_db(
            'SELECT * FROM package_documents WHERE package_id = ? ORDER BY created_at', (package_id,))
        steps = query_db(
            'SELECT a.*, u.full_name as approver_name '
            'FROM approval_steps a LEFT JOIN users u ON a.approver_id = u.id '
            'WHERE a.package_id = ? ORDER BY a.step_order', (package_id,))

        my_step = None
        if current_user.role in dict(config.APPROVAL_CHAIN) and pkg['status'] == 'in_review':
            my_step = query_db(
                "SELECT * FROM approval_steps WHERE package_id = ? AND role = ? AND status = 'pending'",
                (package_id, current_user.role), one=True)

        return render_template('packages/detail.html',
                               pkg=pkg, docs=docs, steps=steps,
                               doc_type_labels=DOC_TYPE_LABELS,
                               status_labels=PACKAGE_STATUS_LABELS,
                               is_contractor=_is_package_contractor(pkg),
                               approval_chain=config.APPROVAL_CHAIN,
                               my_step=my_step)

    @app.route('/packages/<int:package_id>/upload', methods=['POST'])
    @login_required
    def package_doc_upload(package_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg:
            abort(404)
        full = _get_package_full(package_id)
        if not _is_package_contractor(full):
            abort(403)
        if pkg['status'] not in ('draft', 'returned'):
            flash('Документы можно добавлять только в черновик или возвращённый пакет.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        file = request.files.get('file')
        doc_type = request.form.get('doc_type', 'free_form')
        title = request.form.get('title', '').strip()

        if not file or not file.filename:
            flash('Выберите файл.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        if doc_type not in DOC_TYPE_LABELS:
            doc_type = 'free_form'

        filename = save_package_document(file, package_id)
        if not filename:
            flash('Недопустимый формат файла.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        if not title:
            title = file.filename

        execute_db(
            'INSERT INTO package_documents (package_id, doc_type, title, filename, is_generated) '
            'VALUES (?, ?, ?, ?, 0)',
            (package_id, doc_type, title, filename))
        flash('Документ добавлен.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    @app.route('/packages/<int:package_id>/docs/<int:doc_id>/delete', methods=['POST'])
    @login_required
    def package_doc_delete(package_id, doc_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg:
            abort(404)
        full = _get_package_full(package_id)
        if not _is_package_contractor(full):
            abort(403)
        if pkg['status'] not in ('draft', 'returned'):
            flash('Нельзя удалять документы из пакета на согласовании.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        doc = query_db('SELECT * FROM package_documents WHERE id = ? AND package_id = ?',
                       (doc_id, package_id), one=True)
        if not doc:
            abort(404)

        if doc['filename']:
            filepath = os.path.join(config.PACKAGES_FOLDER, str(package_id), doc['filename'])
            if os.path.exists(filepath):
                os.remove(filepath)
        execute_db('DELETE FROM package_documents WHERE id = ?', (doc_id,))
        flash('Документ удалён.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    @app.route('/packages/<int:package_id>/docs/<int:doc_id>/download')
    @login_required
    def package_doc_download(package_id, doc_id):
        pkg = _get_package_full(package_id)
        if not pkg or not _can_view_package(pkg):
            abort(403)
        doc = query_db('SELECT * FROM package_documents WHERE id = ? AND package_id = ?',
                       (doc_id, package_id), one=True)
        if not doc or not doc['filename']:
            abort(404)
        folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
        ext = doc['filename'].rsplit('.', 1)[-1] if '.' in doc['filename'] else ''
        dl_name = doc['title']
        if ext and not dl_name.lower().endswith('.' + ext.lower()):
            dl_name = f"{dl_name}.{ext}"
        return send_from_directory(folder, doc['filename'], as_attachment=True,
                                   download_name=dl_name)

    # ═══ Конструктор документов ═══

    @app.route('/packages/<int:package_id>/delete', methods=['POST'])
    @login_required
    def package_delete(package_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg:
            abort(404)
        full = _get_package_full(package_id)
        if not _is_package_contractor(full):
            abort(403)
        if pkg['status'] not in ('draft', 'returned'):
            flash('Можно удалить только черновик или возвращённый пакет.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        substage_id = pkg['substage_id']
        db = get_db()
        # Удалить файлы документов
        docs = query_db('SELECT filename FROM package_documents WHERE package_id = ?', (package_id,))
        import shutil
        pkg_folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
        if os.path.isdir(pkg_folder):
            shutil.rmtree(pkg_folder)
        db.execute('DELETE FROM package_documents WHERE package_id = ?', (package_id,))
        db.execute('DELETE FROM approval_steps WHERE package_id = ?', (package_id,))
        db.execute('DELETE FROM doc_packages WHERE id = ?', (package_id,))
        # Вернуть подэтап в done если был closed
        sub = query_db('SELECT status FROM substages WHERE id = ?', (substage_id,), one=True)
        if sub and sub['status'] == 'closed':
            db.execute("UPDATE substages SET status = 'done' WHERE id = ?", (substage_id,))
        db.commit()
        flash('Пакет документов удалён.', 'success')
        return redirect(url_for('packages_list'))

    SERVICE_ONLINE_LINKS = {
        'ks2': 'https://service-online.su/forms/ks-2/',
        'ks3': 'https://service-online.su/forms/ks-3/',
        'invoice': 'https://service-online.su/forms/schet-faktura/',
        'raw_material_report': '',
    }

    def _get_prefill_data(package_id):
        pkg = _get_package_full(package_id)
        if not pkg:
            return None

        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (pkg['stage_id'],), one=True)
        obj = query_db('SELECT * FROM objects WHERE id = ?', (pkg['object_id'],), one=True)

        investor = query_db("SELECT * FROM organizations WHERE type='developer' ORDER BY id LIMIT 1", one=True)
        contractor_org = query_db('SELECT * FROM organizations WHERE id = ?',
                                  (pkg['contractor_id'],), one=True) if pkg['contractor_id'] else None

        substage = query_db('SELECT * FROM substages WHERE id = ?', (pkg['substage_id'],), one=True)
        substages = query_db('SELECT * FROM substages WHERE stage_id = ? ORDER BY id', (pkg['stage_id'],))

        from db import get_setting
        vat_rate = get_setting('vat_rate', '20')

        # Накопительные суммы для КС-3: сумма КС-2 из ранее завершённых пакетов
        # того же подрядчика на том же объекте
        prev_completed = query_db(
            "SELECT pd.data_json FROM package_documents pd "
            "JOIN doc_packages dp ON pd.package_id = dp.id "
            "JOIN substages ss ON dp.substage_id = ss.id "
            "JOIN construction_stages cs ON ss.stage_id = cs.id "
            "WHERE pd.doc_type = 'ks2' AND pd.is_generated = 1 "
            "AND dp.status = 'completed' AND dp.contractor_id = ? AND cs.object_id = ? "
            "AND dp.id != ?",
            (pkg['contractor_id'], pkg['object_id'], package_id))

        cumulative_total = 0
        for row in prev_completed:
            if row['data_json']:
                try:
                    d = json.loads(row['data_json'])
                    for item in d.get('items', []):
                        q = float(item.get('quantity', 0) or 0)
                        p = float(item.get('price', 0) or 0)
                        cumulative_total += q * p
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

        return {
            'pkg': pkg,
            'stage': dict(stage) if stage else {},
            'obj': dict(obj) if obj else {},
            'investor': dict(investor) if investor else {},
            'contractor': dict(contractor_org) if contractor_org else {},
            'substage': dict(substage) if substage else {},
            'substages': [dict(s) for s in substages],
            'cumulative_total': cumulative_total,
            'vat_rate': vat_rate,
        }

    @app.route('/packages/<int:package_id>/create-doc/<doc_type>', methods=['GET', 'POST'])
    @login_required
    def package_create_doc(package_id, doc_type):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg:
            abort(404)
        full = _get_package_full(package_id)
        if not _is_package_contractor(full):
            abort(403)
        if pkg['status'] not in ('draft', 'returned'):
            flash('Документы можно создавать только в черновике или возвращённом пакете.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))
        if doc_type not in ('ks2', 'ks3', 'invoice', 'raw_material_report'):
            abort(404)

        prefill = _get_prefill_data(package_id)

        if request.method == 'POST':
            data_json = request.form.get('data_json', '{}')
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                flash('Ошибка данных формы.', 'danger')
                return redirect(url_for('package_create_doc', package_id=package_id, doc_type=doc_type))

            title = data.get('title', DOC_TYPE_LABELS.get(doc_type, doc_type))
            output_fmt = request.form.get('output_format', 'xlsx')

            from doc_generator import generate_document
            filename = generate_document(doc_type, data, package_id, fmt=output_fmt)

            db = get_db()
            db.execute(
                'INSERT INTO package_documents (package_id, doc_type, title, filename, is_generated, data_json) '
                'VALUES (?, ?, ?, ?, 1, ?)',
                (package_id, doc_type, title, filename, data_json))
            db.commit()

            flash(f'Документ «{title}» сформирован.', 'success')
            return redirect(url_for('package_detail', package_id=package_id))

        # Если редактирование существующего — подгрузить data_json
        edit_doc_id = request.args.get('edit')
        existing_data = None
        if edit_doc_id:
            doc = query_db('SELECT * FROM package_documents WHERE id = ? AND package_id = ? AND is_generated = 1',
                           (edit_doc_id, package_id), one=True)
            if doc and doc['data_json']:
                existing_data = json.loads(doc['data_json'])

        return render_template(f'packages/forms/{doc_type}.html',
                               pkg=full, prefill=prefill,
                               doc_type=doc_type,
                               doc_type_label=DOC_TYPE_LABELS.get(doc_type),
                               existing_data=existing_data,
                               edit_doc_id=edit_doc_id,
                               service_link=SERVICE_ONLINE_LINKS.get(doc_type, ''))

    @app.route('/packages/<int:package_id>/submit', methods=['POST'])
    @login_required
    def package_submit(package_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg:
            abort(404)
        full = _get_package_full(package_id)
        if not _is_package_contractor(full):
            abort(403)

        if pkg['status'] == 'returned':
            return _resubmit(pkg, full)

        if pkg['status'] != 'draft':
            flash('Пакет уже отправлен.', 'warning')
            return redirect(url_for('package_detail', package_id=package_id))

        docs = query_db('SELECT id FROM package_documents WHERE package_id = ?', (package_id,))
        if not docs:
            flash('Добавьте хотя бы один документ перед отправкой.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        db = get_db()
        db.execute(
            "UPDATE doc_packages SET status = 'in_review', submitted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (package_id,))
        db.execute(
            "UPDATE substages SET status = 'closed' WHERE id = ?",
            (pkg['substage_id'],))

        for i, (role, _) in enumerate(config.APPROVAL_CHAIN, 1):
            status = 'pending' if i == 1 else 'waiting'
            db.execute(
                'INSERT INTO approval_steps (package_id, step_order, role, status) VALUES (?, ?, ?, ?)',
                (package_id, i, role, status))
        db.commit()

        first_role = config.APPROVAL_CHAIN[0][0]
        users = query_db("SELECT id FROM users WHERE role = ? AND is_approved = 1", (first_role,))
        for u in users:
            notify(u['id'], 'approval',
                   f'Пакет на согласование: {full["substage_name"]}',
                   f'Подрядчик «{full["contractor_name"]}» отправил пакет по подэтапу «{full["substage_name"]}» '
                   f'(объект «{full["object_name"]}», этап «{full["stage_name"]}»).',
                   f'/packages/{package_id}')

        flash('Пакет отправлен на согласование.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    def _resubmit(pkg, full):
        package_id = pkg['id']
        return_to = pkg['return_to_role']

        db = get_db()
        db.execute("UPDATE doc_packages SET status = 'in_review', return_to_role = NULL WHERE id = ?",
                   (package_id,))

        if return_to:
            db.execute(
                "UPDATE approval_steps SET status = 'pending', comment = NULL, acted_at = NULL "
                "WHERE package_id = ? AND role = ? AND status = 'returned'",
                (package_id, return_to))
        db.commit()

        target_role = return_to or config.APPROVAL_CHAIN[0][0]
        users = query_db("SELECT id FROM users WHERE role = ? AND is_approved = 1", (target_role,))
        for u in users:
            notify(u['id'], 'approval',
                   f'Пакет повторно отправлен: {full["substage_name"]}',
                   f'Подрядчик исправил и повторно отправил пакет по подэтапу «{full["substage_name"]}».',
                   f'/packages/{package_id}')

        flash('Пакет повторно отправлен на согласование.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    # ═══ Список пакетов / согласования ═══

    @app.route('/packages')
    @login_required
    def packages_list():
        tab = request.args.get('tab', 'active')

        if current_user.role == 'contractor':
            if tab == 'archive':
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    "WHERE dp.contractor_id = ? AND dp.status = 'completed' ORDER BY dp.completed_at DESC",
                    (current_user.organization_id,))
            else:
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    "WHERE dp.contractor_id = ? AND dp.status != 'completed' ORDER BY dp.created_at DESC",
                    (current_user.organization_id,))
        elif current_user.role in ('manager', 'admin'):
            if tab == 'archive':
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON dp.contractor_id = org.id '
                    "WHERE dp.status = 'completed' ORDER BY dp.completed_at DESC")
            else:
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON dp.contractor_id = org.id '
                    "WHERE dp.status != 'completed' ORDER BY dp.created_at DESC")
        elif current_user.role in dict(config.APPROVAL_CHAIN):
            # Согласующие роли: только свои pending + архив
            if tab == 'archive':
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON dp.contractor_id = org.id '
                    "WHERE dp.status = 'completed' ORDER BY dp.completed_at DESC")
            else:
                pkgs = query_db(
                    'SELECT dp.*, ss.name as substage_name, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM doc_packages dp '
                    'JOIN substages ss ON dp.substage_id = ss.id '
                    'JOIN construction_stages cs ON ss.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON dp.contractor_id = org.id '
                    'JOIN approval_steps a ON a.package_id = dp.id '
                    "WHERE a.role = ? AND a.status = 'pending' AND dp.status = 'in_review' "
                    'ORDER BY dp.created_at DESC',
                    (current_user.role,))
        else:
            abort(403)

        my_pending = []
        if current_user.role in dict(config.APPROVAL_CHAIN):
            my_pending = query_db(
                'SELECT a.package_id FROM approval_steps a '
                'WHERE a.role = ? AND a.status = \'pending\'',
                (current_user.role,))
            my_pending = [r['package_id'] for r in my_pending]

        return render_template('packages/list.html', pkgs=pkgs,
                               status_labels=PACKAGE_STATUS_LABELS,
                               my_pending=my_pending, tab=tab)

    # ═══ Согласование / возврат ═══

    def _get_my_pending_step(package_id):
        if current_user.role not in dict(config.APPROVAL_CHAIN):
            return None
        return query_db(
            "SELECT * FROM approval_steps WHERE package_id = ? AND role = ? AND status = 'pending'",
            (package_id, current_user.role), one=True)

    @app.route('/packages/<int:package_id>/approve', methods=['POST'])
    @login_required
    def package_approve(package_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg or pkg['status'] != 'in_review':
            abort(404)

        step = _get_my_pending_step(package_id)
        if not step:
            abort(403)

        comment = request.form.get('comment', '').strip() or None
        full = _get_package_full(package_id)

        db = get_db()
        db.execute(
            "UPDATE approval_steps SET status = 'approved', approver_id = ?, comment = ?, "
            "acted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (current_user.id, comment, step['id']))

        next_step = query_db(
            "SELECT * FROM approval_steps WHERE package_id = ? AND step_order = ?",
            (package_id, step['step_order'] + 1), one=True)

        if next_step:
            db.execute("UPDATE approval_steps SET status = 'pending' WHERE id = ?", (next_step['id'],))
            db.commit()

            next_role = next_step['role']
            role_label = dict(config.APPROVAL_CHAIN).get(current_user.role, current_user.role)
            users = query_db("SELECT id FROM users WHERE role = ? AND is_approved = 1", (next_role,))
            for u in users:
                notify(u['id'], 'approval',
                       f'Ваша очередь: пакет «{full["substage_name"]}»',
                       f'{role_label} согласовал(а) пакет по подэтапу «{full["substage_name"]}». Ваша очередь.',
                       f'/packages/{package_id}')
        else:
            db.execute(
                "UPDATE doc_packages SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (package_id,))
            db.execute("UPDATE substages SET status = 'approved' WHERE id = ?", (pkg['substage_id'],))
            db.commit()

            contractor_users = query_db(
                'SELECT id FROM users WHERE organization_id = ? AND is_approved = 1',
                (full['contractor_id'],))
            for u in contractor_users:
                notify(u['id'], 'approval',
                       f'Пакет согласован: {full["substage_name"]}',
                       f'Пакет по подэтапу «{full["substage_name"]}» полностью согласован и закрыт.',
                       f'/packages/{package_id}')

            managers = query_db("SELECT id FROM users WHERE role = 'manager' AND is_approved = 1")
            for u in managers:
                notify(u['id'], 'approval',
                       f'Пакет завершён: {full["substage_name"]}',
                       f'Пакет по подэтапу «{full["substage_name"]}» прошёл все согласования.',
                       f'/packages/{package_id}')

        flash('Согласовано.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))

    @app.route('/packages/<int:package_id>/return', methods=['POST'])
    @login_required
    def package_return(package_id):
        pkg = query_db('SELECT * FROM doc_packages WHERE id = ?', (package_id,), one=True)
        if not pkg or pkg['status'] != 'in_review':
            abort(404)

        step = _get_my_pending_step(package_id)
        if not step:
            abort(403)

        comment = request.form.get('comment', '').strip()
        if not comment:
            flash('Укажите причину возврата.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        full = _get_package_full(package_id)
        role_label = dict(config.APPROVAL_CHAIN).get(current_user.role, current_user.role)

        db = get_db()
        db.execute(
            "UPDATE approval_steps SET status = 'returned', approver_id = ?, comment = ?, "
            "acted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (current_user.id, comment, step['id']))
        db.execute(
            "UPDATE doc_packages SET status = 'returned', return_to_role = ? WHERE id = ?",
            (current_user.role, package_id))
        db.commit()

        contractor_users = query_db(
            'SELECT id FROM users WHERE organization_id = ? AND is_approved = 1',
            (full['contractor_id'],))
        for u in contractor_users:
            notify(u['id'], 'approval',
                   f'Пакет возвращён: {full["substage_name"]}',
                   f'{role_label} вернул(а) пакет по подэтапу «{full["substage_name"]}»: {comment}',
                   f'/packages/{package_id}')

        flash('Пакет возвращён подрядчику.', 'success')
        return redirect(url_for('package_detail', package_id=package_id))
