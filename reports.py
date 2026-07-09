from db import query_db


def _ow(developer_id):
    """Returns (obj_where, stage_where, sub_where, defect_where, pkg_where, mr_where) SQL fragments
    and the args list to scope queries by developer_id tenant.
    All fragments start with AND so they can be appended after an existing WHERE clause.
    """
    if not developer_id:
        return '', '', '', '', '', '', []
    a = [developer_id]
    o = ' AND developer_id = ?'
    s = ' AND object_id IN (SELECT id FROM objects WHERE developer_id = ?)'
    ss = (' AND stage_id IN (SELECT cs.id FROM construction_stages cs '
          'WHERE cs.object_id IN (SELECT id FROM objects WHERE developer_id = ?))')
    d = (' AND object_id IN (SELECT id FROM objects WHERE developer_id = ?)')
    p = (' AND substage_id IN (SELECT ss.id FROM substages ss '
         'JOIN construction_stages cs ON ss.stage_id=cs.id '
         'WHERE cs.object_id IN (SELECT id FROM objects WHERE developer_id = ?))')
    mr = (' AND stage_id IN (SELECT id FROM construction_stages '
          'WHERE object_id IN (SELECT id FROM objects WHERE developer_id = ?))')
    return o, s, ss, d, p, mr, a


def summary_cards(developer_id=None):
    ow, sw, ssw, dw, pw, mrw, a = _ow(developer_id)
    return {
        'objects_active': query_db(f"SELECT COUNT(*) as c FROM objects WHERE status='active'{ow}", a, one=True)['c'],
        'objects_archived': query_db(f"SELECT COUNT(*) as c FROM objects WHERE status='archived'{ow}", a, one=True)['c'],
        'stages_total': query_db(f"SELECT COUNT(*) as c FROM construction_stages WHERE 1=1{sw}", a, one=True)['c'],
        'stages_in_progress': query_db(f"SELECT COUNT(*) as c FROM construction_stages WHERE status='in_progress'{sw}", a, one=True)['c'],
        'stages_done': query_db(f"SELECT COUNT(*) as c FROM construction_stages WHERE status='done'{sw}", a, one=True)['c'],
        'substages_total': query_db(f"SELECT COUNT(*) as c FROM substages WHERE 1=1{ssw}", a, one=True)['c'],
        'substages_not_started': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='not_started'{ssw}", a, one=True)['c'],
        'substages_in_progress': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='in_progress'{ssw}", a, one=True)['c'],
        'substages_done': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='done'{ssw}", a, one=True)['c'],
        'substages_closed': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='closed'{ssw}", a, one=True)['c'],
        'substages_approved': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='approved'{ssw}", a, one=True)['c'],
        'defects_open': query_db(f"SELECT COUNT(*) as c FROM defects WHERE status IN ('open','in_progress'){dw}", a, one=True)['c'],
        'defects_overdue': query_db(f"SELECT COUNT(*) as c FROM defects WHERE due_date < to_char(now(),'YYYY-MM-DD') AND status NOT IN ('closed','verified'){dw}", a, one=True)['c'],
        'defects_closed': query_db(f"SELECT COUNT(*) as c FROM defects WHERE status IN ('closed','verified'){dw}", a, one=True)['c'],
        'substages_overdue': query_db(f"SELECT COUNT(*) as c FROM substages WHERE plan_end_date < to_char(now(),'YYYY-MM-DD') AND status NOT IN ('done','closed','approved'){ssw}", a, one=True)['c'],
        'packages_in_review': query_db(f"SELECT COUNT(*) as c FROM doc_packages WHERE status='in_review'{pw}", a, one=True)['c'],
        'packages_completed': query_db(f"SELECT COUNT(*) as c FROM doc_packages WHERE status='completed'{pw}", a, one=True)['c'],
        'mr_active': query_db(f"SELECT COUNT(*) as c FROM material_requests WHERE status IN ('submitted','approved','processing'){mrw}", a, one=True)['c'],
    }


def chart_substage_statuses(developer_id=None):
    _, _, ssw, _, _, _, a = _ow(developer_id)
    return {
        'not_started': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='not_started'{ssw}", a, one=True)['c'],
        'in_progress': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='in_progress'{ssw}", a, one=True)['c'],
        'done': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='done'{ssw}", a, one=True)['c'],
        'closed': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='closed'{ssw}", a, one=True)['c'],
        'approved': query_db(f"SELECT COUNT(*) as c FROM substages WHERE status='approved'{ssw}", a, one=True)['c'],
    }


def chart_schedule_health(developer_id=None):
    from datetime import date
    _, sw, _, _, _, _, a = _ow(developer_id)
    today = date.today().isoformat()
    done = query_db(f"SELECT COUNT(*) as c FROM construction_stages WHERE status='done'{sw}", a, one=True)['c']
    on_track = query_db(
        f"SELECT COUNT(*) as c FROM construction_stages WHERE status IN ('planned','in_progress') "
        f"AND (plan_end_date IS NULL OR plan_end_date >= ?){sw}", [today] + a, one=True)['c']
    overdue = query_db(
        f"SELECT COUNT(*) as c FROM construction_stages WHERE status IN ('planned','in_progress') "
        f"AND plan_end_date IS NOT NULL AND plan_end_date < ?{sw}", [today] + a, one=True)['c']
    suspended = query_db(f"SELECT COUNT(*) as c FROM construction_stages WHERE status='suspended'{sw}", a, one=True)['c']
    return {'done': done, 'on_track': on_track, 'overdue': overdue, 'suspended': suspended}


def chart_defects_priority(developer_id=None):
    _, _, _, dw, _, _, a = _ow(developer_id)
    rows = query_db(
        f"SELECT priority, COUNT(*) as c FROM defects WHERE status NOT IN ('closed','verified'){dw} GROUP BY priority",
        a)
    return {r['priority']: r['c'] for r in rows}


def chart_packages_pipeline(developer_id=None):
    _, _, _, _, pw, _, a = _ow(developer_id)
    rows = query_db(
        f"SELECT a.role, COUNT(*) as c FROM approval_steps a "
        f"JOIN doc_packages dp ON a.package_id = dp.id "
        f"WHERE dp.status = 'in_review' AND a.status = 'pending'{pw.replace('substage_id', 'dp.substage_id')} GROUP BY a.role",
        a)
    return {r['role']: r['c'] for r in rows}


def objects_summary(developer_id=None):
    ow, _, _, _, _, _, a = _ow(developer_id)
    objects = query_db(
        f"SELECT o.*, "
        "(SELECT COUNT(*) FROM construction_stages cs2 WHERE cs2.object_id=o.id) as stages_count, "
        "(SELECT COUNT(*) FROM defects d2 WHERE d2.object_id=o.id AND d2.status NOT IN ('closed','verified')) as defects_open "
        f"FROM objects o WHERE o.status='active'{ow} ORDER BY o.name",
        a)
    result = []
    for obj in objects:
        o = dict(obj)
        subs = query_db(
            "SELECT ss.status, ss.total_price FROM substages ss "
            "JOIN construction_stages cs ON ss.stage_id=cs.id WHERE cs.object_id=?", (o['id'],))
        total = len(subs)
        done = sum(1 for s in subs if s['status'] in ('done', 'closed', 'approved'))
        o['substages_total'] = total
        o['substages_done'] = done
        o['progress'] = round(done / total * 100) if total > 0 else 0
        o['total_sum'] = sum(s['total_price'] or 0 for s in subs)
        o['completed_sum'] = sum(s['total_price'] or 0 for s in subs if s['status'] in ('done', 'closed', 'approved'))
        pkgs = query_db(
            "SELECT COUNT(*) as c FROM doc_packages dp "
            "JOIN substages ss ON dp.substage_id=ss.id "
            "JOIN construction_stages cs ON ss.stage_id=cs.id "
            "WHERE cs.object_id=? AND dp.status IN ('in_review','returned')", (o['id'],))
        o['packages_active'] = pkgs[0]['c'] if pkgs else 0
        result.append(o)
    return result
