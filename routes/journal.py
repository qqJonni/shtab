import os
import io
from datetime import date
from flask import render_template, redirect, url_for, request, flash, abort, send_file
from flask_login import login_required, current_user

import config
from db import query_db, execute_db, get_db
from helpers import role_required, save_journal_photo
from doc_generator import _fmt_date

VIEWERS = ('manager', 'admin', 'pto', 'inspector', 'foreman')
WRITERS = ('foreman', 'inspector', 'manager', 'pto', 'admin')


def register(app):

    @app.route('/objects/<int:obj_id>/journal')
    @login_required
    @role_required(*VIEWERS)
    def journal_list(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)
        entries = query_db(
            'SELECT je.*, u.full_name as author_name, u.role as author_role '
            'FROM journal_entries je '
            'LEFT JOIN users u ON je.author_id = u.id '
            'WHERE je.object_id = ? ORDER BY je.entry_date DESC, je.created_at DESC',
            (obj_id,))
        entries_list = [dict(e) for e in entries]
        for e in entries_list:
            photos = query_db('SELECT * FROM journal_photos WHERE entry_id = ? ORDER BY id', (e['id'],))
            e['photos'] = [dict(p) for p in photos]

        return render_template('journal/list.html', obj=obj, entries=entries_list)

    @app.route('/objects/<int:obj_id>/journal/add', methods=['GET', 'POST'])
    @login_required
    @role_required(*WRITERS)
    def journal_add(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        if request.method == 'POST':
            text = request.form.get('text', '').strip()
            entry_date = request.form.get('entry_date', '').strip() or date.today().isoformat()
            weather = request.form.get('weather', '').strip()

            if not text:
                flash('Введите текст записи.', 'danger')
                return render_template('journal/form.html', obj=obj, entry=None)

            db = get_db()
            cur = db.execute(
                'INSERT INTO journal_entries (object_id, author_id, entry_date, text, weather) '
                'VALUES (?, ?, ?, ?, ?)',
                (obj_id, current_user.id, entry_date, text, weather))
            entry_id = cur.lastrowid
            db.commit()

            photos = request.files.getlist('photos')
            for f in photos:
                filename = save_journal_photo(f, entry_id)
                if filename:
                    execute_db('INSERT INTO journal_photos (entry_id, filename) VALUES (?, ?)',
                               (entry_id, filename))

            flash('Запись добавлена.', 'success')
            return redirect(url_for('journal_list', obj_id=obj_id))

        return render_template('journal/form.html', obj=obj, entry=None)

    @app.route('/journal/<int:entry_id>/edit', methods=['GET', 'POST'])
    @login_required
    def journal_edit(entry_id):
        entry = query_db('SELECT * FROM journal_entries WHERE id = ?', (entry_id,), one=True)
        if not entry:
            abort(404)
        if entry['author_id'] != current_user.id and current_user.role not in ('manager', 'admin'):
            abort(403)
        obj = query_db('SELECT * FROM objects WHERE id = ?', (entry['object_id'],), one=True)

        if request.method == 'POST':
            text = request.form.get('text', '').strip()
            entry_date = request.form.get('entry_date', '').strip() or entry['entry_date']
            weather = request.form.get('weather', '').strip()
            if not text:
                flash('Введите текст записи.', 'danger')
                return render_template('journal/form.html', obj=obj, entry=entry)

            execute_db('UPDATE journal_entries SET text=?, entry_date=?, weather=? WHERE id=?',
                       (text, entry_date, weather, entry_id))

            photos = request.files.getlist('photos')
            for f in photos:
                filename = save_journal_photo(f, entry_id)
                if filename:
                    execute_db('INSERT INTO journal_photos (entry_id, filename) VALUES (?, ?)',
                               (entry_id, filename))

            flash('Запись обновлена.', 'success')
            return redirect(url_for('journal_list', obj_id=entry['object_id']))

        photos = query_db('SELECT * FROM journal_photos WHERE entry_id = ? ORDER BY id', (entry_id,))
        return render_template('journal/form.html', obj=obj, entry=entry, photos=photos)

    @app.route('/journal/<int:entry_id>/delete', methods=['POST'])
    @login_required
    def journal_delete(entry_id):
        entry = query_db('SELECT * FROM journal_entries WHERE id = ?', (entry_id,), one=True)
        if not entry:
            abort(404)
        if entry['author_id'] != current_user.id and current_user.role not in ('manager', 'admin'):
            abort(403)
        import shutil
        photo_dir = os.path.join(config.JOURNAL_FOLDER, str(entry_id))
        if os.path.isdir(photo_dir):
            shutil.rmtree(photo_dir)
        execute_db('DELETE FROM journal_photos WHERE entry_id = ?', (entry_id,))
        execute_db('DELETE FROM journal_entries WHERE id = ?', (entry_id,))
        flash('Запись удалена.', 'success')
        return redirect(url_for('journal_list', obj_id=entry['object_id']))

    # ═══ PDF-отчёт по объекту ═══

    @app.route('/objects/<int:obj_id>/report-pdf')
    @login_required
    @role_required('manager', 'pto', 'admin')
    def object_report_pdf(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        stages = query_db(
            'SELECT cs.*, org.name as contractor_name '
            'FROM construction_stages cs '
            'LEFT JOIN organizations org ON cs.contractor_id = org.id '
            'WHERE cs.object_id = ? ORDER BY cs.order_num', (obj_id,))

        stages_data = []
        total_subs = 0
        total_done = 0
        for s in stages:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            sub_total = len(subs)
            sub_done = sum(1 for sub in subs if sub['status'] in ('done', 'closed', 'approved'))
            total_subs += sub_total
            total_done += sub_done
            stages_data.append({
                'name': s['name'], 'contractor': s['contractor_name'] or '—',
                'status': s['status'], 'plan_start': s['plan_start_date'],
                'plan_end': s['plan_end_date'], 'sub_total': sub_total, 'sub_done': sub_done,
                'progress': round(sub_done / sub_total * 100) if sub_total else 0,
            })
        progress = round(total_done / total_subs * 100) if total_subs else 0

        defects_open = query_db("SELECT COUNT(*) as c FROM defects WHERE object_id=? AND status NOT IN ('closed','verified')", (obj_id,), one=True)['c']
        defects_closed = query_db("SELECT COUNT(*) as c FROM defects WHERE object_id=? AND status IN ('closed','verified')", (obj_id,), one=True)['c']
        pkgs_review = query_db(
            "SELECT COUNT(*) as c FROM doc_packages dp JOIN substages ss ON dp.substage_id=ss.id "
            "JOIN construction_stages cs ON ss.stage_id=cs.id WHERE cs.object_id=? AND dp.status='in_review'", (obj_id,), one=True)['c']
        pkgs_completed = query_db(
            "SELECT COUNT(*) as c FROM doc_packages dp JOIN substages ss ON dp.substage_id=ss.id "
            "JOIN construction_stages cs ON ss.stage_id=cs.id WHERE cs.object_id=? AND dp.status='completed'", (obj_id,), one=True)['c']
        mr_active = query_db(
            "SELECT COUNT(*) as c FROM material_requests mr JOIN construction_stages cs ON mr.stage_id=cs.id "
            "WHERE cs.object_id=? AND mr.status NOT IN ('completed')", (obj_id,), one=True)['c']

        journal = query_db(
            'SELECT je.*, u.full_name as author_name FROM journal_entries je '
            'LEFT JOIN users u ON je.author_id = u.id '
            'WHERE je.object_id = ? ORDER BY je.entry_date DESC LIMIT 10', (obj_id,))

        pdf_buf = _build_object_pdf(obj, stages_data, progress, total_subs, total_done,
                                     defects_open, defects_closed, pkgs_review, pkgs_completed,
                                     mr_active, journal)

        safe_name = obj['name'].replace('/', '-').replace('"', '')[:40]
        return send_file(pdf_buf, as_attachment=True,
                         download_name=f'Отчёт_{safe_name}_{date.today().isoformat()}.pdf',
                         mimetype='application/pdf')


def _build_object_pdf(obj, stages, progress, total_subs, total_done,
                       defects_open, defects_closed, pkgs_review, pkgs_completed,
                       mr_active, journal):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab.lib.enums as enums

    buf = io.BytesIO()

    font_registered = False
    for fp in ['/System/Library/Fonts/Supplemental/Arial.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
               '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('MainFont', fp))
                font_registered = True
                break
            except Exception:
                continue
    mf = 'MainFont' if font_registered else 'Helvetica'

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)

    sn = ParagraphStyle('n', fontName=mf, fontSize=9, leading=12)
    sb = ParagraphStyle('b', fontName=mf, fontSize=9, leading=12)
    sc = ParagraphStyle('c', fontName=mf, fontSize=9, leading=12, alignment=enums.TA_CENTER)
    st = ParagraphStyle('t', fontName=mf, fontSize=14, leading=18, alignment=enums.TA_CENTER)
    sh = ParagraphStyle('h', fontName=mf, fontSize=11, leading=14)
    ss = ParagraphStyle('s', fontName=mf, fontSize=8, leading=10)

    status_labels = {'planned': 'Планируется', 'in_progress': 'В работе',
                     'done': 'Завершён', 'suspended': 'Приостановлен'}

    elements = []

    # ═══ ТИТУЛ ═══
    elements.append(Paragraph('<b>СВОДНЫЙ ОТЧЁТ ПО ОБЪЕКТУ</b>', st))
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph(f'<b>{obj["name"]}</b>', ParagraphStyle('title', fontName=mf, fontSize=12, alignment=enums.TA_CENTER)))
    if obj['address']:
        elements.append(Paragraph(obj['address'], sc))
    elements.append(Spacer(1, 2*mm))
    elements.append(Paragraph(f'Дата формирования: {_fmt_date(date.today().isoformat())}', sc))
    elements.append(Spacer(1, 6*mm))

    # ═══ СВОДКА ═══
    elements.append(Paragraph('<b>Общая сводка</b>', sh))
    elements.append(Spacer(1, 2*mm))

    summary = [
        ['Общий прогресс', f'{progress}% ({total_done} из {total_subs} подэтапов)'],
        ['Этапов', str(len(stages))],
        ['Замечания открытые', str(defects_open)],
        ['Замечания закрытые', str(defects_closed)],
        ['Пакеты на согласовании', str(pkgs_review)],
        ['Пакеты завершённые', str(pkgs_completed)],
        ['Заявки на материал (активные)', str(mr_active)],
    ]

    thin = colors.Color(0.8, 0.8, 0.8)
    t = Table(summary, colWidths=[70*mm, 100*mm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), mf), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, thin),
        ('BACKGROUND', (0, 0), (0, -1), colors.Color(0.96, 0.97, 0.98)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 6*mm))

    # ═══ ЭТАПЫ ═══
    elements.append(Paragraph('<b>Этапы строительства</b>', sh))
    elements.append(Spacer(1, 2*mm))

    stg_data = [['№', 'Этап', 'Подрядчик', 'Статус', 'Сроки', 'Прогресс']]
    for i, s in enumerate(stages, 1):
        dates = ''
        if s['plan_start']:
            dates = _fmt_date(s['plan_start'])
        if s['plan_end']:
            dates += f" — {_fmt_date(s['plan_end'])}"
        if not dates:
            dates = '—'
        stg_data.append([
            str(i), Paragraph(s['name'], ss), s['contractor'],
            status_labels.get(s['status'], s['status']), dates,
            f"{s['progress']}% ({s['sub_done']}/{s['sub_total']})" if s['sub_total'] else '—',
        ])

    t2 = Table(stg_data, colWidths=[8*mm, 45*mm, 35*mm, 25*mm, 35*mm, 25*mm])
    t2.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), mf), ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, thin),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.96, 0.97, 0.98)),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
    ]))
    elements.append(t2)
    elements.append(Spacer(1, 6*mm))

    # ═══ ЖУРНАЛ ═══
    if journal:
        elements.append(Paragraph('<b>Последние записи журнала</b>', sh))
        elements.append(Spacer(1, 2*mm))

        s_journal = ParagraphStyle('j', fontName=mf, fontSize=8, leading=10)
        for je in journal:
            je_date = _fmt_date(je['entry_date'])
            author = je['author_name'] or '—'
            weather = f" · {je['weather']}" if je['weather'] else ''
            elements.append(Paragraph(f'<b>{je_date}</b> — {author}{weather}', s_journal))
            elements.append(Paragraph(je['text'][:300] + ('…' if len(je['text']) > 300 else ''), s_journal))
            elements.append(Spacer(1, 2*mm))

    # Footer
    elements.append(Spacer(1, 10*mm))
    elements.append(Paragraph('Сформировано системой ШТАБ', ParagraphStyle('f', fontName=mf, fontSize=7, alignment=enums.TA_CENTER, textColor=colors.gray)))

    doc.build(elements)
    buf.seek(0)
    return buf
