import os
import json
from flask import render_template, redirect, url_for, request, flash, abort, send_from_directory
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db, notify
from helpers import role_required, assert_object_access, get_object_team, \
    get_chain_for_object, CHAIN_ROLES, save_package_document

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
    row = query_db(
        'SELECT dp.*, cs.name as stage_name, cs.contractor_id as stage_contractor_id, '
        'o.name as object_name, o.id as object_id, '
        'org.name as contractor_name, u.full_name as creator_name '
        'FROM doc_packages dp '
        'JOIN construction_stages cs ON dp.stage_id = cs.id '
        'JOIN objects o ON cs.object_id = o.id '
        'LEFT JOIN organizations org ON dp.contractor_id = org.id '
        'LEFT JOIN users u ON dp.created_by = u.id '
        'WHERE dp.id = ?', (package_id,), one=True)
    if not row:
        return None
    pkg = dict(row)
    items = _get_package_items(package_id)
    pkg['items'] = items
    pkg['items_total'] = sum(float(i['amount'] or 0) for i in items)
    # substage_name — метка пакета для списков/уведомлений (обратная совместимость шаблонов)
    if items:
        label = items[0]['substage_name']
        if len(items) > 1:
            label += f' (+{len(items) - 1})'
    else:
        label = pkg['stage_name']
    pkg['substage_name'] = label
    return pkg


def _get_package_items(package_id):
    return [dict(r) for r in query_db(
        'SELECT pi.*, ss.name as substage_name, ss.unit, ss.volume as substage_volume, '
        'ss.status as substage_status '
        'FROM package_items pi JOIN substages ss ON pi.substage_id = ss.id '
        'WHERE pi.package_id = ? ORDER BY pi.id', (package_id,))]


def _stage_substage_volumes(stage_id, exclude_package_id=None):
    """Для каждого подэтапа этапа: договорной объём, закрыто (completed-пакеты),
    зарезервировано (пакеты в работе: draft/in_review/returned) и остаток.
    qty IS NULL в строке пакета означает «полное закрытие подэтапа»."""
    subs = [dict(r) for r in query_db(
        'SELECT * FROM substages WHERE stage_id = ? ORDER BY id', (stage_id,))]
    ex = exclude_package_id or 0
    closed_rows = query_db(
        "SELECT pi.substage_id, "
        "SUM(CASE WHEN pi.qty IS NULL THEN NULL ELSE pi.qty END) as qty_sum, "
        "COUNT(*) FILTER (WHERE pi.qty IS NULL) as full_cnt "
        "FROM package_items pi JOIN doc_packages dp ON pi.package_id = dp.id "
        "WHERE dp.status = 'completed' AND dp.stage_id = ? AND dp.id != ? "
        "GROUP BY pi.substage_id", (stage_id, ex))
    reserved_rows = query_db(
        "SELECT pi.substage_id, "
        "SUM(CASE WHEN pi.qty IS NULL THEN NULL ELSE pi.qty END) as qty_sum, "
        "COUNT(*) FILTER (WHERE pi.qty IS NULL) as full_cnt "
        "FROM package_items pi JOIN doc_packages dp ON pi.package_id = dp.id "
        "WHERE dp.status IN ('draft', 'in_review', 'returned') AND dp.stage_id = ? AND dp.id != ? "
        "GROUP BY pi.substage_id", (stage_id, ex))
    closed = {r['substage_id']: r for r in closed_rows}
    reserved = {r['substage_id']: r for r in reserved_rows}

    result = []
    for s in subs:
        volume = float(s['volume']) if s['volume'] is not None else None
        c = closed.get(s['id'])
        r = reserved.get(s['id'])
        closed_qty = float(c['qty_sum']) if c and c['qty_sum'] is not None else 0.0
        reserved_qty = float(r['qty_sum']) if r and r['qty_sum'] is not None else 0.0
        fully_closed = bool(c and c['full_cnt'])
        fully_reserved = bool(r and r['full_cnt'])
        if volume is not None:
            fully_closed = fully_closed or closed_qty >= volume - 1e-9
            remaining = max(volume - closed_qty - reserved_qty, 0.0)
            if fully_closed or fully_reserved:
                remaining = 0.0
        else:
            # без сметного объёма — только полное закрытие
            remaining = 0.0 if (fully_closed or fully_reserved) else None
        s.update(closed_qty=closed_qty, reserved_qty=reserved_qty,
                 fully_closed=fully_closed, fully_reserved=fully_reserved,
                 remaining=remaining)
        result.append(s)
    return result


def _can_view_package(pkg):
    if current_user.role in ('manager', 'admin', 'pto', 'inspector', 'foreman', 'accountant'):
        return True
    if current_user.role == 'contractor' and pkg['contractor_id'] == current_user.organization_id:
        return True
    return False


def _is_package_contractor(pkg):
    return current_user.role == 'contractor' and pkg['contractor_id'] == current_user.organization_id


def register(app):

    @app.route('/stages/<int:stage_id>/create-package', methods=['GET', 'POST'])
    @login_required
    def package_create(stage_id):
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?', (stage_id,), one=True)
        if not stage:
            abort(404)
        if current_user.role != 'contractor' or stage['contractor_id'] != current_user.organization_id:
            abort(403)

        volumes = _stage_substage_volumes(stage_id)
        # доступны для включения: есть остаток либо (без объёма) не закрыт
        available = [s for s in volumes
                     if (s['remaining'] is None and not s['fully_closed'] and not s['fully_reserved'])
                     or (s['remaining'] is not None and s['remaining'] > 1e-9)]

        if request.method == 'POST':
            items = []   # (substage_id, qty|None, unit_price, amount)
            errors = []
            for s in available:
                sid = s['id']
                if s['volume'] is None:
                    # без сметного объёма — чекбокс полного закрытия
                    if request.form.get(f'full_{sid}'):
                        items.append((sid, None, s['unit_price'], s['total_price']))
                    continue
                raw = request.form.get(f'qty_{sid}', '').strip().replace(',', '.').replace(' ', '')
                if not raw:
                    continue
                try:
                    qty = float(raw)
                except ValueError:
                    errors.append(f'«{s["name"]}»: некорректное число')
                    continue
                if qty <= 0:
                    continue
                if qty > s['remaining'] + 1e-9:
                    errors.append(f'«{s["name"]}»: заявлено {qty:g}, доступно {s["remaining"]:g} {s["unit"] or ""}')
                    continue
                price = float(s['unit_price']) if s['unit_price'] is not None else 0.0
                items.append((sid, qty, price, round(qty * price, 2)))

            if errors:
                for e in errors:
                    flash(e, 'danger')
                return render_template('packages/create.html', stage=stage, volumes=volumes)
            if not items:
                flash('Укажите объём хотя бы по одному подэтапу.', 'danger')
                return render_template('packages/create.html', stage=stage, volumes=volumes)

            db = get_db()
            cur = db.execute(
                'INSERT INTO doc_packages (stage_id, contractor_id, created_by) VALUES (?, ?, ?)',
                (stage_id, stage['contractor_id'], current_user.id))
            package_id = cur.lastrowid
            for sid, qty, price, amount in items:
                db.execute(
                    'INSERT INTO package_items (package_id, substage_id, qty, unit_price, amount) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (package_id, sid, qty, price, amount))
            db.commit()
            flash('Пакет документов создан. Добавьте документы и отправьте на согласование.', 'success')
            return redirect(url_for('package_detail', package_id=package_id))

        return render_template('packages/create.html', stage=stage, volumes=volumes)

    @app.route('/packages/<int:package_id>')
    @login_required
    def package_detail(package_id):
        pkg = _get_package_full(package_id)
        if not pkg or not _can_view_package(pkg):
            abort(403)
        assert_object_access(current_user, pkg['object_id'])

        docs = query_db(
            'SELECT * FROM package_documents WHERE package_id = ? ORDER BY created_at', (package_id,))
        steps = query_db(
            'SELECT a.*, u.full_name as approver_name '
            'FROM approval_steps a LEFT JOIN users u ON a.approver_id = u.id '
            'WHERE a.package_id = ? ORDER BY a.step_order', (package_id,))

        my_step = None
        if current_user.role in CHAIN_ROLES and pkg['status'] == 'in_review':
            my_step = query_db(
                "SELECT * FROM approval_steps WHERE package_id = ? AND role = ? AND status = 'pending'",
                (package_id, current_user.role), one=True)

        return render_template('packages/detail.html',
                               pkg=pkg, docs=docs, steps=steps,
                               doc_type_labels=DOC_TYPE_LABELS,
                               status_labels=PACKAGE_STATUS_LABELS,
                               is_contractor=_is_package_contractor(pkg),
                               approval_chain=get_chain_for_object(pkg['object_id'], 'ks'),
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

        item_sub_ids = [it['substage_id'] for it in _get_package_items(package_id)]
        db = get_db()
        # Удалить файлы документов
        import shutil
        pkg_folder = os.path.join(config.PACKAGES_FOLDER, str(package_id))
        if os.path.isdir(pkg_folder):
            shutil.rmtree(pkg_folder)
        db.execute('DELETE FROM package_documents WHERE package_id = ?', (package_id,))
        db.execute('DELETE FROM approval_steps WHERE package_id = ?', (package_id,))
        db.execute('DELETE FROM package_items WHERE package_id = ?', (package_id,))
        db.execute('DELETE FROM doc_packages WHERE id = ?', (package_id,))
        # Вернуть подэтапы пакета из «closed» в «done»
        for sid in item_sub_ids:
            db.execute("UPDATE substages SET status = 'done' WHERE id = ? AND status = 'closed'", (sid,))
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

        # Позиции пакета — строки будущего КС-2 (объёмы и цены зафиксированы при подаче)
        items = _get_package_items(package_id)
        substages = [
            {**dict(i), 'name': i['substage_name'],
             'volume': i['qty'] if i['qty'] is not None else i['substage_volume'],
             'unit_price': i['unit_price'], 'total_price': i['amount']}
            for i in items
        ]
        substage = substages[0] if substages else {}

        from db import get_setting
        vat_rate = get_setting('vat_rate', '20')

        # Накопительные суммы для КС-3: сумма КС-2 из ранее завершённых пакетов
        # того же подрядчика на том же объекте
        prev_completed = query_db(
            "SELECT pd.data_json FROM package_documents pd "
            "JOIN doc_packages dp ON pd.package_id = dp.id "
            "JOIN construction_stages cs ON dp.stage_id = cs.id "
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

        # Проверяем команду объекта — все роли цепочки должны быть назначены
        team = get_object_team(full['object_id'])
        chain = get_chain_for_object(full['object_id'], 'ks')
        chain_roles = [role for role, _ in chain]
        missing = [dict(chain).get(r, r) for r in chain_roles if r not in team]
        if missing:
            flash(f'Нельзя отправить: в команде объекта не назначены — {", ".join(missing)}. '
                  'Руководитель должен назначить команду на странице объекта.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        # Валидация объёмов: заявленное не должно превышать остаток
        # (остаток считается без учёта строк самого пакета)
        volumes = {s['id']: s for s in _stage_substage_volumes(pkg['stage_id'], exclude_package_id=package_id)}
        items = _get_package_items(package_id)
        vol_errors = []
        for it in items:
            s = volumes.get(it['substage_id'])
            if not s:
                continue
            if it['qty'] is None:
                if s['fully_closed'] or s['fully_reserved']:
                    vol_errors.append(f'«{s["name"]}» уже закрыт или закрывается другим пакетом')
            elif s['remaining'] is not None and float(it['qty']) > s['remaining'] + 1e-9:
                vol_errors.append(f'«{s["name"]}»: заявлено {float(it["qty"]):g}, доступно {s["remaining"]:g}')
        if vol_errors:
            for e in vol_errors:
                flash(f'Нельзя отправить: {e}.', 'danger')
            return redirect(url_for('package_detail', package_id=package_id))

        db = get_db()
        db.execute(
            "UPDATE doc_packages SET status = 'in_review', submitted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (package_id,))
        # Подэтапы, закрываемые полностью этим пакетом и уже выполненные, — в статус «closed»
        for it in items:
            s = volumes.get(it['substage_id'])
            if not s or s['status'] != 'done':
                continue
            covers_all = it['qty'] is None or (
                s['volume'] is not None and s['closed_qty'] + float(it['qty']) >= float(s['volume']) - 1e-9)
            if covers_all:
                db.execute("UPDATE substages SET status = 'closed' WHERE id = ?", (it['substage_id'],))

        for i, (role, _) in enumerate(chain, 1):
            status = 'pending' if i == 1 else 'waiting'
            approver_id = team.get(role, {}).get('id')
            db.execute(
                'INSERT INTO approval_steps (package_id, step_order, role, status, approver_id) VALUES (?, ?, ?, ?, ?)',
                (package_id, i, role, status, approver_id))
        db.commit()

        first_role = chain[0][0]
        first_approver = team.get(first_role, {})
        if first_approver.get('id'):
            notify(first_approver['id'], 'approval',
                   f'Пакет на согласование: {full["substage_name"]}',
                   f'Подрядчик «{full["contractor_name"]}» отправил пакет по подэтапу «{full["substage_name"]}» '
                   f'(объект «{full["object_name"]}», этап «{full["stage_name"]}»).',
                   f'/packages/{package_id}')
        else:
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

        target_role = return_to or get_chain_for_object(full['object_id'], 'ks')[0][0]
        # Уведомить конкретного согласующего (по approver_id шага), иначе всю роль
        pending_step = query_db(
            "SELECT approver_id FROM approval_steps WHERE package_id = ? AND role = ? AND status = 'pending'",
            (package_id, target_role), one=True)
        target_user_ids = []
        if pending_step and pending_step['approver_id']:
            target_user_ids = [pending_step['approver_id']]
        else:
            target_user_ids = [u['id'] for u in query_db(
                "SELECT id FROM users WHERE role = ? AND is_approved = 1", (target_role,))]
        for uid in target_user_ids:
            notify(uid, 'approval',
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

        base = (
            'SELECT dp.*, cs.name as stage_name, o.name as object_name, '
            'org.name as contractor_name, '
            '(SELECT ss.name FROM package_items pi JOIN substages ss ON pi.substage_id = ss.id '
            ' WHERE pi.package_id = dp.id ORDER BY pi.id LIMIT 1) as first_item_name, '
            '(SELECT COUNT(*) FROM package_items pi WHERE pi.package_id = dp.id) as items_count, '
            '(SELECT COALESCE(SUM(pi.amount), 0) FROM package_items pi WHERE pi.package_id = dp.id) as items_total '
            'FROM doc_packages dp '
            'JOIN construction_stages cs ON dp.stage_id = cs.id '
            'JOIN objects o ON cs.object_id = o.id '
            'LEFT JOIN organizations org ON dp.contractor_id = org.id ')

        if current_user.role == 'contractor':
            if tab == 'archive':
                pkgs = query_db(base + "WHERE dp.contractor_id = ? AND dp.status = 'completed' "
                                "ORDER BY dp.completed_at DESC", (current_user.organization_id,))
            else:
                pkgs = query_db(base + "WHERE dp.contractor_id = ? AND dp.status != 'completed' "
                                "ORDER BY dp.created_at DESC", (current_user.organization_id,))
        elif current_user.role in ('manager', 'admin'):
            if tab == 'archive':
                pkgs = query_db(base + "WHERE dp.status = 'completed' ORDER BY dp.completed_at DESC")
            else:
                pkgs = query_db(base + "WHERE dp.status != 'completed' ORDER BY dp.created_at DESC")
        elif current_user.role in CHAIN_ROLES:
            # Согласующие роли: только свои pending (по approver_id или роли) + архив
            if tab == 'archive':
                pkgs = query_db(base + "WHERE dp.status = 'completed' ORDER BY dp.completed_at DESC")
            else:
                pkgs = query_db(
                    base + 'JOIN approval_steps a ON a.package_id = dp.id '
                    "WHERE (a.approver_id = ? OR (a.approver_id IS NULL AND a.role = ?)) "
                    "AND a.status = 'pending' AND dp.status = 'in_review' "
                    'ORDER BY dp.created_at DESC',
                    (current_user.id, current_user.role))
        else:
            abort(403)

        # Метка пакета: первый подэтап (+N)
        out = []
        for p in pkgs:
            p = dict(p)
            label = p['first_item_name'] or p['stage_name']
            if (p['items_count'] or 0) > 1:
                label += f' (+{p["items_count"] - 1})'
            p['substage_name'] = label
            out.append(p)
        pkgs = out

        my_pending = []
        if current_user.role in CHAIN_ROLES:
            my_pending = query_db(
                "SELECT a.package_id FROM approval_steps a "
                "WHERE (a.approver_id = ? OR (a.approver_id IS NULL AND a.role = ?)) AND a.status = 'pending'",
                (current_user.id, current_user.role))
            my_pending = [r['package_id'] for r in my_pending]

        # ═══ Пакеты ИД (исполнительная документация) ═══
        id_base = (
            'SELECT ip.*, cs.name as stage_name, o.name as object_name, '
            'org.name as contractor_name '
            'FROM id_packages ip '
            'JOIN construction_stages cs ON ip.stage_id = cs.id '
            'JOIN objects o ON cs.object_id = o.id '
            'LEFT JOIN organizations org ON ip.contractor_id = org.id ')
        id_chain_roles = dict(config.ID_APPROVAL_CHAIN)
        id_pkgs = []
        if current_user.role == 'contractor':
            if tab == 'archive':
                id_pkgs = query_db(id_base + "WHERE ip.contractor_id = ? AND ip.status = 'accepted' "
                                   "ORDER BY ip.id DESC", (current_user.organization_id,))
            else:
                id_pkgs = query_db(id_base + "WHERE ip.contractor_id = ? AND ip.status != 'accepted' "
                                   "ORDER BY ip.id DESC", (current_user.organization_id,))
        elif current_user.role in ('manager', 'admin'):
            if tab == 'archive':
                id_pkgs = query_db(id_base + "WHERE ip.status = 'accepted' ORDER BY ip.id DESC")
            else:
                id_pkgs = query_db(id_base + "WHERE ip.status != 'accepted' ORDER BY ip.id DESC")
        elif current_user.role in id_chain_roles:
            if tab == 'archive':
                id_pkgs = query_db(id_base + "WHERE ip.status = 'accepted' ORDER BY ip.id DESC")
            else:
                id_pkgs = query_db(
                    id_base + 'JOIN id_approval_steps a ON a.package_id = ip.id '
                    "WHERE (a.approver_id = ? OR (a.approver_id IS NULL AND a.role = ?)) "
                    "AND a.status = 'pending' AND ip.status = 'in_review' "
                    'ORDER BY ip.id DESC',
                    (current_user.id, current_user.role))

        # manager видит все ИД в статусе in_review, но «Ваша очередь» — только где его pending-шаг
        id_my_pending = []
        if current_user.role in id_chain_roles:
            rows = query_db(
                "SELECT a.package_id FROM id_approval_steps a "
                "WHERE (a.approver_id = ? OR (a.approver_id IS NULL AND a.role = ?)) AND a.status = 'pending'",
                (current_user.id, current_user.role))
            id_my_pending = [r['package_id'] for r in rows]

        return render_template('packages/list.html', pkgs=pkgs,
                               status_labels=PACKAGE_STATUS_LABELS,
                               my_pending=my_pending, tab=tab,
                               id_pkgs=id_pkgs, id_my_pending=id_my_pending)

    # ═══ Согласование / возврат ═══

    def _get_my_pending_step(package_id):
        if current_user.role not in CHAIN_ROLES:
            return None
        return query_db(
            "SELECT * FROM approval_steps WHERE package_id = ? AND status = 'pending' "
            "AND (approver_id = ? OR (approver_id IS NULL AND role = ?))",
            (package_id, current_user.id, current_user.role), one=True)

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
            role_label = CHAIN_ROLES.get(current_user.role, current_user.role)
            if next_step.get('approver_id'):
                notify(next_step['approver_id'], 'approval',
                       f'Ваша очередь: пакет «{full["substage_name"]}»',
                       f'{role_label} согласовал(а) пакет по подэтапу «{full["substage_name"]}». Ваша очередь.',
                       f'/packages/{package_id}')
            else:
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
            db.commit()
            # Подэтапы, полностью закрытые накопленным объёмом (включая этот пакет), — в «approved»
            volumes = {s['id']: s for s in _stage_substage_volumes(pkg['stage_id'])}
            for it in _get_package_items(package_id):
                s = volumes.get(it['substage_id'])
                if s and s['fully_closed']:
                    db.execute("UPDATE substages SET status = 'approved' WHERE id = ?", (it['substage_id'],))
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
        role_label = CHAIN_ROLES.get(current_user.role, current_user.role)

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
