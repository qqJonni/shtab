from flask import render_template, redirect, url_for, abort
from flask_login import login_required, current_user

ROLE_DASHBOARDS = {
    'admin':      'dashboard_admin',
    'manager':    'dashboard_manager',
    'pto':        'dashboard_pto',
    'inspector':  'dashboard_inspector',
    'foreman':    'dashboard_foreman',
    'supply':     'dashboard_supply',
    'accountant': 'dashboard_accountant',
    'contractor': 'dashboard_contractor',
    'guest':      'dashboard_guest',
}

ROLE_SECTIONS = {
    'admin': [
        {'icon': 'bi-people',    'title': 'Пользователи',  'text': 'Управление учётными записями и ролями'},
        {'icon': 'bi-buildings', 'title': 'Организации',   'text': 'Заказчики и подрядчики'},
        {'icon': 'bi-gear',      'title': 'Настройки',     'text': 'Параметры системы'},
    ],
    'manager': [
        {'icon': 'bi-building',         'title': 'Объекты',        'text': 'Создание объектов и этапов строительства'},
        {'icon': 'bi-check2-square',    'title': 'Согласования',   'text': 'Маршрут согласования пакетов документов'},
        {'icon': 'bi-exclamation-triangle', 'title': 'Замечания',  'text': 'Контроль устранения замечаний'},
        {'icon': 'bi-bar-chart-line',   'title': 'Отчёты',        'text': 'Сводные дашборды и аналитика'},
        {'icon': 'bi-file-earmark-excel','title': 'Экспорт',      'text': 'Выгрузка данных в Excel'},
        {'icon': 'bi-link-45deg',       'title': 'Гостевые ссылки','text': 'Доступ для третьих лиц по ссылке'},
    ],
    'pto': [
        {'icon': 'bi-building',      'title': 'Объекты',             'text': 'Просмотр объектов и этапов'},
        {'icon': 'bi-list-check',    'title': 'Подэтапы',            'text': 'Формирование подэтапов по ценовому документу'},
        {'icon': 'bi-check2-square', 'title': 'Согласования',        'text': 'Проверка и согласование пакетов'},
        {'icon': 'bi-box-seam',      'title': 'Заявки на материал',  'text': 'Проверка заявок на давальческий материал'},
        {'icon': 'bi-exclamation-triangle', 'title': 'Замечания',   'text': 'Просмотр и контроль замечаний'},
    ],
    'inspector': [
        {'icon': 'bi-building',             'title': 'Объекты',      'text': 'Проверка хода работ на объектах'},
        {'icon': 'bi-exclamation-triangle',  'title': 'Замечания',   'text': 'Выдача и проверка замечаний подрядчикам'},
        {'icon': 'bi-check2-square',        'title': 'Согласования', 'text': 'Первичное согласование пакетов документов'},
    ],
    'foreman': [
        {'icon': 'bi-building',             'title': 'Объекты',      'text': 'Контроль работ на площадке'},
        {'icon': 'bi-exclamation-triangle',  'title': 'Замечания',   'text': 'Выдача замечаний подрядчикам'},
        {'icon': 'bi-check2-square',        'title': 'Согласования', 'text': 'Согласование пакетов (2-й шаг)'},
    ],
    'supply': [
        {'icon': 'bi-box-seam', 'title': 'Заявки на материал', 'text': 'Обработка заявок на давальческий материал'},
    ],
    'accountant': [
        {'icon': 'bi-file-earmark-text', 'title': 'Документы на оплату', 'text': 'Согласованные пакеты КС-2/КС-3 и счета'},
    ],
    'contractor': [
        {'icon': 'bi-kanban',               'title': 'Мои этапы',           'text': 'Назначенные этапы и подэтапы'},
        {'icon': 'bi-file-earmark-check',    'title': 'Закрытие работ',     'text': 'Формирование пакетов документов и отправка на согласование'},
        {'icon': 'bi-exclamation-triangle',  'title': 'Замечания',          'text': 'Замечания от технадзора и прораба'},
        {'icon': 'bi-box-seam',             'title': 'Заявки на материал',  'text': 'Заявки на давальческий материал'},
    ],
    'guest': [
        {'icon': 'bi-eye', 'title': 'Просмотр объекта', 'text': 'Информация по объекту доступна по гостевой ссылке'},
    ],
}


def register(app):

    @app.route('/dashboard')
    @login_required
    def dashboard():
        endpoint = ROLE_DASHBOARDS.get(current_user.role, 'dashboard_guest')
        return redirect(url_for(endpoint))

    @app.route('/dashboard/admin')
    @login_required
    def dashboard_admin():
        return _render('admin')

    @app.route('/dashboard/manager')
    @login_required
    def dashboard_manager():
        return _render('manager')

    @app.route('/dashboard/pto')
    @login_required
    def dashboard_pto():
        if current_user.role != 'pto' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        stages = query_db(
            'SELECT cs.id, cs.name, cs.status, o.name as object_name, o.id as object_id '
            'FROM construction_stages cs JOIN objects o ON cs.object_id = o.id '
            "WHERE o.status = 'active' ORDER BY o.name, cs.order_num"
        )
        stages_list = [dict(s) for s in stages]
        stages_no_subs = 0
        total_substages = 0
        substages_in_progress = 0
        substages_done = 0
        for s in stages_list:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            cnt = len(subs)
            s['sub_total'] = cnt
            s['sub_done'] = sum(1 for sub in subs if sub['status'] == 'done')
            s['sub_in_progress'] = sum(1 for sub in subs if sub['status'] == 'in_progress')
            if cnt == 0:
                stages_no_subs += 1
            total_substages += cnt
            substages_in_progress += s['sub_in_progress']
            substages_done += s['sub_done']
        import config
        return render_template('dashboards/pto.html',
                               stages=stages_list, stages_no_subs=stages_no_subs,
                               total_substages=total_substages,
                               substages_in_progress=substages_in_progress,
                               substages_done=substages_done,
                               role_label=config.ROLES.get('pto'))

    @app.route('/dashboard/inspector')
    @login_required
    def dashboard_inspector():
        return _render('inspector')

    @app.route('/dashboard/foreman')
    @login_required
    def dashboard_foreman():
        return _render('foreman')

    @app.route('/dashboard/supply')
    @login_required
    def dashboard_supply():
        return _render('supply')

    @app.route('/dashboard/accountant')
    @login_required
    def dashboard_accountant():
        return _render('accountant')

    @app.route('/dashboard/contractor')
    @login_required
    def dashboard_contractor():
        if current_user.role != 'contractor' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        stages = query_db(
            'SELECT cs.*, o.name as object_name '
            'FROM construction_stages cs '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE cs.contractor_id = ? '
            'ORDER BY cs.status, o.name, cs.order_num',
            (current_user.organization_id,),
        )
        stages_list = [dict(s) for s in stages]
        for s in stages_list:
            subs = query_db('SELECT status FROM substages WHERE stage_id = ?', (s['id'],))
            s['sub_total'] = len(subs)
            s['sub_done'] = sum(1 for sub in subs if sub['status'] == 'done')
        import config
        return render_template('dashboards/contractor.html',
                               stages=stages_list, role_label=config.ROLES.get('contractor'))

    @app.route('/dashboard/guest')
    @login_required
    def dashboard_guest():
        return _render('guest')


def _render(role):
    if current_user.role != role and current_user.role != 'admin':
        abort(403)
    import config
    sections = ROLE_SECTIONS.get(role, [])
    role_label = config.ROLES.get(role, role)
    return render_template('dashboards/role.html', sections=sections, role_label=role_label)
