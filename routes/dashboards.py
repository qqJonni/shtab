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


def _tenant_obj_filter(user):
    """Returns (extra_join, extra_where, args) fragments for scoping by tenant."""
    if user.role == 'admin':
        return '', '', []
    if user.role == 'contractor':
        return '', '', []  # contractor dashboard already uses org_id filter
    if user.organization_id:
        return '', ' AND o.developer_id = ?', [user.organization_id]
    return '', ' AND 1=0', []


def _package_counts(role=None, user=None):
    from db import query_db
    from flask_login import current_user as cu
    u = user or cu

    tenant_where = ''
    tenant_args = []
    if u.role != 'admin' and u.role != 'contractor' and u.organization_id:
        tenant_where = (
            ' AND dp.substage_id IN ('
            '  SELECT ss.id FROM substages ss'
            '  JOIN construction_stages cs ON ss.stage_id = cs.id'
            '  JOIN objects o ON cs.object_id = o.id'
            '  WHERE o.developer_id = ?)'
        )
        tenant_args = [u.organization_id]

    if role:
        pending = query_db(
            f"SELECT COUNT(*) as c FROM approval_steps a "
            f"JOIN doc_packages dp ON a.package_id = dp.id "
            f"WHERE a.role = ? AND a.status = 'pending'{tenant_where}",
            [role] + tenant_args, one=True)['c']
    else:
        pending = 0
    return {
        'pending': pending,
        'total': query_db(f"SELECT COUNT(*) as c FROM doc_packages dp WHERE 1=1{tenant_where}", tenant_args, one=True)['c'],
        'in_review': query_db(f"SELECT COUNT(*) as c FROM doc_packages dp WHERE status='in_review'{tenant_where}", tenant_args, one=True)['c'],
        'returned': query_db(f"SELECT COUNT(*) as c FROM doc_packages dp WHERE status='returned'{tenant_where}", tenant_args, one=True)['c'],
        'completed': query_db(f"SELECT COUNT(*) as c FROM doc_packages dp WHERE status='completed'{tenant_where}", tenant_args, one=True)['c'],
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
        if current_user.role != 'admin':
            abort(403)
        from reports import summary_cards, chart_substage_statuses, chart_schedule_health, \
            chart_defects_priority, chart_packages_pipeline, objects_summary
        sc = summary_cards()
        cs = chart_substage_statuses()
        sh = chart_schedule_health()
        dp = chart_defects_priority()
        pp = chart_packages_pipeline()
        objs = objects_summary()
        pc = _package_counts()
        import config
        return render_template('dashboards/manager.html',
                               role_label=config.ROLES.get('admin'),
                               sc=sc, cs=cs, sh=sh, dp=dp, pp=pp, pc=pc, objs=objs)

    @app.route('/dashboard/manager')
    @login_required
    def dashboard_manager():
        if current_user.role != 'manager' and current_user.role != 'admin':
            abort(403)
        from reports import summary_cards, chart_substage_statuses, chart_schedule_health, \
            chart_defects_priority, chart_packages_pipeline, objects_summary
        dev_id = current_user.organization_id if current_user.role == 'manager' else None
        sc = summary_cards(dev_id)
        cs = chart_substage_statuses(dev_id)
        sh = chart_schedule_health(dev_id)
        dp = chart_defects_priority(dev_id)
        pp = chart_packages_pipeline(dev_id)
        objs = objects_summary(dev_id)
        pc = _package_counts('manager', current_user)
        import config
        return render_template('dashboards/manager.html',
                               role_label=config.ROLES.get('manager'),
                               sc=sc, cs=cs, sh=sh, dp=dp, pp=pp, pc=pc, objs=objs)

    @app.route('/dashboard/pto')
    @login_required
    def dashboard_pto():
        if current_user.role != 'pto' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        _tj, _tw, _ta = _tenant_obj_filter(current_user)
        stages = query_db(
            'SELECT cs.id, cs.name, cs.status, o.name as object_name, o.id as object_id '
            'FROM construction_stages cs JOIN objects o ON cs.object_id = o.id '
            f"WHERE o.status = 'active'{_tw} ORDER BY o.name, cs.order_num",
            _ta,
        )
        stages_list = [dict(s) for s in stages]
        stages_no_subs = 0
        total_substages = 0
        substages_in_progress = 0
        substages_done = 0
        substages_not_started = 0
        substages_overdue = 0
        from datetime import date
        today = date.today().isoformat()
        for s in stages_list:
            subs = query_db('SELECT status, plan_end_date FROM substages WHERE stage_id = ?', (s['id'],))
            cnt = len(subs)
            s['sub_total'] = cnt
            s['sub_done'] = sum(1 for sub in subs if sub['status'] == 'done')
            s['sub_in_progress'] = sum(1 for sub in subs if sub['status'] == 'in_progress')
            s['sub_not_started'] = sum(1 for sub in subs if sub['status'] == 'not_started')
            s['sub_overdue'] = sum(1 for sub in subs if sub['plan_end_date'] and sub['plan_end_date'] < today and sub['status'] not in ('done', 'closed', 'approved'))
            if cnt == 0:
                stages_no_subs += 1
            total_substages += cnt
            substages_in_progress += s['sub_in_progress']
            substages_done += s['sub_done']
            substages_not_started += s['sub_not_started']
            substages_overdue += s['sub_overdue']
        pc = _package_counts('pto', current_user)
        mr_pending = query_db(
            "SELECT COUNT(*) as c FROM material_requests mr "
            "JOIN construction_stages cs2 ON mr.stage_id = cs2.id "
            "JOIN objects o ON cs2.object_id = o.id "
            f"WHERE mr.status='submitted' AND mr.route_role='pto'{_tw}",
            _ta, one=True)['c']
        import config
        return render_template('dashboards/pto.html',
                               stages=stages_list, stages_no_subs=stages_no_subs,
                               total_substages=total_substages,
                               substages_in_progress=substages_in_progress,
                               substages_done=substages_done,
                               substages_not_started=substages_not_started,
                               substages_overdue=substages_overdue,
                               role_label=config.ROLES.get('pto'), pc=pc,
                               mr_pending=mr_pending)

    @app.route('/dashboard/inspector')
    @login_required
    def dashboard_inspector():
        if current_user.role != 'inspector' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        _tj, _tw, _ta = _tenant_obj_filter(current_user)
        _dq = (
            "FROM defects d JOIN objects o ON d.object_id = o.id WHERE 1=1"
            + _tw
        )
        open_cnt = query_db(f"SELECT COUNT(*) as c {_dq} AND d.status='open'", _ta, one=True)['c']
        resolved_cnt = query_db(f"SELECT COUNT(*) as c {_dq} AND d.status='resolved'", _ta, one=True)['c']
        my_created = query_db(f"SELECT COUNT(*) as c {_dq} AND d.reporter_id=?",
                              _ta + [current_user.id], one=True)['c']
        overdue = query_db(
            f"SELECT COUNT(*) as c {_dq} AND d.due_date < to_char(now(),'YYYY-MM-DD') AND d.status NOT IN ('closed','verified')",
            _ta, one=True)['c']
        pc = _package_counts('inspector', current_user)
        import config
        return render_template('dashboards/inspector.html',
                               role_label=config.ROLES.get('inspector'),
                               open_cnt=open_cnt, resolved_cnt=resolved_cnt,
                               my_created=my_created, overdue=overdue, pc=pc)

    @app.route('/dashboard/foreman')
    @login_required
    def dashboard_foreman():
        if current_user.role != 'foreman' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        _tj, _tw, _ta = _tenant_obj_filter(current_user)
        _dq = "FROM defects d JOIN objects o ON d.object_id = o.id WHERE 1=1" + _tw
        open_cnt = query_db(f"SELECT COUNT(*) as c {_dq} AND d.status='open'", _ta, one=True)['c']
        my_created = query_db(f"SELECT COUNT(*) as c {_dq} AND d.reporter_id=?",
                              _ta + [current_user.id], one=True)['c']
        in_progress = query_db(f"SELECT COUNT(*) as c {_dq} AND d.status='in_progress'", _ta, one=True)['c']
        pc = _package_counts('foreman', current_user)
        import config
        return render_template('dashboards/foreman.html',
                               role_label=config.ROLES.get('foreman'),
                               open_cnt=open_cnt, my_created=my_created,
                               in_progress=in_progress, pc=pc)

    @app.route('/dashboard/supply')
    @login_required
    def dashboard_supply():
        if current_user.role != 'supply' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        _tj, _tw, _ta = _tenant_obj_filter(current_user)
        _mrq = (
            'FROM material_requests mr '
            'JOIN construction_stages cs ON mr.stage_id = cs.id '
            'JOIN objects o ON cs.object_id = o.id '
            'WHERE 1=1' + _tw
        )
        approved = query_db(f"SELECT COUNT(*) as c {_mrq} AND mr.status='approved'", _ta, one=True)['c']
        processing = query_db(f"SELECT COUNT(*) as c {_mrq} AND mr.status='processing'", _ta, one=True)['c']
        completed = query_db(f"SELECT COUNT(*) as c {_mrq} AND mr.status='completed'", _ta, one=True)['c']
        import config
        return render_template('dashboards/supply.html',
                               role_label=config.ROLES.get('supply'),
                               approved=approved, processing=processing, completed=completed)

    @app.route('/dashboard/accountant')
    @login_required
    def dashboard_accountant():
        if current_user.role != 'accountant' and current_user.role != 'admin':
            abort(403)
        from db import query_db
        _tj, _tw, _ta = _tenant_obj_filter(current_user)
        pc = _package_counts('accountant', current_user)
        completed = query_db(
            "SELECT dp.*, ss.name as substage_name, cs.name as stage_name, "
            "o.name as object_name, org.name as contractor_name "
            "FROM doc_packages dp "
            "JOIN substages ss ON dp.substage_id = ss.id "
            "JOIN construction_stages cs ON ss.stage_id = cs.id "
            "JOIN objects o ON cs.object_id = o.id "
            "LEFT JOIN organizations org ON dp.contractor_id = org.id "
            f"WHERE dp.status = 'completed'{_tw} ORDER BY dp.completed_at DESC LIMIT 10",
            _ta)
        import config
        return render_template('dashboards/accountant.html',
                               role_label=config.ROLES.get('accountant'),
                               pc=pc, completed=completed)

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
        from datetime import date
        today = date.today().isoformat()
        substages_overdue = 0
        for s in stages_list:
            subs = query_db('SELECT status, plan_end_date FROM substages WHERE stage_id = ?', (s['id'],))
            s['sub_total'] = len(subs)
            s['sub_done'] = sum(1 for sub in subs if sub['status'] == 'done')
            s_overdue = sum(1 for sub in subs if sub['plan_end_date'] and sub['plan_end_date'] < today and sub['status'] not in ('done', 'closed', 'approved'))
            substages_overdue += s_overdue
        # Defect counts for contractor
        org_id = current_user.organization_id
        defect_open = query_db("SELECT COUNT(*) as c FROM defects WHERE contractor_id=? AND status IN ('open','rejected')",
                               (org_id,), one=True)['c']
        defect_in_progress = query_db("SELECT COUNT(*) as c FROM defects WHERE contractor_id=? AND status='in_progress'",
                                      (org_id,), one=True)['c']
        defect_overdue = query_db(
            "SELECT COUNT(*) as c FROM defects WHERE contractor_id=? AND due_date < to_char(now(),'YYYY-MM-DD') AND status NOT IN ('closed','verified')",
            (org_id,), one=True)['c']
        pkg_in_review = query_db("SELECT COUNT(*) as c FROM doc_packages WHERE contractor_id=? AND status='in_review'",
                                  (org_id,), one=True)['c']
        pkg_returned = query_db("SELECT COUNT(*) as c FROM doc_packages WHERE contractor_id=? AND status='returned'",
                                (org_id,), one=True)['c']
        pkg_completed = query_db("SELECT COUNT(*) as c FROM doc_packages WHERE contractor_id=? AND status='completed'",
                                 (org_id,), one=True)['c']
        mr_active = query_db("SELECT COUNT(*) as c FROM material_requests WHERE contractor_id=? AND status IN ('submitted','approved','processing')",
                             (org_id,), one=True)['c']
        mr_returned = query_db("SELECT COUNT(*) as c FROM material_requests WHERE contractor_id=? AND status='returned'",
                               (org_id,), one=True)['c']
        import config
        return render_template('dashboards/contractor.html',
                               stages=stages_list, role_label=config.ROLES.get('contractor'),
                               defect_open=defect_open, defect_in_progress=defect_in_progress,
                               defect_overdue=defect_overdue,
                               pkg_in_review=pkg_in_review, pkg_returned=pkg_returned,
                               pkg_completed=pkg_completed,
                               mr_active=mr_active, mr_returned=mr_returned,
                               substages_overdue=substages_overdue)

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
