from flask import render_template, redirect, request, url_for, flash, jsonify
from flask_login import login_required, current_user

from db import query_db, execute_db


def register(app):

    @app.route('/notifications')
    @login_required
    def notifications_list():
        items = query_db(
            'SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC',
            (current_user.id,),
        )
        return render_template('notifications.html', notifications=items)

    @app.route('/notifications/<int:nid>/read')
    @login_required
    def notification_read(nid):
        row = query_db('SELECT * FROM notifications WHERE id = ? AND user_id = ?',
                        (nid, current_user.id), one=True)
        if row:
            execute_db('UPDATE notifications SET is_read = 1 WHERE id = ?', (nid,))
            if row['link']:
                return redirect(row['link'])
        return redirect(url_for('notifications_list'))

    @app.route('/notifications/read-all', methods=['POST'])
    @login_required
    def notifications_read_all():
        execute_db('UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0',
                   (current_user.id,))
        flash('Все уведомления отмечены как прочитанные.', 'success')
        return redirect(url_for('notifications_list'))

    @app.route('/api/notifications/poll')
    @login_required
    def notifications_poll():
        row = query_db(
            'SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0',
            (current_user.id,), one=True)
        count = row['cnt'] if row else 0
        latest = None
        if count > 0:
            last = query_db(
                'SELECT title, type FROM notifications WHERE user_id = ? AND is_read = 0 '
                'ORDER BY created_at DESC LIMIT 1',
                (current_user.id,), one=True)
            if last:
                latest = {'title': last['title'], 'type': last['type']}
        return jsonify({'count': count, 'latest': latest})
