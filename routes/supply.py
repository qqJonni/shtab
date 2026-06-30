from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db, notify
from helpers import role_required

STATUS_LABELS = {
    'submitted': 'Отправлена',
    'returned': 'Возвращена',
    'approved': 'Одобрена',
    'processing': 'В обработке',
    'completed': 'Завершена',
}

VIEWERS = ('manager', 'admin', 'pto', 'supply')


def _get_request_full(req_id):
    return query_db(
        'SELECT mr.*, cs.name as stage_name, cs.object_id, o.name as object_name, '
        'ss.name as substage_name, org.name as contractor_name, u.full_name as requester_name '
        'FROM material_requests mr '
        'JOIN construction_stages cs ON mr.stage_id = cs.id '
        'JOIN objects o ON cs.object_id = o.id '
        'LEFT JOIN substages ss ON mr.substage_id = ss.id '
        'LEFT JOIN organizations org ON mr.contractor_id = org.id '
        'LEFT JOIN users u ON mr.requested_by = u.id '
        'WHERE mr.id = ?', (req_id,), one=True)


def _can_view(mr):
    if current_user.role in VIEWERS:
        return True
    if current_user.role == 'contractor' and mr['contractor_id'] == current_user.organization_id:
        return True
    return False


def _is_own_contractor(mr):
    return current_user.role == 'contractor' and mr['contractor_id'] == current_user.organization_id


def _write_history(req_id, action, comment=None):
    db = get_db()
    db.execute(
        'INSERT INTO material_request_history (request_id, user_id, action, comment) VALUES (?, ?, ?, ?)',
        (req_id, current_user.id, action, comment))
    db.execute('UPDATE material_requests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (req_id,))
    db.commit()


def register(app):

    @app.route('/supply/requests')
    @login_required
    def supply_requests():
        tab = request.args.get('tab', 'active')

        if current_user.role == 'contractor':
            if tab == 'archive':
                reqs = query_db(
                    'SELECT mr.*, cs.name as stage_name, o.name as object_name '
                    'FROM material_requests mr '
                    'JOIN construction_stages cs ON mr.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    "WHERE mr.contractor_id = ? AND mr.status = 'completed' ORDER BY mr.completed_at DESC",
                    (current_user.organization_id,))
            else:
                reqs = query_db(
                    'SELECT mr.*, cs.name as stage_name, o.name as object_name '
                    'FROM material_requests mr '
                    'JOIN construction_stages cs ON mr.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    "WHERE mr.contractor_id = ? AND mr.status != 'completed' ORDER BY mr.created_at DESC",
                    (current_user.organization_id,))
        elif current_user.role in VIEWERS:
            if tab == 'archive':
                reqs = query_db(
                    'SELECT mr.*, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM material_requests mr '
                    'JOIN construction_stages cs ON mr.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON mr.contractor_id = org.id '
                    "WHERE mr.status = 'completed' ORDER BY mr.completed_at DESC")
            else:
                reqs = query_db(
                    'SELECT mr.*, cs.name as stage_name, o.name as object_name, '
                    'org.name as contractor_name '
                    'FROM material_requests mr '
                    'JOIN construction_stages cs ON mr.stage_id = cs.id '
                    'JOIN objects o ON cs.object_id = o.id '
                    'LEFT JOIN organizations org ON mr.contractor_id = org.id '
                    "WHERE mr.status != 'completed' ORDER BY mr.created_at DESC")
        else:
            abort(403)

        counts = {}
        count_where = ''
        count_args = []
        if current_user.role == 'contractor':
            count_where = 'WHERE contractor_id = ?'
            count_args = [current_user.organization_id]
        for st in STATUS_LABELS:
            row = query_db(f'SELECT COUNT(*) as c FROM material_requests {count_where} {"AND" if count_where else "WHERE"} status = ?',
                           count_args + [st], one=True)
            counts[st] = row['c']

        return render_template('supply/list.html', reqs=reqs, counts=counts,
                               status_labels=STATUS_LABELS, tab=tab)

    @app.route('/supply/requests/add', methods=['GET', 'POST'])
    @login_required
    @role_required('contractor')
    def supply_request_add():
        # Get contractor's stages
        stages = query_db(
            'SELECT cs.id, cs.name, o.name as object_name '
            'FROM construction_stages cs '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE cs.contractor_id = ? ORDER BY o.name, cs.order_num',
            (current_user.organization_id,))

        if request.method == 'POST':
            stage_id = request.form.get('stage_id', '')
            substage_id = request.form.get('substage_id', '').strip() or None

            if not stage_id:
                flash('Выберите этап.', 'danger')
                return render_template('supply/form.html', stages=stages)

            stage = query_db('SELECT * FROM construction_stages WHERE id = ? AND contractor_id = ?',
                             (stage_id, current_user.organization_id), one=True)
            if not stage:
                abort(403)

            # Parse items from form
            names = request.form.getlist('item_name[]')
            units = request.form.getlist('item_unit[]')
            qtys = request.form.getlist('item_qty[]')
            prices = request.form.getlist('item_price[]')
            comments = request.form.getlist('item_comment[]')

            items = []
            for i in range(len(names)):
                name = names[i].strip() if i < len(names) else ''
                if not name:
                    continue
                items.append({
                    'name': name,
                    'unit': units[i].strip() if i < len(units) else '',
                    'qty': qtys[i].strip() if i < len(qtys) else '',
                    'price': prices[i].strip() if i < len(prices) else '',
                    'comment': comments[i].strip() if i < len(comments) else '',
                })

            if not items:
                flash('Добавьте хотя бы одну позицию.', 'danger')
                return render_template('supply/form.html', stages=stages)

            db = get_db()
            cur = db.execute(
                'INSERT INTO material_requests (stage_id, substage_id, contractor_id, requested_by) '
                'VALUES (?, ?, ?, ?)',
                (stage_id, substage_id, current_user.organization_id, current_user.id))
            req_id = cur.lastrowid

            for item in items:
                try:
                    qty = float(item['qty']) if item['qty'] else None
                except ValueError:
                    qty = None
                try:
                    price = float(item['price']) if item['price'] else None
                except ValueError:
                    price = None
                db.execute(
                    'INSERT INTO material_request_items (request_id, material_name, unit, quantity, price, comment) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (req_id, item['name'], item['unit'], qty, price, item['comment']))

            db.execute(
                'INSERT INTO material_request_history (request_id, user_id, action) VALUES (?, ?, ?)',
                (req_id, current_user.id, 'created'))
            db.commit()

            # Notify PTO
            obj = query_db('SELECT name FROM objects WHERE id = ?', (stage['object_id'],), one=True)
            pto_users = query_db("SELECT id FROM users WHERE role = 'pto' AND is_approved = 1")
            for u in pto_users:
                notify(u['id'], 'supply',
                       f'Заявка на материал #{req_id}',
                       f'Подрядчик «{query_db("SELECT name FROM organizations WHERE id=?", (current_user.organization_id,), one=True)["name"]}» '
                       f'подал заявку на давальческий материал (этап «{stage["name"]}», объект «{obj["name"]}»).',
                       f'/supply/requests/{req_id}')

            flash('Заявка отправлена.', 'success')
            return redirect(url_for('supply_request_detail', req_id=req_id))

        return render_template('supply/form.html', stages=stages)

    @app.route('/supply/requests/<int:req_id>')
    @login_required
    def supply_request_detail(req_id):
        mr = _get_request_full(req_id)
        if not mr or not _can_view(mr):
            abort(403)

        items = query_db('SELECT * FROM material_request_items WHERE request_id = ? ORDER BY id', (req_id,))
        history = query_db(
            'SELECT h.*, u.full_name as user_name FROM material_request_history h '
            'LEFT JOIN users u ON h.user_id = u.id '
            'WHERE h.request_id = ? ORDER BY h.created_at DESC', (req_id,))

        return render_template('supply/detail.html', mr=mr, items=items, history=history,
                               status_labels=STATUS_LABELS, is_own=_is_own_contractor(mr))

    # ═══ Действия ПТО ═══

    @app.route('/supply/requests/<int:req_id>/approve', methods=['POST'])
    @login_required
    @role_required('pto', 'admin')
    def supply_request_approve(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'submitted' or mr['route_role'] != 'pto':
            abort(403)

        comment = request.form.get('comment', '').strip() or None
        db = get_db()
        db.execute("UPDATE material_requests SET status='approved', route_role='supply' WHERE id=?", (req_id,))
        db.commit()
        _write_history(req_id, 'approved', comment)

        full = _get_request_full(req_id)
        supply_users = query_db("SELECT id FROM users WHERE role='supply' AND is_approved=1")
        for u in supply_users:
            notify(u['id'], 'supply',
                   f'Заявка #{req_id} одобрена ПТО',
                   f'Заявка на материал (этап «{full["stage_name"]}», объект «{full["object_name"]}») одобрена и передана вам.',
                   f'/supply/requests/{req_id}')

        flash('Заявка одобрена и передана снабженцу.', 'success')
        return redirect(url_for('supply_request_detail', req_id=req_id))

    @app.route('/supply/requests/<int:req_id>/return', methods=['POST'])
    @login_required
    @role_required('pto', 'admin')
    def supply_request_return(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'submitted' or mr['route_role'] != 'pto':
            abort(403)

        comment = request.form.get('comment', '').strip()
        if not comment:
            flash('Укажите причину возврата.', 'danger')
            return redirect(url_for('supply_request_detail', req_id=req_id))

        execute_db("UPDATE material_requests SET status='returned' WHERE id=?", (req_id,))
        _write_history(req_id, 'returned', comment)

        if mr['contractor_id']:
            users = query_db('SELECT id FROM users WHERE organization_id=? AND is_approved=1',
                             (mr['contractor_id'],))
            full = _get_request_full(req_id)
            for u in users:
                notify(u['id'], 'supply',
                       f'Заявка #{req_id} возвращена',
                       f'ПТО вернул заявку на материал: {comment}',
                       f'/supply/requests/{req_id}')

        flash('Заявка возвращена подрядчику.', 'success')
        return redirect(url_for('supply_request_detail', req_id=req_id))

    # ═══ Редактирование заявки подрядчиком ═══

    @app.route('/supply/requests/<int:req_id>/edit', methods=['GET', 'POST'])
    @login_required
    def supply_request_edit(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'returned':
            abort(404)
        if not _is_own_contractor(mr):
            abort(403)

        if request.method == 'POST':
            names = request.form.getlist('item_name[]')
            units = request.form.getlist('item_unit[]')
            qtys = request.form.getlist('item_qty[]')
            comments = request.form.getlist('item_comment[]')

            items = []
            for i in range(len(names)):
                name = names[i].strip() if i < len(names) else ''
                if not name:
                    continue
                items.append({
                    'name': name,
                    'unit': units[i].strip() if i < len(units) else '',
                    'qty': qtys[i].strip() if i < len(qtys) else '',
                    'comment': comments[i].strip() if i < len(comments) else '',
                })

            if not items:
                flash('Добавьте хотя бы одну позицию.', 'danger')
                return redirect(url_for('supply_request_edit', req_id=req_id))

            db = get_db()
            db.execute('DELETE FROM material_request_items WHERE request_id = ?', (req_id,))
            for item in items:
                try:
                    qty = float(item['qty']) if item['qty'] else None
                except ValueError:
                    qty = None
                db.execute(
                    'INSERT INTO material_request_items (request_id, material_name, unit, quantity, comment) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (req_id, item['name'], item['unit'], qty, item['comment']))
            db.commit()
            _write_history(req_id, 'edited', 'Позиции отредактированы')

            flash('Заявка отредактирована.', 'success')
            return redirect(url_for('supply_request_detail', req_id=req_id))

        items = query_db('SELECT * FROM material_request_items WHERE request_id = ? ORDER BY id', (req_id,))
        full = _get_request_full(req_id)
        return render_template('supply/edit.html', mr=mr, full=full, items=items)

    # ═══ Повторная отправка подрядчиком ═══

    @app.route('/supply/requests/<int:req_id>/resubmit', methods=['POST'])
    @login_required
    def supply_request_resubmit(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'returned':
            abort(404)
        if not _is_own_contractor(mr):
            abort(403)

        execute_db("UPDATE material_requests SET status='submitted', route_role='pto' WHERE id=?", (req_id,))
        _write_history(req_id, 'resubmitted')

        pto_users = query_db("SELECT id FROM users WHERE role='pto' AND is_approved=1")
        full = _get_request_full(req_id)
        for u in pto_users:
            notify(u['id'], 'supply',
                   f'Заявка #{req_id} повторно отправлена',
                   f'Подрядчик исправил и повторно отправил заявку на материал (этап «{full["stage_name"]}»).',
                   f'/supply/requests/{req_id}')

        flash('Заявка повторно отправлена на проверку ПТО.', 'success')
        return redirect(url_for('supply_request_detail', req_id=req_id))

    # ═══ Действия снабженца ═══

    @app.route('/supply/requests/<int:req_id>/take', methods=['POST'])
    @login_required
    @role_required('supply', 'admin')
    def supply_request_take(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'approved':
            abort(403)

        execute_db("UPDATE material_requests SET status='processing' WHERE id=?", (req_id,))
        _write_history(req_id, 'processing')

        if mr['contractor_id']:
            full = _get_request_full(req_id)
            users = query_db('SELECT id FROM users WHERE organization_id=? AND is_approved=1',
                             (mr['contractor_id'],))
            for u in users:
                notify(u['id'], 'supply',
                       f'Заявка #{req_id} в обработке',
                       f'Снабженец взял вашу заявку на материал в обработку.',
                       f'/supply/requests/{req_id}')

        flash('Заявка взята в обработку.', 'success')
        return redirect(url_for('supply_request_detail', req_id=req_id))

    @app.route('/supply/requests/<int:req_id>/complete', methods=['POST'])
    @login_required
    @role_required('supply', 'admin')
    def supply_request_complete(req_id):
        mr = query_db('SELECT * FROM material_requests WHERE id = ?', (req_id,), one=True)
        if not mr or mr['status'] != 'processing':
            abort(403)

        comment = request.form.get('comment', '').strip() or None
        db = get_db()
        db.execute("UPDATE material_requests SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?",
                   (req_id,))
        db.commit()
        _write_history(req_id, 'completed', comment)

        if mr['contractor_id']:
            users = query_db('SELECT id FROM users WHERE organization_id=? AND is_approved=1',
                             (mr['contractor_id'],))
            for u in users:
                notify(u['id'], 'supply',
                       f'Заявка #{req_id} завершена',
                       f'Материал по вашей заявке готов/выдан.{" " + comment if comment else ""}',
                       f'/supply/requests/{req_id}')

        flash('Заявка завершена.', 'success')
        return redirect(url_for('supply_request_detail', req_id=req_id))

    # API: substages for prefill
    @app.route('/api/stage-materials/<int:stage_id>')
    @login_required
    def api_stage_materials(stage_id):
        subs = query_db('SELECT name, unit, volume FROM substages WHERE stage_id = ? ORDER BY id', (stage_id,))
        from flask import jsonify
        return jsonify([dict(s) for s in subs])

    # API: approved/completed material request items for raw material report prefill
    @app.route('/api/material-request-items/<int:stage_id>/<int:contractor_id>')
    @login_required
    def api_material_request_items(stage_id, contractor_id):
        items = query_db(
            'SELECT mri.material_name as name, mri.unit, mri.quantity as issued '
            'FROM material_request_items mri '
            'JOIN material_requests mr ON mri.request_id = mr.id '
            'WHERE mr.stage_id = ? AND mr.contractor_id = ? '
            "AND mr.status IN ('approved', 'processing', 'completed') "
            'ORDER BY mri.id',
            (stage_id, contractor_id))
        from flask import jsonify
        return jsonify([dict(i) for i in items])
