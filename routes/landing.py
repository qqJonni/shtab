"""Публичный лендинг /preview — витрина продукта, без авторизации и без данных."""
import os
import re
import time

from flask import render_template, request, redirect, url_for, flash

from db import query_db, execute_db, get_db, notify, _send_email

# Простой rate-limit по IP (в памяти процесса): не чаще 1 заявки в 60 сек.
_LAST_LEAD = {}
_RATE_SECONDS = 60

# Куда дублировать заявки письмом (переопределяется через env LEAD_EMAIL)
LEAD_EMAIL = os.environ.get('LEAD_EMAIL', 'tepliy_shov@mail.ru')

CONTACT = {
    'name': 'Львов Валерий Вадимович',
    'phone_display': '+7 982 435-72-07',
    'phone_tel': '+79824357207',
    'email': 'tepliy_shov@mail.ru',
    'site': 'shtab-crm.ru',
}


def register(app):

    @app.route('/preview')
    def landing_preview():
        # Публичный маршрут: нет @login_required, ничего не тянет из БД.
        import os
        pdf_path = os.path.join(app.static_folder, 'ШТАБ_Презентация.pdf')
        return render_template('landing/preview.html',
                               contact=CONTACT,
                               has_pdf=os.path.exists(pdf_path))

    @app.route('/preview/lead', methods=['POST'])
    def landing_lead():
        # honeypot: скрытое поле, которое боты заполняют
        if request.form.get('website', '').strip():
            flash('Спасибо! Мы свяжемся с вами.', 'success')
            return redirect(url_for('landing_preview') + '#contact')

        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
        now = time.time()
        last = _LAST_LEAD.get(ip, 0)
        if now - last < _RATE_SECONDS:
            flash('Заявка уже отправлена. Мы скоро свяжемся с вами.', 'warning')
            return redirect(url_for('landing_preview') + '#contact')

        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        company = request.form.get('company', '').strip()
        comment = request.form.get('comment', '').strip()

        if not name or not phone:
            flash('Укажите имя и телефон.', 'danger')
            return redirect(url_for('landing_preview') + '#contact')
        digits = re.sub(r'\D', '', phone)
        if len(digits) < 10:
            flash('Проверьте номер телефона.', 'danger')
            return redirect(url_for('landing_preview') + '#contact')

        ua = request.headers.get('User-Agent', '')[:300]
        execute_db(
            'INSERT INTO landing_leads (name, phone, company, comment, ip, user_agent) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (name, phone, company or None, comment or None, ip or None, ua))
        _LAST_LEAD[ip] = now

        # уведомить admin/manager (в приложении; email/push — если настроены)
        body = (f'{name}, тел. {phone}' + (f', {company}' if company else '') +
                (f'. {comment}' if comment else ''))
        recipients = query_db("SELECT id FROM users WHERE role IN ('admin', 'manager') AND is_approved = 1")
        for u in recipients:
            notify(u['id'], 'user', f'Заявка с лендинга: {name}', body, '/admin/leads')

        # письмо с заявкой на почту владельца (если настроен SMTP; иначе тихо пропускается)
        mail_body = (
            'Новая заявка с лендинга shtab-crm.ru/preview\n\n'
            f'Имя:        {name}\n'
            f'Телефон:    {phone}\n'
            f'Компания:   {company or "—"}\n'
            f'Комментарий: {comment or "—"}\n\n'
            f'IP: {ip or "—"}'
        )
        _send_email(LEAD_EMAIL, f'ШТАБ · заявка с лендинга: {name}', mail_body)

        flash('Спасибо! Заявка принята — свяжемся с вами в ближайшее время.', 'success')
        return redirect(url_for('landing_preview') + '#contact')

    @app.route('/admin/leads')
    def landing_leads_list():
        from flask_login import current_user
        from flask import abort
        if not current_user.is_authenticated or current_user.role not in ('admin', 'manager'):
            abort(403)
        leads = query_db('SELECT * FROM landing_leads ORDER BY id DESC')
        return render_template('landing/leads.html', leads=leads)
