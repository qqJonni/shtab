from flask import render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

import config
from db import query_db, execute_db, notify
from helpers import save_avatar


def register(app):

    @app.route('/', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            row = query_db('SELECT * FROM users WHERE username = ?', (username,), one=True)

            if row and check_password_hash(row['password_hash'], password):
                if not row['is_approved']:
                    flash('Ваш аккаунт ожидает подтверждения администратором.', 'warning')
                    return redirect(url_for('login'))
                from app import load_user_obj
                user = load_user_obj(row)
                login_user(user)
                return redirect(request.args.get('next') or url_for('dashboard'))

            flash('Неверный логин или пароль.', 'danger')

        return render_template('auth/login.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register_view():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username  = request.form.get('username', '').strip()
            password  = request.form.get('password', '')
            full_name = request.form.get('full_name', '').strip()
            email     = request.form.get('email', '').strip() or None
            join_code = request.form.get('join_code', '').strip().upper()

            if not username or not password or not full_name or not join_code:
                flash('Заполните все обязательные поля.', 'danger')
                return render_template('auth/register.html')

            org = query_db('SELECT id, name FROM organizations WHERE join_code = ? AND status = ?',
                           (join_code, 'active'), one=True)
            if not org:
                flash('Код организации не найден или организация неактивна. Уточните у администратора.', 'danger')
                return render_template('auth/register.html')

            if query_db('SELECT id FROM users WHERE username = ?', (username,), one=True):
                flash('Пользователь с таким логином уже существует.', 'danger')
                return render_template('auth/register.html')

            execute_db(
                'INSERT INTO users (username, password_hash, role, full_name, is_approved, organization_id, email) '
                'VALUES (?, ?, ?, ?, 0, ?, ?)',
                (username, generate_password_hash(password), 'pending', full_name, org['id'], email),
            )

            # Уведомить тех, кто может подтвердить заявку:
            # админы + руководители тенанта (для подрядной орг — руководители
            # тенанта-создателя, для developer-орг — её собственные)
            org_full = query_db('SELECT type, created_by_org FROM organizations WHERE id = ?',
                                (org['id'],), one=True)
            if org_full and org_full['type'] == 'contractor' and org_full['created_by_org']:
                manager_org_id = org_full['created_by_org']
            else:
                manager_org_id = org['id']
            approvers = query_db(
                "SELECT id FROM users WHERE is_approved = 1 AND (role = 'admin' "
                "OR (role = 'manager' AND organization_id = ?))",
                (manager_org_id,))
            for u in approvers:
                notify(u['id'], 'user',
                       f'Новая заявка на регистрацию: {full_name}',
                       f'{full_name} ({username}) зарегистрировался в организации «{org["name"]}» '
                       'и ожидает подтверждения и назначения роли.',
                       '/admin/users')

            flash(
                f'Заявка отправлена. Вы привязаны к организации «{org["name"]}». '
                'Дождитесь подтверждения и назначения роли администратором.',
                'success'
            )
            return redirect(url_for('login'))

        return render_template('auth/register.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Вы вышли из системы.', 'info')
        return redirect(url_for('login'))

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        if request.method == 'POST':
            new_username = request.form.get('username', '').strip()
            new_name = request.form.get('full_name', '').strip()
            new_password = request.form.get('new_password', '')
            avatar_file = request.files.get('avatar')

            if new_username and new_username != current_user.username:
                existing = query_db('SELECT id FROM users WHERE username = ? AND id != ?',
                                    (new_username, current_user.id), one=True)
                if existing:
                    flash('Логин занят.', 'danger')
                    return redirect(url_for('profile'))
                execute_db('UPDATE users SET username = ? WHERE id = ?', (new_username, current_user.id))

            if new_name:
                execute_db('UPDATE users SET full_name = ? WHERE id = ?', (new_name, current_user.id))

            if new_password:
                execute_db('UPDATE users SET password_hash = ? WHERE id = ?',
                           (generate_password_hash(new_password), current_user.id))

            if avatar_file and avatar_file.filename:
                filename = save_avatar(avatar_file)
                if filename:
                    execute_db('UPDATE users SET avatar = ? WHERE id = ?', (filename, current_user.id))

            flash('Профиль обновлён.', 'success')
            return redirect(url_for('profile'))

        return render_template('auth/profile.html')
