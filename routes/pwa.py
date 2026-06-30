from flask import send_from_directory, make_response
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
