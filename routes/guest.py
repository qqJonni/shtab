import uuid
from flask import render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from db import query_db, execute_db
from helpers import role_required


def register(app):

    @app.route('/guest-tokens')
    @login_required
    @role_required('manager', 'admin')
    def guest_tokens_list():
        tokens = query_db(
            'SELECT gt.*, o.name as object_name, u.full_name as creator_name '
            'FROM guest_tokens gt '
            'JOIN objects o ON gt.object_id = o.id '
            'LEFT JOIN users u ON gt.created_by = u.id '
            'ORDER BY gt.created_at DESC')
        objects = query_db("SELECT id, name FROM objects WHERE status='active' ORDER BY name")
        return render_template('guest/tokens.html', tokens=tokens, objects=objects)

    @app.route('/guest-tokens/create', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def guest_token_create():
        object_id = request.form.get('object_id', '')
        if not object_id:
            flash('Выберите объект.', 'danger')
            return redirect(url_for('guest_tokens_list'))

        existing = query_db('SELECT id FROM guest_tokens WHERE object_id = ?', (object_id,), one=True)
        if existing:
            flash('Для этого объекта уже есть ссылка.', 'warning')
            return redirect(url_for('guest_tokens_list'))

        token = uuid.uuid4().hex
        execute_db(
            'INSERT INTO guest_tokens (object_id, token, created_by) VALUES (?, ?, ?)',
            (object_id, token, current_user.id))
        flash('Гостевая ссылка создана.', 'success')
        return redirect(url_for('guest_tokens_list'))

    @app.route('/guest-tokens/<int:token_id>/reset', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def guest_token_reset(token_id):
        gt = query_db('SELECT * FROM guest_tokens WHERE id = ?', (token_id,), one=True)
        if not gt:
            abort(404)
        new_token = uuid.uuid4().hex
        execute_db('UPDATE guest_tokens SET token = ? WHERE id = ?', (new_token, token_id))
        flash('Ссылка сброшена. Старая ссылка больше не работает.', 'success')
        return redirect(url_for('guest_tokens_list'))

    @app.route('/guest-tokens/<int:token_id>/delete', methods=['POST'])
    @login_required
    @role_required('manager', 'admin')
    def guest_token_delete(token_id):
        execute_db('DELETE FROM guest_tokens WHERE id = ?', (token_id,))
        flash('Гостевая ссылка удалена.', 'success')
        return redirect(url_for('guest_tokens_list'))

    @app.route('/guest/<token>')
    def guest_view(token):
        gt = query_db('SELECT * FROM guest_tokens WHERE token = ?', (token,), one=True)
        if not gt:
            abort(404)

        obj = query_db('SELECT * FROM objects WHERE id = ?', (gt['object_id'],), one=True)
        if not obj:
            abort(404)

        stages = query_db(
            'SELECT cs.*, org.name as contractor_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'WHERE cs.object_id = ? ORDER BY cs.order_num', (obj['id'],))

        stages_list = [dict(s) for s in stages]
        total_subs = 0
        total_done = 0
        for s in stages_list:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            s['sub_total'] = len(subs)
            s['sub_done'] = sum(1 for sub in subs if sub['status'] in ('done', 'closed', 'approved'))
            total_subs += s['sub_total']
            total_done += s['sub_done']

        progress = round(total_done / total_subs * 100) if total_subs > 0 else 0

        defects_open = query_db(
            "SELECT COUNT(*) as c FROM defects WHERE object_id = ? AND status NOT IN ('closed','verified')",
            (obj['id'],), one=True)['c']
        defects_closed = query_db(
            "SELECT COUNT(*) as c FROM defects WHERE object_id = ? AND status IN ('closed','verified')",
            (obj['id'],), one=True)['c']

        return render_template('guest/view.html', obj=obj, stages=stages_list,
                               progress=progress, total_subs=total_subs, total_done=total_done,
                               defects_open=defects_open, defects_closed=defects_closed)
