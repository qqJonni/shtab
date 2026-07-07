import io
import os
from datetime import date, timedelta

from flask import abort, render_template, request, send_file
from flask_login import current_user, login_required

from db import query_db, notify
from helpers import role_required

DIGEST_ROLES = ('manager', 'admin', 'pto')

_STATUS_LABELS = {
    'planned': 'Планируется', 'in_progress': 'В работе',
    'done': 'Завершён', 'suspended': 'Приостановлен',
}
_PRIORITY_LABELS = {
    'critical': 'Критично', 'high': 'Высокий',
    'normal': 'Обычный', 'low': 'Низкий',
}


def _fmt_date(s):
    if not s:
        return '—'
    try:
        return f'{s[8:10]}.{s[5:7]}.{s[:4]}'
    except Exception:
        return s


def _fmt_money(v):
    if v is None or v == 0:
        return '—'
    return f'{v:,.0f} ₽'.replace(',', ' ')


# ── PDF-инфраструктура (переиспользуется из journal._build_object_pdf) ──────


def _register_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for fp in ['/System/Library/Fonts/Supplemental/Arial.ttf',
               '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
               '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('DigestFont', fp))
                return 'DigestFont'
            except Exception:
                continue
    return 'Helvetica'


def _build_digest_pdf(obj, d):
    """Формирует PDF-сводку из dict, возвращённого object_digest()."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    import reportlab.lib.enums as enums

    buf = io.BytesIO()
    mf = _register_font()

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)

    sn = ParagraphStyle('dn', fontName=mf, fontSize=9, leading=12)
    sc = ParagraphStyle('dc', fontName=mf, fontSize=9, leading=12, alignment=enums.TA_CENTER)
    st = ParagraphStyle('dt', fontName=mf, fontSize=14, leading=18, alignment=enums.TA_CENTER)
    sh = ParagraphStyle('dh', fontName=mf, fontSize=11, leading=14)
    ss = ParagraphStyle('ds', fontName=mf, fontSize=8, leading=10)
    thin = colors.Color(0.8, 0.8, 0.8)
    blue_bg = colors.Color(0.96, 0.97, 0.98)
    red_bg  = colors.Color(1.0, 0.95, 0.95)

    def tbl(data, widths, header_row=True):
        t = Table(data, colWidths=widths)
        style = [
            ('FONTNAME',    (0, 0), (-1, -1), mf),
            ('FONTSIZE',    (0, 0), (-1, -1), 8),
            ('GRID',        (0, 0), (-1, -1), 0.5, thin),
            ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]
        if header_row:
            style += [
                ('BACKGROUND', (0, 0), (-1, 0), blue_bg),
                ('ALIGN',      (0, 0), (-1, 0), 'CENTER'),
                ('FONTSIZE',   (0, 0), (-1, 0), 8),
            ]
        else:
            style.append(('BACKGROUND', (0, 0), (0, -1), blue_bg))
        t.setStyle(TableStyle(style))
        return t

    el = []

    # ── ТИТУЛ ────────────────────────────────────────────────────────────────
    el.append(Paragraph('<b>ДАЙДЖЕСТ ПО ОБЪЕКТУ</b>', st))
    el.append(Spacer(1, 3*mm))
    el.append(Paragraph(f'<b>{obj["name"]}</b>',
                        ParagraphStyle('dtt', fontName=mf, fontSize=12, alignment=enums.TA_CENTER)))
    if obj.get('address'):
        el.append(Paragraph(obj['address'], sc))
    el.append(Spacer(1, 2*mm))
    el.append(Paragraph(
        f'Период: последние {d["period_days"]} дн. · Дата: {_fmt_date(d["generated_at"])}', sc))
    el.append(Spacer(1, 6*mm))

    # ── KPI ──────────────────────────────────────────────────────────────────
    el.append(Paragraph('<b>Ключевые показатели</b>', sh))
    el.append(Spacer(1, 2*mm))
    kpi = [
        ['Готовность',                f'{d["progress_pct"]}% ({d["sub_done"]} из {d["sub_total"]} подэтапов)'],
        ['Выполнено за период',       f'{_fmt_money(d["period_completed_sum"])} / {d["sub_done_week"]} подэт.'],
        ['Просрочено подэтапов',      str(d['sub_overdue'])],
        ['Замечания открытые',        str(d['defects_open'])],
        ['Замечания просроченные',    str(d['defects_overdue'])],
        ['Пакеты на согласовании',    str(d['pkgs_in_review'])],
        ['Пакеты зависли >7 дн.',     str(d['pkgs_stalled'])],
    ]
    el.append(tbl(kpi, [85*mm, 95*mm], header_row=False))
    el.append(Spacer(1, 6*mm))

    # ── ФИНАНСЫ ──────────────────────────────────────────────────────────────
    el.append(Paragraph('<b>Деньги</b>', sh))
    el.append(Spacer(1, 2*mm))
    money = [
        ['Показатель', 'Сумма'],
        ['Смета (подэтапы)',    _fmt_money(d['smeta_sum'])],
        ['Выполнено накопит.',  _fmt_money(d['completed_sum'])],
        ['Остаток',             _fmt_money(d['remaining_sum'])],
        ['Сумма договоров',     _fmt_money(d['contract_sum']) if d['contract_sum'] else '—'],
        ['% выполнения',        f'{d["contract_pct"]}%' if d['contract_pct'] is not None else '—'],
    ]
    el.append(tbl(money, [85*mm, 95*mm]))
    el.append(Spacer(1, 6*mm))

    # ── ТРЕБУЕТ ВНИМАНИЯ ─────────────────────────────────────────────────────
    if d['overdue_subs'] or d['stalled_pkgs'] or d['returned_pkgs']:
        el.append(Paragraph('<b>⚠ Требует внимания</b>', sh))
        el.append(Spacer(1, 2*mm))

        if d['overdue_subs']:
            el.append(Paragraph('Просроченные подэтапы:', sn))
            el.append(Spacer(1, 1*mm))
            rows = [['Подэтап', 'Этап', 'Просрочка', 'Плановая дата']]
            for s in d['overdue_subs']:
                rows.append([
                    Paragraph(s['name'], ss),
                    Paragraph(s['stage_name'], ss),
                    f'+{s["days_overdue"]} дн.',
                    _fmt_date(s['plan_end_date']),
                ])
            t_ov = Table(rows, colWidths=[60*mm, 55*mm, 25*mm, 40*mm])
            t_ov.setStyle(TableStyle([
                ('FONTNAME',    (0, 0), (-1, -1), mf), ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID',        (0, 0), (-1, -1), 0.5, thin),
                ('BACKGROUND',  (0, 0), (-1, 0), blue_bg),
                ('BACKGROUND',  (0, 1), (-1, -1), red_bg),
                ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ]))
            el.append(t_ov)
            el.append(Spacer(1, 3*mm))

        if d['stalled_pkgs']:
            el.append(Paragraph('Зависшие согласования:', sn))
            el.append(Spacer(1, 1*mm))
            rows = [['Подэтап', 'Роль', 'Дней ожидания']]
            for p in d['stalled_pkgs']:
                rows.append([Paragraph(p['substage_name'], ss),
                             p['pending_role_label'], f'{p["days_waiting"]} дн.'])
            el.append(tbl(rows, [100*mm, 50*mm, 30*mm]))
            el.append(Spacer(1, 3*mm))

        if d['returned_pkgs']:
            el.append(Paragraph('Возвращённые пакеты КС:', sn))
            el.append(Spacer(1, 1*mm))
            rows = [['Подэтап', 'Этап']]
            for p in d['returned_pkgs']:
                rows.append([Paragraph(p['substage_name'], ss), Paragraph(p['stage_name'], ss)])
            el.append(tbl(rows, [90*mm, 90*mm]))
            el.append(Spacer(1, 3*mm))

        el.append(Spacer(1, 3*mm))

    # ── ЧТО СДЕЛАНО ──────────────────────────────────────────────────────────
    if d['period_done_subs']:
        el.append(Paragraph(f'<b>Выполнено за {d["period_days"]} дней</b>', sh))
        el.append(Spacer(1, 2*mm))
        rows = [['Подэтап', 'Этап', 'Дата', 'Сумма']]
        for s in d['period_done_subs']:
            rows.append([
                Paragraph(s['name'], ss),
                Paragraph(s['stage_name'], ss),
                _fmt_date(s['completed_at']),
                _fmt_money(s['total_price']),
            ])
        el.append(tbl(rows, [65*mm, 55*mm, 25*mm, 35*mm]))
        el.append(Spacer(1, 6*mm))

    # ── ТОП ЗАМЕЧАНИЙ ────────────────────────────────────────────────────────
    if d['top_defects']:
        el.append(Paragraph('<b>Открытые замечания (топ)</b>', sh))
        el.append(Spacer(1, 2*mm))
        rows = [['Замечание', 'Приоритет', 'Этап', 'Срок']]
        for r in d['top_defects']:
            due = _fmt_date(r['due_date'])
            if r.get('overdue') and r.get('days_overdue'):
                due += f' (+{r["days_overdue"]}д)'
            rows.append([
                Paragraph(r['title'], ss),
                _PRIORITY_LABELS.get(r['priority'], r['priority']),
                Paragraph(r['stage_name'] or '—', ss),
                due,
            ])
        el.append(tbl(rows, [70*mm, 25*mm, 55*mm, 30*mm]))
        el.append(Spacer(1, 6*mm))

    # ── ЭТАПЫ ────────────────────────────────────────────────────────────────
    el.append(Paragraph('<b>Этапы строительства</b>', sh))
    el.append(Spacer(1, 2*mm))
    rows = [['Этап', 'Подрядчик', 'Прогресс', 'Смета', 'Срок']]
    for s in d['stages']:
        end = _fmt_date(s['plan_end_date'])
        if s.get('overdue') and s.get('days_overdue'):
            end += f'\n+{s["days_overdue"]}д'
        rows.append([
            Paragraph(s['name'], ss),
            Paragraph(s['contractor_name'] or '—', ss),
            f'{s["progress_pct"]}% ({s["sub_done"]}/{s["sub_total"]})',
            _fmt_money(s['smeta_sum']),
            Paragraph(end, ss),
        ])
    t_stg = Table(rows, colWidths=[55*mm, 40*mm, 28*mm, 30*mm, 27*mm])
    st_style = TableStyle([
        ('FONTNAME',    (0, 0), (-1, -1), mf), ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID',        (0, 0), (-1, -1), 0.5, thin),
        ('BACKGROUND',  (0, 0), (-1, 0), blue_bg),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
    ])
    # красим просроченные строки
    for i, s in enumerate(d['stages'], 1):
        if s.get('overdue'):
            st_style.add('BACKGROUND', (0, i), (-1, i), red_bg)
    t_stg.setStyle(st_style)
    el.append(t_stg)
    el.append(Spacer(1, 6*mm))

    # ── ЖУРНАЛ ───────────────────────────────────────────────────────────────
    if d['journal']:
        el.append(Paragraph(f'<b>Активность за {d["period_days"]} дней</b>', sh))
        el.append(Spacer(1, 2*mm))
        rows = [['Дата', 'Вид работ', 'Организация']]
        for je in d['journal']:
            rows.append([
                _fmt_date(je['entry_date']),
                Paragraph(je['work_type'] or je.get('text', '')[:60] or '—', ss),
                Paragraph(je['contractor_name'] or je.get('author_name', '') or '—', ss),
            ])
        el.append(tbl(rows, [22*mm, 100*mm, 58*mm]))

    doc.build(el)
    buf.seek(0)
    return buf


# ── Функция еженедельной рассылки (вызывается из cron-скрипта) ──────────────


def send_weekly_digests(app=None):
    """
    Формирует сводку по каждому активному объекту и шлёт notify()
    всем пользователям с ролью 'manager' или 'admin'.

    Вызов: python3 -c "from routes.digest import send_weekly_digests; send_weekly_digests()"
    или через Flask app_context, если импортируется из приложения.
    """
    import sys

    if app is None:
        # Запуск вне Flask — создаём контекст сами
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from dotenv import load_dotenv
        load_dotenv()
        from app import app as flask_app
        app = flask_app

    with app.app_context():
        from digest import object_digest
        from db import query_db as qdb, notify as _notify

        objects = qdb("SELECT id, name FROM objects WHERE status = 'active'")
        managers = qdb("SELECT id FROM users WHERE role IN ('manager', 'admin')")

        if not managers:
            print('send_weekly_digests: нет получателей (manager/admin)')
            return

        sent = 0
        for obj in objects:
            try:
                d = object_digest(obj['id'], period_days=7)
            except Exception as e:
                print(f'  объект {obj["id"]} — ошибка digest: {e}')
                continue

            # Краткая выжимка для тела уведомления
            parts = [f'Готовность {d["progress_pct"]}% ({d["sub_done"]}/{d["sub_total"]})']
            if d['sub_overdue']:
                parts.append(f'⚠ просрочено {d["sub_overdue"]} подэт.')
            if d['defects_open']:
                parts.append(f'{d["defects_open"]} замечаний')
            if d['pkgs_stalled']:
                parts.append(f'{d["pkgs_stalled"]} пакетов зависло')
            if d['period_completed_sum']:
                parts.append(f'выполнено за неделю: {_fmt_money(d["period_completed_sum"])}')

            body = ' · '.join(parts)
            link = f'/objects/{obj["id"]}/digest'

            for mgr in managers:
                try:
                    _notify(
                        mgr['id'],
                        'digest',
                        f'Еженедельная сводка: {obj["name"]}',
                        body,
                        link,
                    )
                    sent += 1
                except Exception as e:
                    print(f'  notify user {mgr["id"]} — ошибка: {e}')

        print(f'send_weekly_digests: отправлено {sent} уведомлений '
              f'({len(list(objects))} объектов, {len(list(managers))} получателей)')


def register(app):

    @app.route('/objects/<int:obj_id>/digest')
    @login_required
    @role_required(*DIGEST_ROLES)
    def object_digest_view(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        period = request.args.get('period', 'week')
        try:
            period_days = int(request.args.get('days', 7))
        except ValueError:
            period_days = 7

        if period == 'month':
            period_days = 30
        elif period == 'week':
            period_days = 7

        from digest import object_digest
        d = object_digest(obj_id, period_days=period_days)

        return render_template(
            'digest/view.html',
            obj=obj, d=d,
            period=period, period_days=period_days,
        )

    @app.route('/objects/<int:obj_id>/digest/pdf')
    @login_required
    @role_required(*DIGEST_ROLES)
    def object_digest_pdf(obj_id):
        obj = query_db('SELECT * FROM objects WHERE id = ?', (obj_id,), one=True)
        if not obj:
            abort(404)

        period = request.args.get('period', 'week')
        try:
            period_days = int(request.args.get('days', 7))
        except ValueError:
            period_days = 7
        if period == 'month':
            period_days = 30
        elif period == 'week':
            period_days = 7

        from digest import object_digest
        d = object_digest(obj_id, period_days=period_days)
        buf = _build_digest_pdf(obj, d)

        safe_name = obj['name'].replace('/', '-').replace('"', '')[:40]
        filename = f'Дайджест_{safe_name}_{date.today().isoformat()}.pdf'
        return send_file(buf, as_attachment=True,
                         download_name=filename,
                         mimetype='application/pdf')
