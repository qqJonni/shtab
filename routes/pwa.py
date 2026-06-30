from flask import send_from_directory, make_response, request, jsonify
from flask_login import current_user, login_required
import os, config


def register(app):

    @app.route('/manifest.webmanifest')
    def pwa_manifest():
        resp = make_response(
            send_from_directory(os.path.join(config.BASE_DIR, 'static'),
                                'manifest.webmanifest'))
        resp.headers['Content-Type'] = 'application/manifest+json'
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp

    @app.route('/sw.js')
    def pwa_sw():
        resp = make_response(
            send_from_directory(os.path.join(config.BASE_DIR, 'static', 'js'),
                                'sw.js'))
        resp.headers['Content-Type'] = 'application/javascript'
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    @app.route('/offline.html')
    def pwa_offline():
        resp = make_response(
            send_from_directory(os.path.join(config.BASE_DIR, 'static'),
                                'offline.html'))
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.route('/api/push/vapid-public-key')
    def push_vapid_key():
        key = os.environ.get('VAPID_PUBLIC_KEY', '')
        return jsonify({'publicKey': key})

    @app.route('/api/push/subscribe', methods=['POST'])
    @login_required
    def push_subscribe():
        from db import get_db
        data = request.get_json(silent=True) or {}
        endpoint = data.get('endpoint', '')
        p256dh = (data.get('keys') or {}).get('p256dh', '')
        auth = (data.get('keys') or {}).get('auth', '')
        if not (endpoint and p256dh and auth):
            return jsonify({'error': 'invalid subscription'}), 400
        db = get_db()
        db.execute(
            '''INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (user_id, endpoint) DO UPDATE
               SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth''',
            (current_user.id, endpoint, p256dh, auth),
        )
        db.commit()
        return jsonify({'ok': True})

    @app.route('/api/push/unsubscribe', methods=['POST'])
    @login_required
    def push_unsubscribe():
        from db import get_db
        data = request.get_json(silent=True) or {}
        endpoint = data.get('endpoint', '')
        if endpoint:
            db = get_db()
            db.execute(
                'DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?',
                (current_user.id, endpoint),
            )
            db.commit()
        return jsonify({'ok': True})
