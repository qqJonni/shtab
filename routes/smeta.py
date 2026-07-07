import json
import os
import uuid

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import config
import smeta_parser
from db import get_db, query_db

SMETA_ROLES = ('pto', 'manager', 'admin')
ALLOWED_EXT = {'xlsx', 'csv'}


def _ext(filename: str) -> str:
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def _save_file(file, stage_id: int) -> str:
    ext = _ext(file.filename)
    folder = os.path.join(config.BASE_DIR, 'static', 'smeta', str(stage_id))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{uuid.uuid4().hex}.{ext}')
    file.save(path)
    return path


def register(app):

    # ── Загрузка файла ──────────────────────────────────────────────────────
    @app.route('/stages/<int:stage_id>/smeta/upload', methods=['POST'])
    @login_required
    def smeta_upload(stage_id):
        if current_user.role not in SMETA_ROLES:
            abort(403)
        stage = query_db('SELECT id FROM construction_stages WHERE id = ?',
                         (stage_id,), one=True)
        if not stage:
            abort(404)

        f = request.files.get('smeta_file')
        if not f or not f.filename:
            flash('Выберите файл сметы.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        ext = _ext(f.filename)
        if ext not in ALLOWED_EXT:
            flash('Поддерживаются форматы: xlsx, csv.', 'danger')
            return redirect(url_for('stage_detail', stage_id=stage_id))

        filepath = _save_file(f, stage_id)
        rows = smeta_parser.parse_file(filepath, ext)
        status = 'parsed' if rows else 'failed'

        db = get_db()
        db.execute(
            '''INSERT INTO smeta_imports
               (stage_id, filename, source_type, status, rows_json, uploaded_by)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (stage_id, f.filename, ext, status,
             json.dumps(rows, ensure_ascii=False), current_user.id),
        )
        db.commit()

        imp = query_db(
            'SELECT id FROM smeta_imports WHERE stage_id = ? ORDER BY id DESC LIMIT 1',
            (stage_id,), one=True,
        )

        if not rows:
            flash(
                'Позиции не распознаны. Убедитесь, что файл содержит таблицу '
                'с заголовками (наименование, ед.изм., количество, цена, сумма).',
                'warning',
            )
            return redirect(url_for('stage_detail', stage_id=stage_id))

        return redirect(url_for('smeta_preview', stage_id=stage_id, import_id=imp['id']))

    # ── Предпросмотр черновика ──────────────────────────────────────────────
    @app.route('/stages/<int:stage_id>/smeta/<int:import_id>/preview')
    @login_required
    def smeta_preview(stage_id, import_id):
        if current_user.role not in SMETA_ROLES:
            abort(403)
        stage = query_db(
            'SELECT cs.*, o.name as object_name FROM construction_stages cs '
            'JOIN objects o ON cs.object_id = o.id WHERE cs.id = ?',
            (stage_id,), one=True,
        )
        imp = query_db(
            'SELECT * FROM smeta_imports WHERE id = ? AND stage_id = ?',
            (import_id, stage_id), one=True,
        )
        if not stage or not imp:
            abort(404)

        rows = json.loads(imp['rows_json'] or '[]')
        return render_template(
            'smeta/preview.html',
            stage=stage, imp=imp, rows=rows,
            units=config.UNITS,
        )

    # ── Подтверждение: создаём подэтапы ────────────────────────────────────
    @app.route('/stages/<int:stage_id>/smeta/<int:import_id>/confirm', methods=['POST'])
    @login_required
    def smeta_confirm(stage_id, import_id):
        if current_user.role not in SMETA_ROLES:
            abort(403)
        stage = query_db('SELECT * FROM construction_stages WHERE id = ?',
                         (stage_id,), one=True)
        imp = query_db(
            'SELECT * FROM smeta_imports WHERE id = ? AND stage_id = ? AND status = ?',
            (import_id, stage_id, 'parsed'), one=True,
        )
        if not stage or not imp:
            abort(404)

        names = request.form.getlist('name')
        units = request.form.getlist('unit')
        quantities = request.form.getlist('quantity')
        unit_prices = request.form.getlist('unit_price')
        totals = request.form.getlist('total')

        db = get_db()
        created = 0
        for i, name in enumerate(names):
            name = name.strip()
            if not name:
                continue

            def _f(lst, idx):
                try:
                    v = lst[idx].strip().replace(',', '.')
                    return float(v) if v else None
                except (IndexError, ValueError):
                    return None

            qty = _f(quantities, i)
            price = _f(unit_prices, i)
            total = _f(totals, i)
            unit = units[i].strip() if i < len(units) else ''
            total_price = total if total else (
                round(qty * price, 2) if qty and price else None
            )

            db.execute(
                '''INSERT INTO substages
                   (stage_id, name, volume, unit, unit_price, total_price, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (stage_id, name, qty, unit, price, total_price, current_user.id),
            )
            created += 1

        db.execute(
            "UPDATE smeta_imports SET status = 'confirmed', confirmed_at = "
            "to_char(now(),'YYYY-MM-DD HH24:MI:SS') WHERE id = ?",
            (import_id,),
        )
        db.commit()

        flash(f'Создано подэтапов: {created}.', 'success')
        return redirect(url_for('stage_detail', stage_id=stage_id))

    # ── Отмена черновика ────────────────────────────────────────────────────
    @app.route('/stages/<int:stage_id>/smeta/<int:import_id>/cancel', methods=['POST'])
    @login_required
    def smeta_cancel(stage_id, import_id):
        if current_user.role not in SMETA_ROLES:
            abort(403)
        db = get_db()
        db.execute(
            "UPDATE smeta_imports SET status = 'failed' WHERE id = ? AND stage_id = ?",
            (import_id, stage_id),
        )
        db.commit()
        flash('Импорт отменён.', 'secondary')
        return redirect(url_for('stage_detail', stage_id=stage_id))
